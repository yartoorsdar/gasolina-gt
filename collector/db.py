"""Capa de acceso a base de datos (SQLite) para gasolina-gt.

Una sola tabla: precios
  - id, fecha, producto, precio, fuente, fetched_at

Productos: 'superior', 'regular', 'diésel' (combustible GTQ/Gal)
           'brent', 'wti' (petróleo USD/Bbl)

Insert or ignore para ser idempotente.
"""

import sqlite3
from datetime import date, datetime
from pathlib import Path


# ──────────────────────────────────────────────
# Rutas por defecto
# ──────────────────────────────────────────────

def _default_db_path() -> str:
    """Ruta a la base de datos en data/historial.db."""
    db_dir = Path(__file__).resolve().parent.parent / "data"
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "historial.db")


# ──────────────────────────────────────────────
# Creación de tablas — UNA SOLA TABLA
# ──────────────────────────────────────────────

def crear_tablas(conn: sqlite3.Connection) -> None:
    """Crea la única tabla si no existe."""
    conn.executescript("""
        DROP TABLE IF EXISTS precios;
        CREATE TABLE precios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            producto TEXT NOT NULL,
            precio REAL NOT NULL,
            fuente TEXT NOT NULL DEFAULT 'manual',
            fetched_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_precios_fecha_prod
            ON precios(fecha, producto);
    """)


# ──────────────────────────────────────────────
# Helpers de conexión
# ──────────────────────────────────────────────

def conectar(db_path: str = None) -> sqlite3.Connection:
    """Abre una conexión a la base de datos y asegura tablas creadas."""
    if db_path is None:
        db_path = _default_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # acceso por nombre de columna
    crear_tablas(conn)
    return conn


def conectar_temporal() -> sqlite3.Connection:
    """Conecta a una base temporal en memoria (ideal para tests)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    crear_tablas(conn)
    return conn


# ──────────────────────────────────────────────
# Inserciones (insert or ignore — idempotentes)
# ──────────────────────────────────────────────

def insertar_precio(
    conn: sqlite3.Connection,
    fecha: str,
    producto: str,
    precio: float,
    fuente: str = "manual",
) -> int | None:
    """Inserta o ignora un precio (combustible o petróleo).

    Returns:
        id del registro insertado, o None si ya existía (duplicado).
    """
    fetched_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")
    cursor = conn.execute(
        "INSERT OR IGNORE INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
        (fecha, producto, precio, fuente, fetched_at),
    )
    conn.commit()
    row = obtener_precio(conn, fecha, producto)
    return row["id"] if row else None


# ──────────────────────────────────────────────
# Consultas
# ──────────────────────────────────────────────

def obtener_precios_actuales(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Obtiene el precio más reciente por cada producto.

    Ordena por fecha DESC, toma solo el primero por producto.
    """
    rows = conn.execute(
        "SELECT * FROM precios ORDER BY fecha DESC, id DESC"
    ).fetchall()

    seen = set()
    result = []
    for row in rows:
        if row["producto"] not in seen:
            seen.add(row["producto"])
            result.append(row)
    return result


def obtener_historial_precios(
    conn: sqlite3.Connection,
    producto: str = None,
    dias: int = 30,
) -> list[sqlite3.Row]:
    """Obtiene el historial de precios, opcionalmente filtrado por producto y días."""
    query = "SELECT * FROM precios WHERE 1=1"
    params: list = []

    if producto:
        query += " AND producto = ?"
        params.append(producto)

    if dias and dias > 0:
        query += " AND fecha >= date('now', ?)"
        params.append(f"-{dias} days")

    query += " ORDER BY fecha, producto"
    rows = conn.execute(query, params).fetchall()
    return rows


def obtener_ultimo_precio(conn: sqlite3.Connection, producto: str) -> sqlite3.Row | None:
    """Obtiene el último precio disponible de un producto específico."""
    row = conn.execute(
        "SELECT * FROM precios WHERE producto = ? ORDER BY fecha DESC LIMIT 1",
        (producto,),
    ).fetchone()
    return row


def obtener_precio(conn: sqlite3.Connection, fecha: str, producto: str) -> sqlite3.Row | None:
    """Obtiene un precio específico por fecha y producto."""
    row = conn.execute(
        "SELECT * FROM precios WHERE fecha = ? AND producto = ?",
        (fecha, producto),
    ).fetchone()
    return row
