"""API y servidor web del sistema de ingresos y gastos."""
import base64
import csv
import io
import os
import re
import secrets
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import VERSION, db, extractor

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
MAX_ADJUNTO = 15 * 1024 * 1024  # 15 MB
TIPOS_ADJUNTO = {"application/pdf", "image/jpeg", "image/png", "image/webp", "image/gif"}

app = FastAPI(title="CSA - Ingresos y Gastos")
db.init_db()


# ---------------------------------------------------------------- Acceso opcional con usuario/clave

@app.middleware("http")
async def basic_auth(request: Request, call_next):
    usuario, clave = os.environ.get("APP_USER"), os.environ.get("APP_PASSWORD")
    if usuario and clave and request.url.path not in ("/manifest.json", "/sw.js"):
        header = request.headers.get("authorization", "")
        ok = False
        if header.startswith("Basic "):
            try:
                u, _, p = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(u, usuario) and secrets.compare_digest(p, clave)
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="CSA"'})
    return await call_next(request)


@app.get("/api/version")
def version():
    return {"version": VERSION}


# ---------------------------------------------------------------- Helpers

def _ahora() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _validar_fecha(valor: str | None) -> str:
    if not valor:
        return date.today().isoformat()
    try:
        return date.fromisoformat(valor).isoformat()
    except ValueError:
        raise HTTPException(422, "fecha_factura debe tener formato YYYY-MM-DD")


def _validar_tipo(tipo: str) -> str:
    tipo = tipo.lower().strip()
    if tipo not in ("ingreso", "egreso"):
        raise HTTPException(422, "tipo debe ser 'ingreso' o 'egreso'")
    return tipo


def _validar_monto(monto: float) -> float:
    if monto < 0:
        raise HTTPException(422, "El monto no puede ser negativo")
    return round(monto, 2)


async def _leer_adjunto(archivo: UploadFile) -> tuple[bytes, str]:
    tipo = (archivo.content_type or "").lower()
    if tipo not in TIPOS_ADJUNTO:
        raise HTTPException(415, "Solo se aceptan PDF o imágenes (JPG, PNG, WEBP)")
    contenido = await archivo.read()
    if len(contenido) > MAX_ADJUNTO:
        raise HTTPException(413, "El archivo supera los 15 MB")
    if not contenido:
        raise HTTPException(422, "El archivo está vacío")
    return contenido, tipo


def _guardar_adjunto(contenido: bytes, nombre_original: str) -> str:
    ext = Path(nombre_original or "").suffix.lower()[:10]
    nombre = f"{uuid.uuid4().hex}{ext}"
    (db.UPLOADS_DIR / nombre).write_bytes(contenido)
    return nombre


def _borrar_adjunto(nombre: str | None) -> None:
    if nombre:
        (db.UPLOADS_DIR / nombre).unlink(missing_ok=True)


def _fila(row) -> dict:
    d = dict(row)
    d["tiene_factura"] = bool(d["tiene_factura"])
    d["tiene_adjunto"] = bool(d.pop("adjunto_archivo"))
    d["revisar"] = bool(d.get("revisar"))
    return d


