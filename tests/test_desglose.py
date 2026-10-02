import json

from test_app import client, pdf_con_texto  # noqa: F401  (fixture)

MI_CUIT = "30-11111111-8"
# Aviso de pago con retenciones, mismo formato que los de BBVA (datos ficticios)
AVISO = pdf_con_texto([
    [(50, "BANCO DEMO"), (430, "Aviso de Pago")],
    [(50, MI_CUIT), (280, "Forma de pago")],
    [(50, "MI EMPRESA SRL"), (330, "PAGO FACTURAS ELECT. CTA PROP")],
    [(50, "AV. SIEMPRE VIVA 123"), (280, "Número"), (430, "Fecha")],
    [(280, "SUC:017 CTA:0999920"), (450, "28/09/2026")],
    [(280, "Moneda"), (430, "Nro. Comprobante")],
    [(300, "PESO ARGENTINO"), (450, "5400008377")],
    [(50, "Detalle"), (480, "Importe")],
    [(50, "Importe"), (460, "80.510.208,02")],
    [(50, "Ret. Ing. Brutos"), (460, "-1.996.120,86")],
    [(50, "Ret. Seg Social"), (460, "-665.373,62")],
    [(50, "Ret. IVA"), (460, "-11.178.276,82")],
    [(50, "Ret. Ganancias"), (460, "-1.330.747,24")],
    [(50, "Fact. Nro: 0001A00000104"), (200, "$"), (300, "80.510.208,02")],
    [(50, "Total factura"), (300, "80.510.208,02")],
    [(50, "Sesenta y cinco millones trescientos treinta y nueve mil seiscientos ochenta y nueve con 48 centavos"), (460, "65.339.689,48")],
    [(50, "Banco Demo S.A. - Reconquista 199 - Cap. Fed.")],
    [(50, "C.U.I.T.: 30-50000319-3")],
])
RETENCIONES = {"Ret. Ingresos Brutos": 1996120.86, "Ret. Seguridad Social": 665373.62,
               "Ret. IVA": 11178276.82, "Ret. Ganancias": 1330747.24}


def leer(client):
    return client.post("/api/leer-factura", files={"archivo": ("aviso.pdf", AVISO, "application/pdf")}).json()


def test_aviso_de_pago_neto_y_retenciones(client):
    d = leer(client)
    assert d["documento"] == "aviso_pago" and d["metodo"] == "texto"
    assert d["tipo"] == "ingreso"
    assert d["monto"] == 65339689.48
    assert d["desglose"]["bruto"] == 80510208.02
    assert {i["concepto"]: -i["importe"] for i in d["desglose"]["items"]} == RETENCIONES
    assert d["desglose_verificado"] is True
    assert d["fecha_factura"] == "2026-09-28"
    assert d["numero_factura"] == "0001A00000104"
    assert d["cuit"] == "30-50000319-3"
    assert "5400008377" in d["descripcion"]
    assert d["sugerencia_cuit_propio"]["cuit"] == MI_CUIT


def test_aviso_con_cuit_propio_y_carga_automatica(client):
    client.put("/api/ajustes", json={"cuits_propios": [MI_CUIT]})
    r = client.post("/api/carga-automatica", files={"archivo": ("aviso.pdf", AVISO, "application/pdf")}).json()
    m = r["movimiento"]
    assert r["estado"] == "creado" and m["tipo"] == "ingreso" and m["monto"] == 65339689.48
    assert m["desglose"]["bruto"] == 80510208.02 and len(m["desglose"]["items"]) == 4

    dash = client.get("/api/dashboard", params={"desde": "2026-09-01", "hasta": "2026-09-30"}).json()
    assert dash["totales"]["ingresos"] == 65339689.48
    ret = dash["deducciones"]["ingreso"]
    assert ret["total"] == 15170518.54 and ret["bruto"] == 80510208.02
    assert ret["conceptos"][0]["nombre"] == "Ret. IVA"

    csv = client.get("/api/exportar.csv").text
    assert "80510208,02" in csv and "15170518,54" in csv and "Ret. IVA: 11178276,82" in csv
    hoja = client.get("/api/dashboard.csv", params={"desde": "2026-09-01", "hasta": "2026-09-30"}).text
    assert "Retenciones sufridas" in hoja and "Ret. Ganancias;1330747,24" in hoja


def test_desglose_manual_calcula_el_neto(client):
    des = {"bruto": 1000, "items": [{"concepto": "Ret. IVA", "importe": 100}, {"concepto": "Comisión", "importe": -50}]}
    m = client.post("/api/movimientos", data={"tipo": "ingreso", "monto": "1", "desglose": json.dumps(des)}).json()
    assert m["monto"] == 850 and m["desglose"]["items"][0]["importe"] == -100

    # Al editar sin desglose se quita y queda el monto que se envía
    e = client.put(f"/api/movimientos/{m['id']}", data={"tipo": "ingreso", "monto": "900", "desglose": ""}).json()
    assert e["monto"] == 900 and e["desglose"] is None

    malo = {"bruto": 10, "items": [{"concepto": "x", "importe": 20}]}
    assert client.post("/api/movimientos", data={"tipo": "ingreso", "monto": "1", "desglose": json.dumps(malo)}).status_code == 422
    assert client.post("/api/movimientos", data={"tipo": "ingreso", "monto": "1", "desglose": "{roto"}).status_code == 422


def test_ocr_confunde_coma_decimal():
    from app.extractor import parse_monto
    assert parse_monto("80.510.208.02") == 80510208.02
    assert parse_monto("-665.373.62") == -665373.62
    assert parse_monto("1.500.000") == 1500000
