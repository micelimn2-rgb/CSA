import sqlite3

from test_app import client  # noqa: F401  (fixture)
from test_carga_automatica import MI_CUIT, TRANSFERENCIA, subir


def ids(client):
    e = {x["nombre"]: x["id"] for x in client.get("/api/empresas").json()}
    return e["Solvencias"], e["Agencia"]


def test_empresas_iniciales(client):
    nombres = [e["nombre"] for e in client.get("/api/empresas").json()]
    assert nombres == ["Solvencias", "Agencia"]


def test_movimientos_separados_por_empresa(client):
    sol, age = ids(client)
    client.post(f"/api/movimientos?empresa={sol}", data={"tipo": "ingreso", "monto": "1000", "categoria": "Cobranzas"})
    client.post(f"/api/movimientos?empresa={age}", data={"tipo": "egreso", "monto": "300", "categoria": "Publicidad"})

    assert [m["monto"] for m in client.get(f"/api/movimientos?empresa={sol}").json()] == [1000]
    assert [m["monto"] for m in client.get(f"/api/movimientos?empresa={age}").json()] == [300]
    assert client.get(f"/api/resumen?empresa={age}").json()["ARS"]["egresos"] == 300
    assert client.get(f"/api/categorias?empresa={sol}").json() == ["Cobranzas"]
    assert client.get(f"/api/dashboard?empresa={age}").json()["totales"]["ingresos"] == 0
    assert client.get(f"/api/dashboard?empresa={sol}").json()["empresa"] == "Solvencias"
    csv = client.get(f"/api/exportar.csv?empresa={age}")
    assert "agencia" in csv.headers["content-disposition"] and "Publicidad" in csv.text and "Cobranzas" not in csv.text
    assert client.get("/api/movimientos?empresa=999").status_code == 404


def test_mover_movimiento_de_empresa(client):
    sol, age = ids(client)
    m = client.post(f"/api/movimientos?empresa={sol}", data={"tipo": "egreso", "monto": "50"}).json()
    assert m["empresa_id"] == sol
    r = client.put(f"/api/movimientos/{m['id']}", data={"tipo": "egreso", "monto": "50", "empresa_id": str(age)}).json()
    assert r["empresa_id"] == age
    assert client.get(f"/api/movimientos?empresa={sol}").json() == []


def test_cuit_propio_por_empresa_y_sin_repetir(client):
    sol, age = ids(client)
    assert client.put(f"/api/ajustes?empresa={sol}", json={"cuits_propios": [MI_CUIT]}).json()["cuits_propios"] == [MI_CUIT]
    assert client.get(f"/api/ajustes?empresa={age}").json()["cuits_propios"] == []
    r = client.put(f"/api/empresas/{age}", json={"cuits_propios": [MI_CUIT]})
    assert r.status_code == 409 and "Solvencias" in r.json()["detail"]


def test_carga_automatica_va_a_la_empresa_del_cuit(client):
    sol, age = ids(client)
    client.put(f"/api/empresas/{age}", json={"cuits_propios": [MI_CUIT]})
    # Estando en Solvencias, el comprobante es de Agencia (el ordenante es su CUIT)
    r = subir_en(client, sol)
    assert r["estado"] == "creado"
    assert r["movimiento"]["empresa_id"] == age and r["datos"]["empresa_nombre"] == "Agencia"
    assert r["movimiento"]["tipo"] == "egreso"
    assert client.get(f"/api/movimientos?empresa={sol}").json() == []
    # El duplicado se detecta dentro de la empresa
    assert subir_en(client, sol)["estado"] == "duplicado"


def test_sin_cuit_va_a_la_empresa_actual_y_sugiere(client):
    sol, age = ids(client)
    r = subir_en(client, age)
    assert r["movimiento"]["empresa_id"] == age
    assert r["sugerencia_cuit_propio"]["cuit"] == MI_CUIT
    # Si ese CUIT ya es de otra empresa, no se sugiere para esta
    client.put(f"/api/empresas/{sol}", json={"cuits_propios": [MI_CUIT]})
    r2 = subir_en(client, age, forzar="true")
    assert r2["movimiento"]["empresa_id"] == sol and "sugerencia_cuit_propio" not in r2


def test_categoria_aprendida_es_por_empresa(client):
    sol, age = ids(client)
    m = subir_en(client, sol)["movimiento"]
    client.put(f"/api/movimientos/{m['id']}", data={"tipo": "egreso", "monto": "369300", "categoria": "Honorarios",
                                                    "tercero": "Pérez Juan Carlos", "cuit": m["cuit"]})
    en_agencia = subir_en(client, age)["movimiento"]
    assert en_agencia["empresa_id"] == age
    assert en_agencia["tercero"] == "Pérez Juan Carlos"  # el nombre corregido sirve para todas
    assert en_agencia["categoria"] is None  # la categoría es propia de cada empresa


def test_crear_renombrar_y_borrar_empresa(client):
    nueva = client.post("/api/empresas", json={"nombre": "Tercera"}).json()
    assert client.post("/api/empresas", json={"nombre": "Tercera"}).status_code == 409
    assert client.put(f"/api/empresas/{nueva['id']}", json={"nombre": "Otra"}).json()["nombre"] == "Otra"
    client.post(f"/api/movimientos?empresa={nueva['id']}", data={"tipo": "egreso", "monto": "1"})
    assert client.delete(f"/api/empresas/{nueva['id']}").status_code == 409
    mov = client.get(f"/api/movimientos?empresa={nueva['id']}").json()[0]
    client.delete(f"/api/movimientos/{mov['id']}")
    assert client.delete(f"/api/empresas/{nueva['id']}").status_code == 204


def test_migracion_asigna_lo_viejo_a_la_primera_empresa(tmp_path, monkeypatch):
    conn = sqlite3.connect(tmp_path / "csa.db")
    conn.executescript("""CREATE TABLE movimientos (id INTEGER PRIMARY KEY AUTOINCREMENT, tipo TEXT NOT NULL,
        monto REAL NOT NULL, moneda TEXT NOT NULL DEFAULT 'ARS', fecha_factura TEXT NOT NULL, fecha_carga TEXT NOT NULL,
        numero_factura TEXT, tercero TEXT, cuit TEXT, categoria TEXT, descripcion TEXT,
        tiene_factura INTEGER NOT NULL DEFAULT 0, adjunto_archivo TEXT, adjunto_nombre TEXT, adjunto_tipo TEXT,
        actualizado TEXT, origen TEXT NOT NULL DEFAULT 'manual', revisar INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE ajustes (clave TEXT PRIMARY KEY, valor TEXT);
        INSERT INTO ajustes VALUES ('cuits_propios', '30-11111111-8');
        INSERT INTO movimientos (tipo, monto, fecha_factura, fecha_carga) VALUES ('egreso', 10, '2026-01-01', '2026-01-01');""")
    conn.commit()
    conn.close()
    monkeypatch.setenv("CSA_DATA_DIR", str(tmp_path))
    import importlib
    from app import db, main
    importlib.reload(db)
    importlib.reload(main)
    from fastapi.testclient import TestClient
    c = TestClient(main.app)
    empresas = c.get("/api/empresas").json()
    assert empresas[0]["nombre"] == "Solvencias" and empresas[0]["cuits_propios"] == [MI_CUIT]
    assert empresas[0]["movimientos"] == 1


def subir_en(client, empresa, **data):
    return client.post(f"/api/carga-automatica?empresa={empresa}",
                       files={"archivo": ("t.pdf", TRANSFERENCIA, "application/pdf")}, data=data).json()
