import importlib
from datetime import date

import pytest
from fastapi.testclient import TestClient


def _pdf_str(t: str) -> str:
    return "(" + t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ")"


def pdf_con_texto(lineas: list) -> bytes:
    """Genera un PDF mínimo. Cada renglón es un texto o una lista de (x, texto) para armar columnas."""
    ops = []
    for i, l in enumerate(lineas):
        celdas = [(50, l)] if isinstance(l, str) else l
        for x, t in celdas:
            ops.append(f"BT /F1 11 Tf {x} {800 - 16 * i} Td {_pdf_str(t)} Tj ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


FACTURA = pdf_con_texto([
    "Razon Social: Ferreteria El Tornillo SRL",
    "CUIT: 30-71234567-8",
    "FACTURA A  N. 0003-00012345",
    "Fecha de Emision: 15/09/2026",
    "Tornillos 6mm x 100      12.500,00",
    "Subtotal: $ 12.500,00",
    "IVA 21%: $ 2.625,00",
    "Importe Total: $ 15.125,00",
])


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CSA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    from app import db, main
    importlib.reload(db)
    importlib.reload(main)
    return TestClient(main.app)


def test_alta_manual_sin_factura(client):
    r = client.post("/api/movimientos", data={"tipo": "egreso", "monto": "1500.5", "descripcion": "Taxi"})
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["tipo"] == "egreso" and m["monto"] == 1500.5
    assert m["fecha_factura"] == date.today().isoformat()
    assert m["fecha_carga"].startswith(date.today().isoformat())
    assert m["tiene_factura"] is False and m["tiene_adjunto"] is False


def test_alta_con_adjunto_y_descarga(client):
    r = client.post(
        "/api/movimientos",
        data={"tipo": "egreso", "monto": "15125", "fecha_factura": "2026-09-15", "tercero": "Ferretería"},
        files={"archivo": ("factura.pdf", FACTURA, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    m = r.json()
    assert m["tiene_adjunto"] and m["tiene_factura"] and m["adjunto_nombre"] == "factura.pdf"
    d = client.get(f"/api/movimientos/{m['id']}/adjunto")
    assert d.status_code == 200 and d.content == FACTURA


def test_validaciones(client):
    assert client.post("/api/movimientos", data={"tipo": "otro", "monto": "1"}).status_code == 422
    assert client.post("/api/movimientos", data={"tipo": "ingreso", "monto": "-1"}).status_code == 422
    assert client.post("/api/movimientos", data={"tipo": "ingreso", "monto": "1", "fecha_factura": "15/09"}).status_code == 422
    r = client.post("/api/movimientos", data={"tipo": "ingreso", "monto": "1"},
                    files={"archivo": ("x.exe", b"MZ", "application/octet-stream")})
    assert r.status_code == 415


def test_resumen_filtros_y_csv(client):
    client.post("/api/movimientos", data={"tipo": "ingreso", "monto": "1000", "fecha_factura": "2026-09-01", "categoria": "Ventas"})
    client.post("/api/movimientos", data={"tipo": "egreso", "monto": "300", "fecha_factura": "2026-09-10", "tercero": "Edenor"})
    client.post("/api/movimientos", data={"tipo": "egreso", "monto": "50", "fecha_factura": "2026-08-10", "moneda": "USD"})

    res = client.get("/api/resumen").json()
    assert res["ARS"] == {"ingresos": 1000, "egresos": 300, "cantidad": 2, "saldo": 700}
    assert res["USD"]["egresos"] == 50

    assert len(client.get("/api/movimientos", params={"desde": "2026-09-01"}).json()) == 2
    assert len(client.get("/api/movimientos", params={"tipo": "egreso"}).json()) == 2
    assert client.get("/api/movimientos", params={"q": "eden"}).json()[0]["tercero"] == "Edenor"
    assert client.get("/api/categorias").json() == ["Ventas"]

    csv = client.get("/api/exportar.csv").text
    assert "Edenor" in csv and "300,00" in csv


def test_editar_y_borrar(client):
    m = client.post("/api/movimientos", data={"tipo": "egreso", "monto": "10"},
                    files={"archivo": ("f.pdf", FACTURA, "application/pdf")}).json()
    r = client.put(f"/api/movimientos/{m['id']}", data={"tipo": "ingreso", "monto": "20", "quitar_adjunto": "true"})
    assert r.status_code == 200
    e = r.json()
    assert e["tipo"] == "ingreso" and e["monto"] == 20 and not e["tiene_adjunto"] and e["actualizado"]
    assert e["fecha_carga"] == m["fecha_carga"]
    assert client.delete(f"/api/movimientos/{m['id']}").status_code == 204
    assert client.get(f"/api/movimientos/{m['id']}").status_code == 404


def test_leer_factura_pdf_sin_ia(client):
    r = client.post("/api/leer-factura", files={"archivo": ("f.pdf", FACTURA, "application/pdf")})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["metodo"] == "texto"
    assert d["monto"] == 15125.0
    assert d["fecha_factura"] == "2026-09-15"
    assert d["cuit"] == "30-71234567-8"
    assert d["numero_factura"] == "0003-00012345"
    assert d["tercero"].startswith("Ferreteria El Tornillo")


def test_leer_foto_sin_ocr_ni_ia_avisa(client, monkeypatch):
    from app import extractor
    monkeypatch.setattr(extractor, "_filas_ocr", lambda contenido: None)
    r = client.post("/api/leer-factura", files={"archivo": ("f.jpg", b"\xff\xd8\xff", "image/jpeg")})
    d = r.json()
    assert r.status_code == 200 and d["metodo"] == "ninguno" and "requirements-ocr" in d["aviso"]


def test_parse_monto():
    from app.extractor import parse_monto
    assert parse_monto("1.234,56") == 1234.56
    assert parse_monto("1,234.56") == 1234.56
    assert parse_monto("1.500") == 1500
    assert parse_monto("99,9") == 99.9
    assert parse_monto("2500") == 2500


def test_frontend_servido(client):
    r = client.get("/")
    assert r.status_code == 200 and "Ingresos y Gastos" in r.text


def test_password_opcional(client, monkeypatch):
    monkeypatch.setenv("APP_USER", "admin")
    monkeypatch.setenv("APP_PASSWORD", "secreto")
    assert client.get("/api/movimientos").status_code == 401
    assert client.get("/api/movimientos", auth=("admin", "secreto")).status_code == 200


def test_dashboard_resumen_mensual_y_segmentos(client):
    alta = lambda **d: client.post("/api/movimientos", data=d)
    alta(tipo="ingreso", monto="1000", fecha_factura="2026-01-10", categoria="Ventas", tercero="Pérez")
    alta(tipo="egreso", monto="300", fecha_factura="2026-01-20", categoria="Servicios", tercero="Edenor", numero_factura="A-1")
    alta(tipo="egreso", monto="200", fecha_factura="2026-03-05", categoria="")
    alta(tipo="egreso", monto="99", fecha_factura="2026-03-05", moneda="USD")
    alta(tipo="egreso", monto="5", fecha_factura="2025-12-31")  # fuera de rango

    d = client.get("/api/dashboard", params={"desde": "2026-01-01", "hasta": "2026-03-31"}).json()
    assert d["moneda"] == "ARS" and set(d["monedas"]) == {"ARS", "USD"}
    assert [m["mes"] for m in d["meses"]] == ["2026-01", "2026-02", "2026-03"]
    assert d["meses"][0] == {"mes": "2026-01", "ingresos": 1000, "egresos": 300, "cantidad": 2, "saldo": 700, "acumulado": 700}
    assert d["meses"][1]["cantidad"] == 0 and d["meses"][1]["acumulado"] == 700
    assert d["meses"][2]["acumulado"] == 500
    assert d["totales"]["ingresos"] == 1000 and d["totales"]["egresos"] == 500 and d["totales"]["margen"] == 50.0

    eg = {s["nombre"]: s for s in d["segmentos"]["egreso"]}
    assert eg["Servicios"]["total"] == 300 and eg["Servicios"]["pct"] == 60.0
    assert eg["Sin categoría"]["meses"] == [0, 0, 200]

    f = client.get("/api/dashboard", params={"desde": "2026-01-01", "hasta": "2026-03-31", "segmento": "factura"}).json()
    assert {s["nombre"] for s in f["segmentos"]["egreso"]} == {"Con n.º de factura", "Manual (sin factura)"}

    usd = client.get("/api/dashboard", params={"desde": "2026-01-01", "hasta": "2026-03-31", "moneda": "USD"}).json()
    assert usd["totales"]["egresos"] == 99

    assert client.get("/api/dashboard", params={"segmento": "x"}).status_code == 422
    assert client.get("/api/dashboard", params={"desde": "2026-05-01", "hasta": "2026-01-01"}).status_code == 422

    csv = client.get("/api/dashboard.csv", params={"desde": "2026-01-01", "hasta": "2026-03-31"}).text
    assert "2026-01;1000,00;300,00;700,00;700,00;2" in csv and "Servicios" in csv


def test_dashboard_agrupa_otros(client):
    for i in range(12):
        client.post("/api/movimientos", data={"tipo": "egreso", "monto": str(100 + i), "categoria": f"Cat {i}"})
    segs = client.get("/api/dashboard").json()["segmentos"]["egreso"]
    assert len(segs) == 8 and segs[-1]["nombre"] == "Otros (5)"
    assert round(sum(s["pct"] for s in segs)) == 100
