import io
import sqlite3

import pytest

from test_app import FACTURA, client, pdf_con_texto  # noqa: F401  (client es un fixture)

# Comprobante de transferencia con el formato en columnas de los bancos (datos ficticios)
TRANSFERENCIA = pdf_con_texto([
    [(50, "BANCO DEMO"), (330, "Transferencias Inmediatas Otras Entidades")],
    [(50, "Datos Ordenante:"), (420, "N° de Referencia")],
    [(50, "Cuenta Origen:"), (220, "CC $ 0999-111111/5"), (400, "10049140005999999")],
    [(50, "Titularidad:"), (220, "MI EMPRESA SRL"), (440, "Fecha")],
    [(50, "CUIT / CUIL / CDI:"), (220, "30-11111111-8"), (420, "28/09/2026")],
    [(50, "Datos Beneficiario:"), (445, "Hora")],
    [(50, "CBU/CVU Destino:"), (220, "0150543601000101111111"), (430, "13:57:00")],
    [(50, "Titularidad:"), (220, "PEREZ JUAN CARLOS")],
    [(50, "CUIT / CUIL / CDI:"), (220, "20-22222222-5")],
    [(50, "Importe:"), (220, "369.300,00")],
    [(50, "Motivo:"), (220, "Honorarios")],
    [(50, "Concepto:"), (220, "VARIOS")],
])
MI_CUIT = "30-11111111-8"


def subir(client, contenido, nombre="comprobante.pdf", tipo="application/pdf", **data):
    return client.post("/api/carga-automatica", files={"archivo": (nombre, contenido, tipo)}, data=data).json()


def test_lee_transferencia_en_columnas(client):
    d = client.post("/api/leer-factura", files={"archivo": ("t.pdf", TRANSFERENCIA, "application/pdf")}).json()
    assert d["documento"] == "transferencia" and d["metodo"] == "texto"
    assert d["monto"] == 369300.0
    assert d["fecha_factura"] == "2026-09-28"
    assert d["numero_factura"] == "10049140005999999"
    assert d["tipo"] == "egreso"
    assert d["tercero"] == "PEREZ JUAN CARLOS" and d["cuit"] == "20-22222222-5"
    assert "Honorarios" in d["descripcion"] and "0150543601000101111111" in d["descripcion"]
    # Sin CUIT propio configurado, sugiere el del ordenante
    assert d["sugerencia_cuit_propio"] == {"cuit": MI_CUIT, "nombre": "MI EMPRESA SRL"}


def test_carga_automatica_crea_y_detecta_duplicado(client):
    r = subir(client, TRANSFERENCIA)
    assert r["estado"] == "creado"
    m = r["movimiento"]
    assert (m["tipo"], m["monto"], m["fecha_factura"]) == ("egreso", 369300.0, "2026-09-28")
    assert m["tercero"] == "PEREZ JUAN CARLOS" and m["tiene_adjunto"] and m["revisar"] and m["origen"] == "automatica"

    assert subir(client, TRANSFERENCIA)["estado"] == "duplicado"
    assert subir(client, TRANSFERENCIA, forzar="true")["estado"] == "creado"

    # Al editarlo deja de figurar "a revisar"
    e = client.put(f"/api/movimientos/{m['id']}", data={"tipo": "egreso", "monto": "369300", "fecha_factura": "2026-09-28"}).json()
    assert e["revisar"] is False


def test_cuit_propio_define_ingreso(client):
    assert client.put("/api/ajustes", json={"cuits_propios": "20222222225"}).json()["cuits_propios"] == ["20-22222222-5"]
    m = subir(client, TRANSFERENCIA)["movimiento"]
    assert m["tipo"] == "ingreso" and m["tercero"] == "MI EMPRESA SRL" and m["cuit"] == MI_CUIT

    client.put("/api/ajustes", json={"cuits_propios": [MI_CUIT]})
    d = client.post("/api/leer-factura", files={"archivo": ("t.pdf", TRANSFERENCIA, "application/pdf")}).json()
    assert d["tipo"] == "egreso" and "sugerencia_cuit_propio" not in d

    assert client.put("/api/ajustes", json={"cuits_propios": ["123"]}).status_code == 422