def _obtener(conn, mov_id: int):
    row = conn.execute("SELECT * FROM movimientos WHERE id = ?", (mov_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Movimiento no encontrado")
    return row


def _empresas(conn) -> list[dict]:
    return [{"id": r["id"], "nombre": r["nombre"], "cuits_propios": [c for c in r["cuits_propios"].split(",") if c]}
            for r in conn.execute("SELECT * FROM empresas ORDER BY id")]


def _empresa_id(conn, empresa: int | None) -> int:
    """Valida la empresa pedida; sin empresa, usa la primera."""
    if empresa is None:
        row = conn.execute("SELECT MIN(id) id FROM empresas").fetchone()
    else:
        row = conn.execute("SELECT id FROM empresas WHERE id = ?", (empresa,)).fetchone()
    if not row or row["id"] is None:
        raise HTTPException(404, "Empresa no encontrada")
    return row["id"]


def _filtros(empresa_id, tipo, desde, hasta, q, categoria):
    where, params = ["empresa_id = ?"], [empresa_id]
    if tipo:
        where.append("tipo = ?")
        params.append(_validar_tipo(tipo))
    if desde:
        where.append("fecha_factura >= ?")
        params.append(_validar_fecha(desde))
    if hasta:
        where.append("fecha_factura <= ?")
        params.append(_validar_fecha(hasta))
    if categoria:
        where.append("categoria = ?")
        params.append(categoria)
    if q:
        where.append("(tercero LIKE ? OR descripcion LIKE ? OR numero_factura LIKE ? OR cuit LIKE ? OR categoria LIKE ?)")
        params += [f"%{q}%"] * 5
    return " WHERE " + " AND ".join(where), params


# ---------------------------------------------------------------- Movimientos

@app.get("/api/movimientos")
def listar(empresa: int | None = None, tipo: str | None = None, desde: str | None = None,
           hasta: str | None = None, q: str | None = None, categoria: str | None = None, limite: int = 500):
    with db.connect() as conn:
        where, params = _filtros(_empresa_id(conn, empresa), tipo, desde, hasta, q, categoria)
        rows = conn.execute(
            f"SELECT * FROM movimientos{where} ORDER BY fecha_factura DESC, id DESC LIMIT ?",
            (*params, max(1, min(limite, 5000))),
        ).fetchall()
    return [_fila(r) for r in rows]


@app.get("/api/resumen")
def resumen(empresa: int | None = None, tipo: str | None = None, desde: str | None = None,
            hasta: str | None = None, q: str | None = None, categoria: str | None = None):
    with db.connect() as conn:
        where, params = _filtros(_empresa_id(conn, empresa), tipo, desde, hasta, q, categoria)
        rows = conn.execute(
            f"SELECT moneda, tipo, SUM(monto) AS total, COUNT(*) AS cantidad FROM movimientos{where} GROUP BY moneda, tipo",
            params,
        ).fetchall()
    por_moneda: dict = {}
    for r in rows:
        m = por_moneda.setdefault(r["moneda"], {"ingresos": 0.0, "egresos": 0.0, "cantidad": 0})
        m["ingresos" if r["tipo"] == "ingreso" else "egresos"] += round(r["total"], 2)
        m["cantidad"] += r["cantidad"]
    for m in por_moneda.values():
        m["saldo"] = round(m["ingresos"] - m["egresos"], 2)
    return por_moneda


@app.get("/api/categorias")
def categorias(empresa: int | None = None):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT categoria FROM movimientos WHERE empresa_id = ? AND categoria IS NOT NULL "
            "AND categoria <> '' ORDER BY categoria", (_empresa_id(conn, empresa),)
        ).fetchall()
    return [r["categoria"] for r in rows]


@app.get("/api/movimientos/{mov_id}")
def obtener(mov_id: int):
    with db.connect() as conn:
        return _fila(_obtener(conn, mov_id))


@app.post("/api/movimientos", status_code=201)
async def crear(
    empresa: int | None = None,
    empresa_id: int | None = Form(None),
    tipo: str = Form(...),
    monto: float = Form(...),
    fecha_factura: str | None = Form(None),
    moneda: str = Form("ARS"),
    numero_factura: str | None = Form(None),
    tercero: str | None = Form(None),
    cuit: str | None = Form(None),
    categoria: str | None = Form(None),
    descripcion: str | None = Form(None),
    archivo: UploadFile | None = File(None),
):
    adjunto = adjunto_nombre = adjunto_tipo = None
    if archivo is not None and archivo.filename:
        contenido, adjunto_tipo = await _leer_adjunto(archivo)
        adjunto = _guardar_adjunto(contenido, archivo.filename)
        adjunto_nombre = archivo.filename

    with db.connect() as conn:
        eid = _empresa_id(conn, empresa_id or empresa)
        mov_id = _insertar(conn, eid, dict(tipo=tipo, monto=monto, moneda=moneda, fecha_factura=fecha_factura,
                                      numero_factura=numero_factura, tercero=tercero, cuit=cuit, categoria=categoria,
                                      descripcion=descripcion), (adjunto, adjunto_nombre, adjunto_tipo))
        return _fila(_obtener(conn, mov_id))


def _insertar(conn, empresa_id: int, c: dict, adjunto: tuple, origen: str = "manual", revisar: bool = False) -> int:
    numero = _vacio(c.get("numero_factura"))
    cur = conn.execute(
        """INSERT INTO movimientos (tipo, monto, moneda, fecha_factura, fecha_carga, numero_factura, tercero,
               cuit, categoria, descripcion, tiene_factura, adjunto_archivo, adjunto_nombre, adjunto_tipo,
               origen, revisar, empresa_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (_validar_tipo(c["tipo"]), _validar_monto(float(c["monto"])), (c.get("moneda") or "ARS").upper()[:5],
         _validar_fecha(c.get("fecha_factura")), _ahora(), numero, _vacio(c.get("tercero")), _vacio(c.get("cuit")),
         _vacio(c.get("categoria")), _vacio(c.get("descripcion")), int(bool(adjunto[0] or numero)),
         *adjunto, origen, int(revisar), empresa_id),
    )
    return cur.lastrowid


@app.put("/api/movimientos/{mov_id}")
async def actualizar(
    mov_id: int,
    tipo: str = Form(...),
    monto: float = Form(...),
    fecha_factura: str | None = Form(None),
    moneda: str = Form("ARS"),
    numero_factura: str | None = Form(None),
    tercero: str | None = Form(None),
    cuit: str | None = Form(None),
    categoria: str | None = Form(None),
    descripcion: str | None = Form(None),
    quitar_adjunto: bool = Form(False),
    empresa_id: int | None = Form(None),
    archivo: UploadFile | None = File(None),
):
    with db.connect() as conn:
        actual = _obtener(conn, mov_id)
        eid = _empresa_id(conn, empresa_id) if empresa_id else actual["empresa_id"]
        adjunto, adjunto_nombre, adjunto_tipo = actual["adjunto_archivo"], actual["adjunto_nombre"], actual["adjunto_tipo"]
        nuevo = None
        if archivo is not None and archivo.filename:
            contenido, tipo_nuevo = await _leer_adjunto(archivo)
            nuevo = (_guardar_adjunto(contenido, archivo.filename), archivo.filename, tipo_nuevo)
        if nuevo or quitar_adjunto:
            _borrar_adjunto(adjunto)
            adjunto, adjunto_nombre, adjunto_tipo = nuevo or (None, None, None)

        tiene_factura = bool(adjunto or (numero_factura or "").strip())
        conn.execute(
            """UPDATE movimientos SET tipo=?, monto=?, moneda=?, fecha_factura=?, numero_factura=?, tercero=?, cuit=?,
                   categoria=?, descripcion=?, tiene_factura=?, adjunto_archivo=?, adjunto_nombre=?, adjunto_tipo=?,
                   actualizado=?, revisar=0, empresa_id=? WHERE id=?""",
            (_validar_tipo(tipo), _validar_monto(monto), (moneda or "ARS").upper()[:5], _validar_fecha(fecha_factura),
             _vacio(numero_factura), _vacio(tercero), _vacio(cuit), _vacio(categoria), _vacio(descripcion),
             int(tiene_factura), adjunto, adjunto_nombre, adjunto_tipo, _ahora(), eid, mov_id),
        )
        return _fila(_obtener(conn, mov_id))


@app.delete("/api/movimientos/{mov_id}", status_code=204)
def borrar(mov_id: int):
    with db.connect() as conn:
        actual = _obtener(conn, mov_id)
        conn.execute("DELETE FROM movimientos WHERE id = ?", (mov_id,))
    _borrar_adjunto(actual["adjunto_archivo"])
    return Response(status_code=204)


@app.get("/api/movimientos/{mov_id}/adjunto")
def descargar_adjunto(mov_id: int):
    with db.connect() as conn:
        row = _obtener(conn, mov_id)
    if not row["adjunto_archivo"]:
        raise HTTPException(404, "Este movimiento no tiene factura adjunta")
    ruta = db.UPLOADS_DIR / row["adjunto_archivo"]
    if not ruta.exists():
        raise HTTPException(404, "Archivo no encontrado")
    return FileResponse(ruta, media_type=row["adjunto_tipo"], filename=row["adjunto_nombre"],
                        content_disposition_type="inline")


# ---------------------------------------------------------------- Lectura de facturas

def _completar_con_historial(conn, empresa_id: int, datos: dict) -> None:
    """Usa lo ya cargado: el nombre corregido de esa contraparte y su categoría más habitual en la empresa."""
    cuit, tercero = datos.get("cuit"), datos.get("tercero")
    if cuit:
        row = conn.execute("SELECT tercero FROM movimientos WHERE cuit = ? AND tercero IS NOT NULL "
                           "ORDER BY empresa_id = ? DESC, revisar ASC, id DESC LIMIT 1", (cuit, empresa_id)).fetchone()
        if row:
            datos["tercero"] = tercero = row["tercero"]
    if not (cuit or tercero):
        return
    row = conn.execute(
        """SELECT categoria, COUNT(*) n FROM movimientos
           WHERE empresa_id = ? AND categoria IS NOT NULL
             AND ((? IS NOT NULL AND cuit = ?) OR (? IS NOT NULL AND tercero = ?))
           GROUP BY categoria ORDER BY n DESC, MAX(id) DESC LIMIT 1""",
        (empresa_id, cuit, cuit, tercero, tercero)).fetchone()
    if row:
        datos["categoria"] = row["categoria"]


def _leer_y_completar(contenido: bytes, tipo_archivo: str, empresa: int | None) -> dict:
    """Lee el comprobante y decide a qué empresa pertenece: si aparece el CUIT de otra de tus
    empresas, va a esa; si no, a la que estás viendo."""
    crudo, metodo, aviso = extractor.leer_crudo(contenido, tipo_archivo)
    if crudo is None:
        return {"metodo": "ninguno", "aviso": aviso}
    with db.connect() as conn:
        actual = _empresa_id(conn, empresa)
        empresas = sorted(_empresas(conn), key=lambda e: e["id"] != actual)  # la actual primero
        elegida, datos = None, None
        for e in empresas:
            r = extractor.resolver(crudo, e["cuits_propios"])
            if r.get("cuit_propio"):
                elegida, datos = e, r
                break
        if elegida is None:
            elegida = empresas[0]
            datos = extractor.resolver(crudo, elegida["cuits_propios"])
            # Solo se sugiere un CUIT que no esté ya asignado a otra empresa
            todos = {c for e in empresas for c in e["cuits_propios"]}
            if any(e["cuits_propios"] for e in empresas[:1]) or datos.get("sugerencia_cuit_propio", {}).get("cuit") in todos:
                datos.pop("sugerencia_cuit_propio", None)
        datos.pop("cuit_propio", None)
        datos.update(metodo=metodo, empresa_id=elegida["id"], empresa_nombre=elegida["nombre"])
        _completar_con_historial(conn, elegida["id"], datos)
    return datos


@app.post("/api/leer-factura")
async def leer_factura(archivo: UploadFile = File(...), empresa: int | None = None):
    contenido, tipo = await _leer_adjunto(archivo)
    return _leer_y_completar(contenido, tipo, empresa)


@app.post("/api/carga-automatica")
async def carga_automatica(archivo: UploadFile = File(...), forzar: bool = Form(False), empresa: int | None = None):
    """Lee el comprobante y, si encuentra el monto, guarda el movimiento sin pedir nada más."""
    contenido, tipo_archivo = await _leer_adjunto(archivo)
    datos = _leer_y_completar(contenido, tipo_archivo, empresa)
    respuesta = {"datos": datos, "archivo": archivo.filename}
    if datos.get("sugerencia_cuit_propio"):
        respuesta["sugerencia_cuit_propio"] = datos["sugerencia_cuit_propio"]

    if not datos.get("monto"):
        return {**respuesta, "estado": "incompleto",
                "mensaje": datos.get("aviso") or "No se encontró el monto. Completalo a mano."}

    eid = datos["empresa_id"]
    with db.connect() as conn:
        if not forzar and datos.get("numero_factura"):
            dup = conn.execute(
                "SELECT * FROM movimientos WHERE empresa_id = ? AND numero_factura = ? AND ABS(monto - ?) < 0.01 "
                "AND moneda = ? LIMIT 1",
                (eid, datos["numero_factura"], datos["monto"], datos.get("moneda", "ARS"))).fetchone()
            if dup:
                return {**respuesta, "estado": "duplicado", "movimiento": _fila(dup),
                        "mensaje": "Ya estaba cargado (mismo n.º y monto)."}
        adjunto = (_guardar_adjunto(contenido, archivo.filename), archivo.filename, tipo_archivo)
        mov_id = _insertar(conn, eid, datos, adjunto, origen="automatica", revisar=True)
        return {**respuesta, "estado": "creado", "movimiento": _fila(_obtener(conn, mov_id))}


# ---------------------------------------------------------------- Empresas y ajustes

def _normalizar_cuits(cuits) -> list[str]:
    if isinstance(cuits, str):
        cuits = re.split(r"[,;\s]+", cuits)
    normalizados = []
    for c in cuits or []:
        if not str(c).strip():
            continue
        n = extractor.norm_cuit(str(c))
        if not n:
            raise HTTPException(422, f"CUIT inválido: {c} (deben ser 11 dígitos)")
        normalizados.append(n)
    return list(dict.fromkeys(normalizados))


def _guardar_empresa(conn, empresa_id: int, nombre: str | None = None, cuits=None) -> None:
    if nombre is not None:
        nombre = nombre.strip()
        if not nombre:
            raise HTTPException(422, "El nombre no puede estar vacío")
        if conn.execute("SELECT 1 FROM empresas WHERE nombre = ? AND id <> ?", (nombre, empresa_id)).fetchone():
            raise HTTPException(409, f"Ya existe una empresa llamada {nombre}")
        conn.execute("UPDATE empresas SET nombre = ? WHERE id = ?", (nombre, empresa_id))
    if cuits is not None:
        cuits = _normalizar_cuits(cuits)
        for e in _empresas(conn):
            repetido = set(cuits) & set(e["cuits_propios"])
            if e["id"] != empresa_id and repetido:
                raise HTTPException(409, f"El CUIT {repetido.pop()} ya está asignado a {e['nombre']}")
        conn.execute("UPDATE empresas SET cuits_propios = ? WHERE id = ?", (",".join(cuits), empresa_id))


@app.get("/api/empresas")
def listar_empresas():
    with db.connect() as conn:
        cant = dict(conn.execute("SELECT empresa_id, COUNT(*) FROM movimientos GROUP BY empresa_id").fetchall())
        return [{**e, "movimientos": cant.get(e["id"], 0)} for e in _empresas(conn)]


@app.post("/api/empresas", status_code=201)
def crear_empresa(cuerpo: dict = Body(...)):
    with db.connect() as conn:
        nombre = (cuerpo.get("nombre") or "").strip()
        if not nombre:
            raise HTTPException(422, "Falta el nombre")
        if conn.execute("SELECT 1 FROM empresas WHERE nombre = ?", (nombre,)).fetchone():
            raise HTTPException(409, f"Ya existe una empresa llamada {nombre}")
        eid = conn.execute("INSERT INTO empresas (nombre) VALUES (?)", (nombre,)).lastrowid
        _guardar_empresa(conn, eid, cuits=cuerpo.get("cuits_propios", []))
        return next(e for e in _empresas(conn) if e["id"] == eid)


@app.put("/api/empresas/{empresa_id}")
def editar_empresa(empresa_id: int, cuerpo: dict = Body(...)):
    with db.connect() as conn:
        eid = _empresa_id(conn, empresa_id)
        _guardar_empresa(conn, eid, cuerpo.get("nombre"), cuerpo.get("cuits_propios"))
        return next(e for e in _empresas(conn) if e["id"] == eid)


@app.delete("/api/empresas/{empresa_id}", status_code=204)
def borrar_empresa(empresa_id: int):
    with db.connect() as conn:
        eid = _empresa_id(conn, empresa_id)
        if conn.execute("SELECT 1 FROM movimientos WHERE empresa_id = ? LIMIT 1", (eid,)).fetchone():
            raise HTTPException(409, "La empresa tiene movimientos: movelos o borralos antes")
        if conn.execute("SELECT COUNT(*) FROM empresas").fetchone()[0] <= 1:
            raise HTTPException(409, "Tiene que quedar al menos una empresa")
        conn.execute("DELETE FROM empresas WHERE id = ?", (eid,))
    return Response(status_code=204)


@app.get("/api/ajustes")
def ver_ajustes(empresa: int | None = None):
    with db.connect() as conn:
        eid = _empresa_id(conn, empresa)
        e = next(x for x in _empresas(conn) if x["id"] == eid)
        return {"empresa_id": eid, "empresa": e["nombre"], "cuits_propios": e["cuits_propios"],
                "ocr_local": extractor.ocr_disponible(), "ia": bool(os.environ.get("ANTHROPIC_API_KEY"))}


@app.put("/api/ajustes")
def guardar_ajustes(cuerpo: dict = Body(...), empresa: int | None = None):
    with db.connect() as conn:
        eid = _empresa_id(conn, empresa)
        _guardar_empresa(conn, eid, cuits=cuerpo.get("cuits_propios", []))
    return ver_ajustes(eid)


# ---------------------------------------------------------------- Exportación

@app.get("/api/exportar.csv")
def exportar(empresa: int | None = None, tipo: str | None = None, desde: str | None = None,
             hasta: str | None = None, q: str | None = None, categoria: str | None = None):
    filas = listar(empresa, tipo, desde, hasta, q, categoria, limite=5000)
    with db.connect() as conn:
        nombre = conn.execute("SELECT nombre FROM empresas WHERE id = ?", (_empresa_id(conn, empresa),)).fetchone()[0]
    columnas = ["id", "tipo", "fecha_factura", "fecha_carga", "monto", "moneda", "numero_factura", "tercero",
                "cuit", "categoria", "descripcion", "tiene_factura", "adjunto_nombre"]
    buf = io.StringIO()
    buf.write("﻿")  # BOM para que Excel respete los acentos
    w = csv.DictWriter(buf, fieldnames=columnas, extrasaction="ignore", delimiter=";")
    w.writeheader()
    for f in filas:
        w.writerow({**f, "monto": f"{f['monto']:.2f}".replace(".", ","), "tiene_factura": "Sí" if f["tiene_factura"] else "No"})
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="movimientos_{_archivo_seguro(nombre)}.csv"'})


def _archivo_seguro(nombre: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", nombre).strip("_").lower() or "empresa"


def _vacio(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


# ---------------------------------------------------------------- Dashboard

SEGMENTOS = {
    "categoria": "COALESCE(NULLIF(TRIM(categoria), ''), 'Sin categoría')",
    "tercero": "COALESCE(NULLIF(TRIM(tercero), ''), 'Sin proveedor/cliente')",
    "factura": """CASE WHEN adjunto_archivo IS NOT NULL THEN 'Con factura adjunta'
                       WHEN tiene_factura = 1 THEN 'Con n.º de factura'
                       ELSE 'Manual (sin factura)' END""",
}
MAX_SEGMENTOS = 8  # el resto se agrupa en "Otros"


def _meses_entre(desde: date, hasta: date) -> list[str]:
    meses, y, m = [], desde.year, desde.month
    while (y, m) <= (hasta.year, hasta.month):
        meses.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return meses


def _rango_por_defecto() -> tuple[date, date]:
    """Últimos 12 meses, incluyendo el actual."""
    hoy = date.today()
    y, m = hoy.year, hoy.month - 11
    if m <= 0:
        y, m = y - 1, m + 12
    fin = date(hoy.year + (hoy.month == 12), hoy.month % 12 + 1, 1) - timedelta(days=1)
    return date(y, m, 1), fin


def _datos_dashboard(empresa, desde, hasta, moneda, segmento):
    if segmento not in SEGMENTOS:
        raise HTTPException(422, f"segmento debe ser uno de: {', '.join(SEGMENTOS)}")
    d_def, h_def = _rango_por_defecto()
    d = date.fromisoformat(_validar_fecha(desde)) if desde else d_def
    h = date.fromisoformat(_validar_fecha(hasta)) if hasta else h_def
    if d > h:
        raise HTTPException(422, "'desde' no puede ser posterior a 'hasta'")
    d = d.replace(day=1)
    meses = _meses_entre(d, h)
    if len(meses) > 120:
        raise HTTPException(422, "El rango máximo es de 10 años")

    with db.connect() as conn:
        eid = _empresa_id(conn, empresa)
        nombre_empresa = conn.execute("SELECT nombre FROM empresas WHERE id = ?", (eid,)).fetchone()[0]
        monedas = [r["moneda"] for r in conn.execute(
            "SELECT moneda, COUNT(*) c FROM movimientos WHERE empresa_id = ? GROUP BY moneda ORDER BY c DESC",
            (eid,)).fetchall()]
        moneda = (moneda or (monedas[0] if monedas else "ARS")).upper()
        base = "FROM movimientos WHERE empresa_id = ? AND moneda = ? AND fecha_factura BETWEEN ? AND ?"
        params = (eid, moneda, d.isoformat(), h.isoformat())
        por_mes = conn.execute(
            f"SELECT substr(fecha_factura, 1, 7) mes, tipo, SUM(monto) total, COUNT(*) c {base} GROUP BY mes, tipo",
            params).fetchall()
        seg_rows = conn.execute(
            f"SELECT {SEGMENTOS[segmento]} seg, tipo, substr(fecha_factura, 1, 7) mes, SUM(monto) total, COUNT(*) c "
            f"{base} GROUP BY seg, tipo, mes", params).fetchall()

    idx = {m: i for i, m in enumerate(meses)}
    filas_mes = [{"mes": m, "ingresos": 0.0, "egresos": 0.0, "cantidad": 0} for m in meses]
    for r in por_mes:
        f = filas_mes[idx[r["mes"]]]
        f["ingresos" if r["tipo"] == "ingreso" else "egresos"] += r["total"]
        f["cantidad"] += r["c"]
    acumulado = 0.0
    for f in filas_mes:
        f["ingresos"], f["egresos"] = round(f["ingresos"], 2), round(f["egresos"], 2)
        f["saldo"] = round(f["ingresos"] - f["egresos"], 2)
        acumulado += f["saldo"]
        f["acumulado"] = round(acumulado, 2)

    ingresos = round(sum(f["ingresos"] for f in filas_mes), 2)
    egresos = round(sum(f["egresos"] for f in filas_mes), 2)

    segmentos = {}
    for tipo in ("ingreso", "egreso"):
        agregados: dict[str, dict] = {}
        for r in seg_rows:
            if r["tipo"] != tipo:
                continue
            a = agregados.setdefault(r["seg"], {"nombre": r["seg"], "total": 0.0, "cantidad": 0, "meses": [0.0] * len(meses)})
            a["total"] += r["total"]
            a["cantidad"] += r["c"]
            a["meses"][idx[r["mes"]]] += r["total"]
        orden = sorted(agregados.values(), key=lambda a: -a["total"])
        if len(orden) > MAX_SEGMENTOS:
            resto = orden[MAX_SEGMENTOS - 1:]
            otros = {"nombre": f"Otros ({len(resto)})", "total": sum(a["total"] for a in resto),
                     "cantidad": sum(a["cantidad"] for a in resto),
                     "meses": [sum(a["meses"][i] for a in resto) for i in range(len(meses))]}
            orden = orden[:MAX_SEGMENTOS - 1] + [otros]
        total_tipo = ingresos if tipo == "ingreso" else egresos
        for a in orden:
            a["total"] = round(a["total"], 2)
            a["meses"] = [round(v, 2) for v in a["meses"]]
            a["pct"] = round(100 * a["total"] / total_tipo, 1) if total_tipo else 0.0
        segmentos[tipo] = orden

    return {
        "empresa_id": eid,
        "empresa": nombre_empresa,
        "moneda": moneda,
        "monedas": monedas or ["ARS"],
        "desde": d.isoformat(),
        "hasta": h.isoformat(),
        "segmento": segmento,
        "totales": {
            "ingresos": ingresos,
            "egresos": egresos,
            "saldo": round(ingresos - egresos, 2),
            "margen": round(100 * (ingresos - egresos) / ingresos, 1) if ingresos else None,
            "cantidad": sum(f["cantidad"] for f in filas_mes),
            "promedio_egresos": round(egresos / len(meses), 2),
        },
        "meses": filas_mes,
        "segmentos": segmentos,
    }


@app.get("/api/dashboard")
def dashboard(empresa: int | None = None, desde: str | None = None, hasta: str | None = None,
              moneda: str | None = None, segmento: str = "categoria"):
    return _datos_dashboard(empresa, desde, hasta, moneda, segmento)


@app.get("/api/dashboard.csv")
def dashboard_csv(empresa: int | None = None, desde: str | None = None, hasta: str | None = None,
                  moneda: str | None = None, segmento: str = "categoria"):
    """Hoja de resumen mensual + desglose por segmento, lista para Excel."""
    d = _datos_dashboard(empresa, desde, hasta, moneda, segmento)
    num = lambda v: f"{v:.2f}".replace(".", ",")
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    meses = [f["mes"] for f in d["meses"]]
    w.writerow([d["empresa"], f"Resumen mensual ({d['moneda']})", f"{d['desde']} a {d['hasta']}"])
    w.writerow(["Mes", "Ingresos", "Egresos", "Saldo", "Saldo acumulado", "Movimientos"])
    for f in d["meses"]:
        w.writerow([f["mes"], num(f["ingresos"]), num(f["egresos"]), num(f["saldo"]), num(f["acumulado"]), f["cantidad"]])
    t = d["totales"]
    w.writerow(["Total", num(t["ingresos"]), num(t["egresos"]), num(t["saldo"]), "", t["cantidad"]])
    for tipo, titulo in (("ingreso", "Ingresos"), ("egreso", "Egresos")):
        w.writerow([])
        w.writerow([f"{titulo} por {segmento}"])
        w.writerow(["Segmento", *meses, "Total", "%"])
        for s in d["segmentos"][tipo]:
            w.writerow([s["nombre"], *map(num, s["meses"]), num(s["total"]), num(s["pct"])])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition":
                             f'attachment; filename="resumen_mensual_{_archivo_seguro(d["empresa"])}.csv"'})


# ---------------------------------------------------------------- Frontend

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
