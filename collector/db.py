"""Capa de acceso a base de datos (SQLite) para gasolina-gt.

Tablas:
  - precios        — todo: R, S, D, wti (GTQ/Gal o USD/Bbl)
  - noticias       — headlines RSS clasificados
  - ejecuciones    — log de runs de colectores

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
# Creación de tablas
# ──────────────────────────────────────────────

def crear_tablas(conn: sqlite3.Connection) -> None:
    """Crea las tablas si no existen."""
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

        DROP TABLE IF EXISTS noticias;
        CREATE TABLE noticias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            titulo TEXT NOT NULL,
            medio TEXT NOT NULL,
            publicado_at TEXT,
            categoria TEXT,
            pais TEXT,
            relevancia INTEGER CHECK(relevancia BETWEEN 1 AND 5),
            resumen_es TEXT,
            fetched_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_noticias_url
            ON noticias(url);

        DROP TABLE IF EXISTS ejecuciones;
        CREATE TABLE ejecuciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inicio TEXT NOT NULL,
            fin TEXT,
            modulo TEXT NOT NULL,
            ok INTEGER DEFAULT 1,
            mensaje TEXT
        );
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
# Inserciones — precios (insert or ignore — idempotentes)
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


def insertar_noticia(
    conn: sqlite3.Connection,
    url: str,
    titulo: str,
    medio: str,
    publicado_at: str = None,
    categoria: str = None,
    pais: str = None,
    relevancia: int = None,
    resumen_es: str = None,
) -> int | None:
    """Inserta o ignora una noticia.

    Returns:
        id del registro insertado, o None si ya existía por URL duplicada.
    """
    fetched_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")
    cursor = conn.execute(
        "INSERT OR IGNORE INTO noticias (url, titulo, medio, publicado_at, categoria, pais, relevancia, resumen_es, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (url, titulo, medio, publicado_at, categoria, pais, relevancia, resumen_es, fetched_at),
    )
    conn.commit()
    return cursor.lastrowid if cursor.rowcount > 0 else None


def insertar_ejecucion(
    conn: sqlite3.Connection,
    modulo: str,
    ok: int = 1,
    mensaje: str = "",
) -> int:
    """Registra una ejecución del colector."""
    inicio = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")
    cursor = conn.execute(
        "INSERT INTO ejecuciones (inicio, modulo, ok, mensaje) VALUES (?, ?, ?, ?)",
        (inicio, modulo, ok, mensaje),
    )
    conn.commit()
    exec_id = cursor.lastrowid
    # Actualizar fin después
    conn.execute(
        "UPDATE ejecuciones SET fin = ? WHERE id = ?",
        (datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00"), exec_id),
    )
    conn.commit()
    return exec_id


# ──────────────────────────────────────────────
# Consultas — precios
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


# ──────────────────────────────────────────────
# Consultas — noticias
# ──────────────────────────────────────────────

def obtener_noticias(
    conn: sqlite3.Connection, dias: int = 30, categoria: str = None
) -> list[sqlite3.Row]:
    """Obtiene noticias relevantes de los últimos N días."""
    query = "SELECT * FROM noticias WHERE publicado_at >= date('now', ?)"
    params: list = [f"-{dias} days"]

    if categoria:
        query += " AND categoria = ?"
        params.append(categoria)

    query += " ORDER BY publicado_at DESC"
    rows = conn.execute(query, params).fetchall()
    return rows


def obtener_noticia_por_url(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    """Verifica si una noticia ya existe por URL."""
    row = conn.execute(
        "SELECT * FROM noticias WHERE url = ?", (url,)
    ).fetchone()
    return row


# ──────────────────────────────────────────────
# Consultas — ejecuciones
# ──────────────────────────────────────────────

def obtener_ultimas_ejecuciones(
    conn: sqlite3.Connection, limite: int = 10
) -> list[sqlite3.Row]:
    """Obtiene las últimas N ejecuciones."""
    rows = conn.execute(
        "SELECT * FROM ejecuciones ORDER BY inicio DESC LIMIT ?", (limite,)
    ).fetchall()
    return rows