def test_aprende_nombre_y_categoria_del_historial(client):
    m = subir(client, TRANSFERENCIA)["movimiento"]
    client.put(f"/api/movimientos/{m['id']}", data={
        "tipo": "egreso", "monto": "369300", "fecha_factura": "2026-09-28",
        "tercero": "Pérez, Juan Carlos", "cuit": "20-22222222-5", "categoria": "Honorarios profesionales"})
    nuevo = subir(client, TRANSFERENCIA, forzar="true")["movimiento"]
    assert nuevo["tercero"] == "Pérez, Juan Carlos"
    assert nuevo["categoria"] == "Honorarios profesionales"


def test_factura_sigue_funcionando_en_carga_automatica(client):
    m = subir(client, FACTURA, "factura.pdf")["movimiento"]
    assert m["tipo"] == "egreso" and m["monto"] == 15125.0 and m["numero_factura"] == "0003-00012345"
    assert m["tercero"].startswith("Ferreteria El Tornillo")


def test_sin_monto_queda_incompleto(client):
    r = subir(client, pdf_con_texto(["Hola", "Nada para leer aca", "Solo texto"]))
    assert r["estado"] == "incompleto" and "monto" in r["mensaje"]
    assert client.get("/api/movimientos").json() == []


def test_migra_base_vieja(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "csa.db")
    conn.executescript("""CREATE TABLE movimientos (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL,
        monto REAL NOT NULL, moneda TEXT NOT NULL DEFAULT 'ARS', fecha_factura TEXT NOT NULL, fecha_carga TEXT NOT NULL,
        numero_factura TEXT, tercero TEXT, cuit TEXT, categoria TEXT, descripcion TEXT,
        tiene_factura INTEGER NOT NULL DEFAULT 0, adjunto_archivo TEXT, adjunto_nombre TEXT, adjunto_tipo TEXT,
        actualizado TEXT);
        INSERT INTO movimientos (tipo, monto, fecha_factura, fecha_carga) VALUES ('egreso', 10, '2026-01-01', '2026-01-01');""")
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSA_DATA_DIR", str(tmp_path))
    import importlib
    from app import db, main
    importlib.reload(db)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    viejo = TestClient(main.app).get("/api/movimientos").json()[0]
    assert viejo["origen"] == "manual" and viejo["revisar"] is False


def test_ocr_de_imagen(client):
    pytest.importorskip("rapidocr_onnxruntime")
    from PIL import Image, ImageDraw, ImageFont

    try:
        fuente = ImageFont.truetype("DejaVuSans.ttf", 22)
    except OSError:
        fuente = ImageFont.load_default(size=22)
    img = Image.new("RGB", (900, 420), "white")
    dib = ImageDraw.Draw(img)
    filas = [
        [(40, "Transferencias Inmediatas")],
        [(40, "Datos Ordenante:"), (600, "N° de Referencia")],
        [(40, "Titularidad:"), (300, "MI EMPRESA SRL"), (640, "Fecha")],
        [(40, "CUIT / CUIL / CDI:"), (300, "30-11111111-8"), (610, "15/08/2026")],
        [(40, "Datos Beneficiario:")],
        [(40, "Titularidad:"), (300, "GOMEZ ANA")],
        [(40, "CUIT / CUIL / CDI:"), (300, "27-33333333-4")],
        [(40, "Importe:"), (300, "45.000,50")],
    ]
    for i, fila in enumerate(filas):
        for x, t in fila:
            dib.text((x, 20 + i * 48), t, fill="black", font=fuente)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    d = client.post("/api/leer-factura", files={"archivo": ("t.png", buf.getvalue(), "image/png")}).json()
    assert d["metodo"] == "ocr"
    assert d["monto"] == 45000.5
    assert d["fecha_factura"] == "2026-08-15"
    assert d["cuit"] == "27-33333333-4" and d["tipo"] == "egreso"
