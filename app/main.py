"""API y servidor web del sistema de ingresos y gastos."""
import base64
import csv
import io
import os
import secrets
import uuid
from datetime import date, datetime, timedelta
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


def _datos_dashboard(desde, hasta, moneda, segmento):
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
        monedas = [r["moneda"] for r in conn.execute(
            "SELECT moneda, COUNT(*) c FROM movimientos GROUP BY moneda ORDER BY c DESC").fetchall()]
        moneda = (moneda or (monedas[0] if monedas else "ARS")).upper()
        base = "FROM movimientos WHERE moneda = ? AND fecha_factura BETWEEN ? AND ?"
        params = (moneda, d.isoformat(), h.isoformat())
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
def dashboard(desde: str | None = None, hasta: str | None = None, moneda: str | None = None,
              segmento: str = "categoria"):
    return _datos_dashboard(desde, hasta, moneda, segmento)


@app.get("/api/dashboard.csv")
def dashboard_csv(desde: str | None = None, hasta: str | None = None, moneda: str | None = None,
                  segmento: str = "categoria"):
    """Hoja de resumen mensual + desglose por segmento, lista para Excel."""
    d = _datos_dashboard(desde, hasta, moneda, segmento)
    num = lambda v: f"{v:.2f}".replace(".", ",")
    buf = io.StringIO()
    buf.write("﻿")
    w = csv.writer(buf, delimiter=";")
    meses = [f["mes"] for f in d["meses"]]
    w.writerow([f"Resumen mensual ({d['moneda']})", f"{d['desde']} a {d['hasta']}"])
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
                    headers={"Content-Disposition": 'attachment; filename="resumen_mensual.csv"'})


# ---------------------------------------------------------------- Frontend

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
