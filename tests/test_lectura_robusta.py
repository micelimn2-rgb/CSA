import io
import subprocess

import pytest

from test_app import client, pdf_con_texto  # noqa: F401  (fixture)

NOMBRE = "Cbte Pago EnvialoSimple - 200.000 envios $392.300.- (03-10-2026).pdf"
INFORME = [
    [(50, "Detalle del Informe de Pago")],
    [(50, "Metodo de pago:"), (330, "Tarjeta Credito / Debito")],
    [(50, "Numero de informe:"), (330, "12554074")],
    [(50, "Fecha del pago:"), (330, "03/10/2026")],
    [(50, "Importe:"), (330, "$ 392.300,00")],
    [(50, "Numero de operacion:"), (330, "950522-11953018-ar")],
    [(50, "Estado:"), (330, "APROBADO")],
]


def subir(client, contenido, nombre=NOMBRE, tipo="application/pdf"):
    return client.post("/api/carga-automatica", files={"archivo": (nombre, contenido, tipo)}).json()


def test_informe_de_pago_toma_proveedor_del_nombre_del_archivo(client):
    r = subir(client, pdf_con_texto(INFORME))
    m = r["movimiento"]
    assert r["estado"] == "creado"
    assert (m["tipo"], m["monto"], m["fecha_factura"]) == ("egreso", 392300.0, "2026-10-03")
    assert m["tercero"] == "EnvialoSimple"
    assert m["numero_factura"] == "950522-11953018-ar"
    assert "200.000 envios" in m["descripcion"] and "Tarjeta" in m["descripcion"]


def test_sin_nombre_util_no_usa_el_titulo_como_proveedor(client):
    d = client.post("/api/leer-factura", files={"archivo": ("scan0001.pdf", pdf_con_texto(INFORME), "application/pdf")}).json()
    assert d["monto"] == 392300.0 and "tercero" not in d


def test_pago_rechazado_no_se_carga(client):
    rechazado = [f if f[0][1] != "Estado:" else [(50, "Estado:"), (330, "RECHAZADO")] for f in INFORME]
    r = subir(client, pdf_con_texto(rechazado))
    assert r["estado"] == "incompleto" and "RECHAZADO" in r["mensaje"]
    assert client.get("/api/movimientos").json() == []


def test_si_el_lector_de_imagenes_se_cae_la_app_sigue(client, monkeypatch):
    from app import extractor

    monkeypatch.setattr(extractor, "ocr_disponible", lambda: True)
    monkeypatch.setattr(extractor.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, returncode=3221225477, stdout=b"", stderr=b"Windows fatal exception: access violation"))
    d = client.post("/api/leer-factura", files={"archivo": ("foto.png", b"\x89PNG....", "image/png")}).json()
    assert d["metodo"] == "ninguno" and "se cerró con un error" in d["aviso"]
    r = subir(client, b"\x89PNG....", "foto.png", "image/png")
    assert r["estado"] == "incompleto" and "lector de imágenes" in r["mensaje"]
    assert client.get("/api/version").status_code == 200  # el servidor sigue vivo


def test_pdf_escaneado_se_lee_con_ocr(client):
    pytest.importorskip("rapidocr_onnxruntime")
    pytest.importorskip("pypdfium2")
    from PIL import Image, ImageDraw, ImageFont

    try:
        fuente = ImageFont.truetype("DejaVuSans.ttf", 40)
    except OSError:
        fuente = ImageFont.load_default(size=40)
    img = Image.new("RGB", (1700, 2200), "white")  # como una hoja escaneada (grande)
    dib = ImageDraw.Draw(img)
    for i, fila in enumerate(INFORME):
        for x, t in fila:
            dib.text((x * 2, 120 + i * 110), t, fill="black", font=fuente)
    pdf = io.BytesIO()
    img.save(pdf, format="PDF", resolution=200)

    d = client.post("/api/leer-factura", files={"archivo": (NOMBRE, pdf.getvalue(), "application/pdf")}).json()
    assert d["metodo"] == "ocr"
    assert d["monto"] == 392300.0 and d["fecha_factura"] == "2026-10-03"
    assert d["tercero"] == "EnvialoSimple" and d["tipo"] == "egreso"
