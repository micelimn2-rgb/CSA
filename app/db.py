"""Acceso a la base de datos SQLite."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("CSA_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "csa.db"
UPLOADS_DIR = DATA_DIR / "facturas"

SCHEMA = """
CREATE TABLE IF NOT EXISTS movimientos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo            TEXT    NOT NULL CHECK (tipo IN ('ingreso', 'egreso')),
    monto           REAL    NOT NULL CHECK (monto >= 0),
    moneda          TEXT    NOT NULL DEFAULT 'ARS',
    fecha_factura   TEXT    NOT NULL,              -- YYYY-MM-DD
    fecha_carga     TEXT    NOT NULL,              -- YYYY-MM-DD HH:MM:SS (automática)
    numero_factura  TEXT,
    tercero         TEXT,                          -- proveedor o cliente
    cuit            TEXT,
    categoria       TEXT,
    descripcion     TEXT,                          -- detalle / referencias
    tiene_factura   INTEGER NOT NULL DEFAULT 0,
    adjunto_archivo TEXT,                          -- nombre del archivo guardado
    adjunto_nombre  TEXT,                          -- nombre original
    adjunto_tipo    TEXT,
    actualizado     TEXT
);
CREATE INDEX IF NOT EXISTS idx_mov_fecha ON movimientos (fecha_factura);
CREATE INDEX IF NOT EXISTS idx_mov_tipo  ON movimientos (tipo);
"""


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
