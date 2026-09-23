"""Capa de acceso a base de datos (SQLite) para gasolina-gt.

Tablas:
  - precios_combustible
  - precios_petroleo
  - noticias
  - ejecuciones

Todas las funciones aceptan una conexión o crean una temporal.
Las inserciones son "insert or ignore" para ser idempotentes.
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
    """Crea todas las tablas si no existen.

    Tabla precios_combustible:
      - id, fecha_observacion, producto, precio, incluye_impuestos (0/1)
      - regimen (texto), nota (nullable)
      - fuente, url, fetched_at
      - UNIQUE(fecha_observacion, producto, fuente)

    Tabla precios_petroleo:
      - id, fecha, referencia (brent|wti|...), usd_barril
      - fuente, url, fetched_at
      - UNIQUE(fecha, referencia)

    Tabla noticias:
      - id, url (UNIQUE), titulo, medio, publicado_at
      - categoria, pais, relevancia, resumen_es, fetched_at

    Tabla ejecuciones:
      - id, inicio, fin, modulo, ok, mensaje
    """
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS precios_combustible (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha_observacion TEXT NOT NULL,
            producto TEXT NOT NULL CHECK(producto IN ('superior', 'regular', 'diessel')),
            precio REAL NOT NULL,
            incluye_impuestos INTEGER NOT NULL DEFAULT 1,
            regimen TEXT NOT NULL DEFAULT 'normal',
            nota TEXT,
            fuente TEXT NOT NULL,
            url TEXT,
            fetched_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_precios_comb_fecha_prod_fuent
            ON precios_combustible(fecha_observacion, producto, fuente);

        CREATE TABLE IF NOT EXISTS precios_petroleo (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            referencia TEXT NOT NULL,
            usd_barril REAL NOT NULL,
            fuente TEXT NOT NULL,
            url TEXT,
            fetched_at TEXT NOT NULL
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_petroleo_fecha_ref
            ON precios_petroleo(fecha, referencia);

        CREATE TABLE IF NOT EXISTS noticias (
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

        CREATE TABLE IF NOT EXISTS ejecuciones (
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
# Inserciones (insert or ignore — idempotentes)
# ──────────────────────────────────────────────

def insertar_precio_combustible(
    conn: sqlite3.Connection,
    fecha_obs: str,
    producto: str,
    precio: float,
    incluye_impuestos: int = 1,
    regimen: str = "normal",
    nota: str = None,
    fuente: str = "",
    url: str = "",
) -> int | None:
    """Inserta o ignora un precio de combustible.

    Returns:
        id del registro insertado, o None si ya existía (duplicado).
    """
    fetched_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")
    cursor = conn.execute(
        """INSERT OR IGNORE INTO precios_combustible
           (fecha_observacion, producto, precio, incluye_impuestos, regimen, nota, fuente, url, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (fecha_obs, producto, precio, incluye_impuestos, regimen, nota, fuente, url, fetched_at),
    )
    # SQLite no retorna el id del IGNORE. Consultamos después.
    conn.commit()
    row = obtener_precio(conn, fecha_obs, producto, fuente)
    return row["id"] if row else None


def insertar_precio_petroleo(
    conn: sqlite3.Connection,
    fecha: str,
    referencia: str,
    usd_barril: float,
    fuente: str = "",
    url: str = "",
) -> int | None:
    """Inserta o ignora un precio de petróleo.

    Returns:
        id del registro insertado, o None si ya existía.
    """
    fetched_at = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")
    conn.execute(
        """INSERT OR IGNORE INTO precios_petroleo
           (fecha, referencia, usd_barril, fuente, url, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (fecha, referencia, usd_barril, fuente, url, fetched_at),
    )
    conn.commit()
    row = obtener_ultimo_petroleo(conn, referencia)
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
        """INSERT OR IGNORE INTO noticias
           (url, titulo, medio, publicado_at, categoria, pais, relevancia, resumen_es, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
# Consultas — precios combustible
# ──────────────────────────────────────────────

def obtener_precio(
    conn: sqlite3.Connection, fecha_obs: str, producto: str, fuente: str
) -> sqlite3.Row | None:
    """Obtiene un precio específico por fecha, producto y fuente."""
    row = conn.execute(
        """SELECT * FROM precios_combustible
           WHERE fecha_observacion = ? AND producto = ? AND fuente = ?""",
        (fecha_obs, producto, fuente),
    ).fetchone()
    return row


def obtener_precios_actuales(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Obtiene el precio más reciente por cada producto.
    
    Si hay múltiples fuentes para la misma fecha, prioriza GPP sobre MEM
    usando el ID más alto (GPP se inserta después de MEM).
    """
    rows = conn.execute(
        """SELECT pc.* FROM precios_combustible pc
           INNER JOIN (
               SELECT producto, MAX(fecha_observacion) as max_fecha
               FROM precios_combustible
               GROUP BY producto
           ) latest ON pc.producto = latest.producto AND pc.fecha_observacion = latest.max_fecha
           ORDER BY pc.id DESC"""
    ).fetchall()
    
    # Tomar solo el primer precio por producto (GPP si existe, sino MEM)
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
    query = """SELECT * FROM precios_combustible WHERE 1=1"""
    params: list = []

    if producto:
        query += " AND producto = ?"
        params.append(producto)

    if dias and dias > 0:
        query += " AND fecha_observacion >= date('now', ?)"
        params.append(f"-{dias} days")

    query += " ORDER BY fecha_observacion, producto"
    rows = conn.execute(query, params).fetchall()
    return rows


# ──────────────────────────────────────────────
# Consultas — precios petróleo
# ──────────────────────────────────────────────

def obtener_petroleo_actual(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Obtiene el precio más reciente de cada referencia.
    
    Prioriza OilPriceAPI (tiempo real), luego FRED, luego cualquier otra fuente.
    """
    rows = conn.execute(
        """SELECT pp.* FROM precios_petroleo pp
           INNER JOIN (
               SELECT referencia, MAX(id) as max_id
               FROM precios_petroleo
               WHERE referencia IN ('brent', 'wti')
               GROUP BY referencia
           ) latest ON pp.id = latest.max_id
           ORDER BY pp.referencia"""
    ).fetchall()
    return rows


def obtener_ultimo_petroleo(
    conn: sqlite3.Connection, referencia: str
) -> sqlite3.Row | None:
    """Obtiene el último precio disponible de una referencia específica."""
    row = conn.execute(
        """SELECT * FROM precios_petroleo
           WHERE referencia = ? ORDER BY fecha DESC LIMIT 1""",
        (referencia,),
    ).fetchone()
    return row


def obtener_historial_petroleo(
    conn: sqlite3.Connection,
    referencia: str = None,
    dias: int = 30,
) -> list[sqlite3.Row]:
    """Obtiene el historial de precios de petróleo."""
    query = "SELECT * FROM precios_petroleo WHERE 1=1"
    params: list = []

    if referencia:
        query += " AND referencia = ?"
        params.append(referencia)

    if dias and dias > 0:
        query += " AND fecha >= date('now', ?)"
        params.append(f"-{dias} days")

    query += " ORDER BY fecha DESC, referencia"
    rows = conn.execute(query, params).fetchall()
    return rows


# ──────────────────────────────────────────────
# Consultas — noticias
# ──────────────────────────────────────────────

def obtener_noticias(
    conn: sqlite3.Connection, dias: int = 30, categoria: str = None
) -> list[sqlite3.Row]:
    """Obtiene noticias relevantes de los últimos N días."""
    query = """SELECT * FROM noticias WHERE publicado_at >= date('now', ?)"""
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
