import io
from datetime import datetime

import openpyxl

from test_app import client  # noqa: F401  (fixture)

CABECERA = ["FECHA", "Consulta de Clientes con Deuda", "0-30", "30-60", "60-90", "90-120", "VENDEDOR", "MM", "JG"]


def excel(filas) -> bytes:
    libro = openpyxl.Workbook()
    hoja = libro.active
    hoja.append(CABECERA)
    for f in filas:
        hoja.append(f)
    buf = io.BytesIO()
    libro.save(buf)
    return buf.getvalue()


SEMANA_1 = [
    [datetime(2026, 10, 19), "A B CONSTRUCCIONES S R L ", 1000, 0, 0, 0, "GAONA", None, "19/10/2026 PAGA ESTA SEMANA"],
    [datetime(2026, 10, 19), "Pérez Hnos", 500, 250.5, 0, 0, "FALCON", "Mandar mje reclamar", None],
    [None, None, 1500, 250.5, 0, 0, None, None, None],  # renglón de totales
]
SEMANA_2 = [
    [datetime(2026, 10, 26), "A B CONSTRUCCIONES S R L", 1200, 0, 0, 0, "GAONA", None, "19/10/2026 PAGA ESTA SEMANA"],
]


def importar(client, filas):
    r = client.post("/api/clientes/importar", files={"archivo": ("deuda.xlsx", excel(filas))})
    assert r.status_code == 200, r.text
    return r.json()


def test_importar_y_buscar(client):
    r = importar(client, SEMANA_1)
    assert r == {"filas": 2, "clientes_nuevos": 2, "cortes": 2, "comentarios_nuevos": 2, "ultimo_corte": "2026-10-19"}

    # Sin tildes ni mayúsculas, y por varias palabras
    encontrados = client.get("/api/clientes?q=perez").json()["clientes"]
    assert [c["nombre"] for c in encontrados] == ["Pérez Hnos"]
    assert encontrados[0]["tramos"] == {"0-30": 500, "30-60": 250.5, "60-90": 0, "90-120": 0}
    assert encontrados[0]["total"] == 750.5 and encontrados[0]["vendedor"] == "FALCON"
    assert [c["nombre"] for c in client.get("/api/clientes?q=constr a b").json()["clientes"]] == ["A B CONSTRUCCIONES S R L"]
    assert client.get("/api/clientes?q=zzz").json()["clientes"] == []


def test_reimportar_suma_cortes_sin_duplicar(client):
    importar(client, SEMANA_1)
    importar(client, SEMANA_1)
    r = importar(client, SEMANA_2)
    # Mismo cliente aunque cambien los espacios; la nota repetida no se duplica
    assert r["clientes_nuevos"] == 0 and r["comentarios_nuevos"] == 0

    lista = {c["nombre"]: c for c in client.get("/api/clientes").json()["clientes"]}
    ab, perez = lista["A B CONSTRUCCIONES S R L"], lista["Pérez Hnos"]
    assert ab["total"] == 1200 and ab["en_ultimo_corte"]
    assert not perez["en_ultimo_corte"]  # no vino en el Excel de la semana 2

    ficha = client.get(f"/api/clientes/{ab['id']}").json()
    assert [k["fecha"] for k in ficha["cortes"]] == ["2026-10-26", "2026-10-19"]
    assert [(h["autor"], h["origen"]) for h in ficha["historial"]] == [("JG", "excel")]


def test_registro_de_acciones(client):
    importar(client, SEMANA_1)
    cid = client.get("/api/clientes?q=perez").json()["clientes"][0]["id"]

    r = client.post(f"/api/clientes/{cid}/comentarios", json={"autor": "  Jesica ", "texto": "Llamé, paga el viernes"})
    assert r.status_code == 201
    assert r.json()["autor"] == "Jesica" and r.json()["origen"] == "web"
    client.post(f"/api/clientes/{cid}/comentarios", json={"autor": "Externo", "texto": "Mandé el mail"})

    historial = client.get(f"/api/clientes/{cid}").json()["historial"]
    assert [h["texto"] for h in historial] == ["Mandé el mail", "Llamé, paga el viernes", "Mandar mje reclamar"]
    assert client.get("/api/clientes?q=perez").json()["clientes"][0]["comentarios"] == 3

    assert client.post(f"/api/clientes/{cid}/comentarios", json={"autor": "", "texto": "x"}).status_code == 422
    assert client.post(f"/api/clientes/{cid}/comentarios", json={"autor": "A", "texto": " "}).status_code == 422
    assert client.post("/api/clientes/999/comentarios", json={"autor": "A", "texto": "x"}).status_code == 404


def test_excel_invalido(client):
    r = client.post("/api/clientes/importar", files={"archivo": ("x.xlsx", b"no es un excel")})
    assert r.status_code == 422
    libro = openpyxl.Workbook()
    libro.active.append(["A", "B"])
    buf = io.BytesIO()
    libro.save(buf)
    r = client.post("/api/clientes/importar", files={"archivo": ("x.xlsx", buf.getvalue())})
    assert r.status_code == 422 and "VENDEDOR" in r.json()["detail"]
