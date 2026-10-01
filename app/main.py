"""API y servidor web del sistema de ingresos y gastos."""
import base64
import csv
import io
import os
import secrets
import uuid
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import db, extractor

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
    return d


def _obtener(conn, mov_id: int):
    row = conn.execute("SELECT * FROM movimientos WHERE id = ?", (mov_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Movimiento no encontrado")
    return row


def _filtros(tipo, desde, hasta, q, categoria):
    where, params = [], []
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
    return (" WHERE " + " AND ".join(where)) if where else "", params


# ---------------------------------------------------------------- Movimientos

@app.get("/api/movimientos")
def listar(tipo: str | None = None, desde: str | None = None, hasta: str | None = None,
           q: str | None = None, categoria: str | None = None, limite: int = 500):
    where, params = _filtros(tipo, desde, hasta, q, categoria)
    with db.connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM movimientos{where} ORDER BY fecha_factura DESC, id DESC LIMIT ?",
            (*params, max(1, min(limite, 5000))),
        ).fetchall()
    return [_fila(r) for r in rows]


@app.get("/api/resumen")
def resumen(tipo: str | None = None, desde: str | None = None, hasta: str | None = None,
            q: str | None = None, categoria: str | None = None):
    where, params = _filtros(tipo, desde, hasta, q, categoria)
    with db.connect() as conn:
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
def categorias():
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT categoria FROM movimientos WHERE categoria IS NOT NULL AND categoria <> '' ORDER BY categoria"
        ).fetchall()
    return [r["categoria"] for r in rows]


@app.get("/api/movimientos/{mov_id}")
def obtener(mov_id: int):
    with db.connect() as conn:
        return _fila(_obtener(conn, mov_id))


@app.post("/api/movimientos", status_code=201)
async def crear(
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

    tiene_factura = bool(adjunto or (numero_factura or "").strip())
    with db.connect() as conn:
        cur = conn.execute(
            """INSERT INTO movimientos (tipo, monto, moneda, fecha_factura, fecha_carga, numero_factura, tercero,
                   cuit, categoria, descripcion, tiene_factura, adjunto_archivo, adjunto_nombre, adjunto_tipo)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (_validar_tipo(tipo), _validar_monto(monto), (moneda or "ARS").upper()[:5], _validar_fecha(fecha_factura),
             _ahora(), _vacio(numero_factura), _vacio(tercero), _vacio(cuit), _vacio(categoria),
             _vacio(descripcion), int(tiene_factura), adjunto, adjunto_nombre, adjunto_tipo),
        )
        return _fila(_obtener(conn, cur.lastrowid))


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
    archivo: UploadFile | None = File(None),
):
    with db.connect() as conn:
        actual = _obtener(conn, mov_id)
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
                   actualizado=? WHERE id=?""",
            (_validar_tipo(tipo), _validar_monto(monto), (moneda or "ARS").upper()[:5], _validar_fecha(fecha_factura),
             _vacio(numero_factura), _vacio(tercero), _vacio(cuit), _vacio(categoria), _vacio(descripcion),
             int(tiene_factura), adjunto, adjunto_nombre, adjunto_tipo, _ahora(), mov_id),
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

@app.post("/api/leer-factura")
async def leer_factura(archivo: UploadFile = File(...)):
    contenido, tipo = await _leer_adjunto(archivo)
    return extractor.extraer(contenido, tipo)


# ---------------------------------------------------------------- Exportación

@app.get("/api/exportar.csv")
def exportar(tipo: str | None = None, desde: str | None = None, hasta: str | None = None,
             q: str | None = None, categoria: str | None = None):
    filas = listar(tipo, desde, hasta, q, categoria, limite=5000)
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
                             headers={"Content-Disposition": 'attachment; filename="movimientos.csv"'})


def _vacio(v: str | None) -> str | None:
    v = (v or "").strip()
    return v or None


# ---------------------------------------------------------------- Frontend

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
