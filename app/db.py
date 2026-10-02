"""Acceso a la base de datos SQLite."""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DATA_DIR = Path(os.environ.get("CSA_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = DATA_DIR / "csa.db"
UPLOADS_DIR = DATA_DIR / "facturas"
EMPRESAS_INICIALES = ("Solvencias", "Agencia")

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
CREATE TABLE IF NOT EXISTS empresas (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre        TEXT    NOT NULL UNIQUE,
    cuits_propios TEXT    NOT NULL DEFAULT ''     -- CUIT de la empresa, separados por coma
);
CREATE TABLE IF NOT EXISTS ajustes (
    clave TEXT PRIMARY KEY,
    valor TEXT
);
CREATE INDEX IF NOT EXISTS idx_mov_fecha ON movimientos (fecha_factura);
CREATE INDEX IF NOT EXISTS idx_mov_tipo  ON movimientos (tipo);
"""


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as conn:
        conn.executescript(SCHEMA)
        _migrar(conn)


def _migrar(conn) -> None:
    """Agrega columnas nuevas a bases creadas con versiones anteriores."""
    columnas = {r["name"] for r in conn.execute("PRAGMA table_info(movimientos)")}
    if "origen" not in columnas:  # 'manual' o 'automatica'
        conn.execute("ALTER TABLE movimientos ADD COLUMN origen TEXT NOT NULL DEFAULT 'manual'")
    if "revisar" not in columnas:  # 1 = cargado automáticamente y todavía no revisado
        conn.execute("ALTER TABLE movimientos ADD COLUMN revisar INTEGER NOT NULL DEFAULT 0")
    if "desglose" not in columnas:  # JSON: {"bruto": 100, "items": [{"concepto": "Ret. IVA", "importe": -10}]}
        conn.execute("ALTER TABLE movimientos ADD COLUMN desglose TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mov_cuit ON movimientos (cuit)")

    # Varias empresas: cada movimiento pertenece a una
    if not conn.execute("SELECT 1 FROM empresas LIMIT 1").fetchone():
        for nombre in EMPRESAS_INICIALES:
            conn.execute("INSERT INTO empresas (nombre) VALUES (?)", (nombre,))
        # El CUIT configurado antes de que existieran las empresas pasa a la primera
        cuits = leer_ajuste(conn, "cuits_propios")
        if cuits:
            conn.execute("UPDATE empresas SET cuits_propios = ? WHERE id = (SELECT MIN(id) FROM empresas)", (cuits,))
    if "empresa_id" not in columnas:
        conn.execute("ALTER TABLE movimientos ADD COLUMN empresa_id INTEGER REFERENCES empresas (id)")
    # Lo cargado antes de separar por empresa queda en la primera (se puede mover desde la edición)
    conn.execute("UPDATE movimientos SET empresa_id = (SELECT MIN(id) FROM empresas) WHERE empresa_id IS NULL")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_mov_empresa ON movimientos (empresa_id, fecha_factura)")


def leer_ajuste(conn, clave: str, defecto: str = "") -> str:
    row = conn.execute("SELECT valor FROM ajustes WHERE clave = ?", (clave,)).fetchone()
    return row["valor"] if row and row["valor"] is not None else defecto


def guardar_ajuste(conn, clave: str, valor: str) -> None:
    conn.execute("INSERT INTO ajustes (clave, valor) VALUES (?, ?) "
                 "ON CONFLICT(clave) DO UPDATE SET valor = excluded.valor", (clave, valor))


@contextmanager
def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()
