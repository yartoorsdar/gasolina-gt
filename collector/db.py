"""Capa de acceso a base de datos (SQLite) para gasolina-gt.

Estructura (una sola forma para todo):
  - productos    — catálogo: superior, regular, diésel, wti (nombre, unidad, archivo…)
  - precios      — UNA fila por (producto, fecha). Todos los productos igual.
  - historial_<archivo> — vista por producto (historial_superior, historial_regular,
                   historial_diesel, historial_wti), generadas desde el catálogo.
  - noticias     — headlines RSS clasificados
  - ejecuciones  — log de runs de colectores

API genérica de precios (cualquier producto, cualquier colector):
  guardar_precios(conn, filas, fuente)   → insertar o actualizar (upsert con prioridad)
  borrar_precios(conn, producto, desde, hasta, fuente)
  leer_historial(conn, producto, desde, hasta)
  leer_ultimo(conn, producto) / leer_actuales(conn)

Regla de conflicto: si dos fuentes traen el mismo (producto, fecha), gana la de
mayor prioridad (MEM oficial > alternas). Re-ejecutar la misma fuente el mismo
día ACTUALIZA el precio — no hace falta borrar antes de insertar.
"""

import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Guatemala = UTC-6 todo el año (sin horario de verano).
# Usar SIEMPRE hora GT tz-aware: el runner (GitHub Actions) tiene reloj UTC,
# y datetime.now() ingenuo etiquetado "-06:00" queda 6h adelantado.
_GT = timezone(timedelta(hours=-6))


def ahora_gt_iso() -> str:
    """Timestamp actual en hora de Guatemala, ISO 8601 con offset -06:00."""
    return datetime.now(_GT).strftime("%Y-%m-%dT%H:%M:%S-06:00")


def hoy_gt() -> str:
    """Fecha actual en Guatemala (YYYY-MM-DD)."""
    return datetime.now(_GT).strftime("%Y-%m-%d")


def hace_dias_gt(dias: int) -> str:
    """Fecha GT de hace N días (YYYY-MM-DD). Sin date('now') de SQLite (es UTC)."""
    return (datetime.now(_GT).date() - timedelta(days=dias)).strftime("%Y-%m-%d")


# ──────────────────────────────────────────────
# Catálogos: productos y fuentes
# ──────────────────────────────────────────────

# Única definición de productos del proyecto. `archivo` = nombre ASCII para
# CSV/vistas (diésel → diesel). `orden` = orden de presentación (R, S, D, WTI).
PRODUCTOS = {
    "regular":  {"nombre": "Regular",  "categoria": "combustible", "unidad": "GTQ/galón",  "archivo": "regular",  "orden": 1},
    "superior": {"nombre": "Superior", "categoria": "combustible", "unidad": "GTQ/galón",  "archivo": "superior", "orden": 2},
    "diésel":   {"nombre": "Diésel",   "categoria": "combustible", "unidad": "GTQ/galón",  "archivo": "diesel",   "orden": 3},
    "wti":      {"nombre": "WTI",      "categoria": "petroleo",    "unidad": "USD/barril", "archivo": "wti",      "orden": 4},
}

COMBUSTIBLES = tuple(p for p, d in PRODUCTOS.items() if d["categoria"] == "combustible")

# Grafías que llegan de colectores/imports viejos → código canónico.
_PRODUCTOS_ALIAS = {
    "diessel": "diésel",
    "diesel": "diésel",
    "diésel": "diésel",
    "super": "superior",
    "superior": "superior",
    "regular": "regular",
    "wti": "wti",
}

# Fuente canónica y prioridad (mayor gana en conflicto del mismo día).
_FUENTES = {
    "mem": ("MEM", 3),
    "ministerio de energía y minas": ("MEM", 3),
    "ministerio de energia y minas": ("MEM", 3),
    "ministerio de energia y minas (html)": ("MEM", 3),
    "mem html": ("MEM", 3),
    "oilpriceapi": ("OilPriceAPI", 3),
    "manual": ("manual", 2),
    "globalpetrolprices": ("GlobalPetrolPrices", 1),
    "chapin tv": ("Chapin TV", 1),
    "prensa libre": ("Prensa Libre", 1),
    "gnews gt": ("GNews GT", 1),
}

_FECHA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def canon_producto(producto: str) -> str:
    """Normaliza un nombre de producto a su código canónico."""
    if not producto:
        return producto
    return _PRODUCTOS_ALIAS.get(producto.strip().lower(), producto.strip())


def canon_fuente(fuente: str) -> str:
    """Normaliza un nombre de fuente ('Ministerio de Energía y Minas' → 'MEM')."""
    fuente = (fuente or "manual").strip()
    return _FUENTES.get(fuente.lower(), (fuente, 1))[0]


def prioridad_fuente(fuente: str) -> int:
    """Prioridad de una fuente (desconocidas = 1, la más baja)."""
    return _FUENTES.get((fuente or "").strip().lower(), (fuente, 1))[1]


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
    """Crea tablas, catálogo y vistas por producto; normaliza datos viejos."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS productos (
            codigo TEXT PRIMARY KEY,
            nombre TEXT NOT NULL,
            categoria TEXT NOT NULL,
            unidad TEXT NOT NULL,
            archivo TEXT NOT NULL,
            orden INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS precios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,
            producto TEXT NOT NULL,
            precio REAL NOT NULL,
            fuente TEXT NOT NULL DEFAULT 'manual',
            fetched_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_precios_fecha_prod
            ON precios(fecha, producto);

        CREATE TABLE IF NOT EXISTS noticias (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL,
            titulo TEXT NOT NULL,
            titulo_es TEXT,
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
    # Catálogo (idempotente: refleja siempre PRODUCTOS)
    conn.executemany(
        "INSERT OR REPLACE INTO productos (codigo, nombre, categoria, unidad, archivo, orden) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(c, d["nombre"], d["categoria"], d["unidad"], d["archivo"], d["orden"]) for c, d in PRODUCTOS.items()],
    )
    # Una vista de historial por producto, generada desde el catálogo
    for codigo, d in PRODUCTOS.items():
        conn.execute(
            f"CREATE VIEW IF NOT EXISTS historial_{d['archivo']} AS "
            f"SELECT fecha, precio, fuente, fetched_at FROM precios "
            f"WHERE producto = '{codigo}' ORDER BY fecha"
        )
    # Migración para DBs existentes (CREATE IF NOT EXISTS no agrega columnas)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(noticias)").fetchall()]
    if "titulo_es" not in cols:
        conn.execute("ALTER TABLE noticias ADD COLUMN titulo_es TEXT")
    _normalizar_precios(conn)
    conn.commit()


def _normalizar_precios(conn: sqlite3.Connection) -> None:
    """Deja DBs viejas en forma canónica: grafías de producto/fuente y catálogo.

    - 'diessel'/'diesel' → 'diésel' (si ya existe el canónico ese día, se borra el alias)
    - productos fuera del catálogo (ej. 'bunker') se eliminan
    - fuentes a su nombre canónico ('Ministerio de Energía y Minas' → 'MEM')
    """
    for alias, canon in _PRODUCTOS_ALIAS.items():
        if alias == canon:
            continue
        conn.execute(
            "DELETE FROM precios WHERE producto = ? AND EXISTS "
            "(SELECT 1 FROM precios p2 WHERE p2.producto = ? AND p2.fecha = precios.fecha)",
            (alias, canon),
        )
        conn.execute("UPDATE precios SET producto = ? WHERE producto = ?", (canon, alias))
    conn.execute("DELETE FROM precios WHERE producto NOT IN (SELECT codigo FROM productos)")
    for alias, (canon, _) in _FUENTES.items():
        conn.execute(
            "UPDATE precios SET fuente = ? WHERE lower(fuente) = ? AND fuente != ?",
            (canon, alias, canon),
        )


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
# Precios — escritura (una sola vía para todos los productos)
# ──────────────────────────────────────────────

def guardar_precios(
    conn: sqlite3.Connection,
    filas,
    fuente: str = None,
    sobrescribir: bool = True,
) -> dict:
    """Inserta o actualiza precios de cualquier producto.

    Args:
        filas: iterable de dicts {producto, fecha, precio[, fuente]}.
        fuente: fuente por defecto si la fila no trae la suya.
        sobrescribir: False = solo sembrar (no toca filas existentes; para
            restaurar memoria sin pisar datos más nuevos).

    Por cada (producto, fecha):
      - no existe                         → insertado
      - existe, fuente de menor prioridad → descartado (no se pisa lo oficial)
      - existe, mismo precio y fuente     → sin_cambio
      - existe, en otro caso              → actualizado

    Returns:
        Conteo {insertados, actualizados, sin_cambio, descartados, invalidos}.
    """
    conteo = {"insertados": 0, "actualizados": 0, "sin_cambio": 0, "descartados": 0, "invalidos": 0}
    ahora = ahora_gt_iso()

    for f in filas:
        producto = canon_producto(f.get("producto") or "")
        fecha = (f.get("fecha") or "").strip()
        src = canon_fuente(f.get("fuente") or fuente)
        try:
            precio = float(f.get("precio"))
        except (TypeError, ValueError):
            precio = 0.0
        if producto not in PRODUCTOS or not _FECHA_RE.match(fecha) or precio <= 0:
            conteo["invalidos"] += 1
            continue

        actual = conn.execute(
            "SELECT precio, fuente FROM precios WHERE producto = ? AND fecha = ?",
            (producto, fecha),
        ).fetchone()

        if actual is None:
            conn.execute(
                "INSERT INTO precios (fecha, producto, precio, fuente, fetched_at) VALUES (?, ?, ?, ?, ?)",
                (fecha, producto, precio, src, ahora),
            )
            conteo["insertados"] += 1
        elif not sobrescribir or prioridad_fuente(src) < prioridad_fuente(actual[1]):
            conteo["descartados"] += 1
        elif actual[0] == precio and actual[1] == src:
            conteo["sin_cambio"] += 1
        else:
            conn.execute(
                "UPDATE precios SET precio = ?, fuente = ?, fetched_at = ? WHERE producto = ? AND fecha = ?",
                (precio, src, ahora, producto, fecha),
            )
            conteo["actualizados"] += 1

    conn.commit()
    return conteo


def borrar_precios(
    conn: sqlite3.Connection,
    producto: str = None,
    desde: str = None,
    hasta: str = None,
    fuente: str = None,
) -> int:
    """Borra precios con los filtros dados (todos opcionales, fechas inclusivas).

    Returns:
        Cantidad de filas borradas.
    """
    where, params = _filtros(producto, desde, hasta, fuente)
    cursor = conn.execute(f"DELETE FROM precios{where}", params)
    conn.commit()
    return cursor.rowcount


# ──────────────────────────────────────────────
# Precios — lectura
# ──────────────────────────────────────────────

def _filtros(producto=None, desde=None, hasta=None, fuente=None) -> tuple[str, list]:
    """WHERE común a lecturas y borrados (mismos filtros en todas partes)."""
    cond, params = [], []
    if producto:
        cond.append("producto = ?")
        params.append(canon_producto(producto))
    if desde:
        cond.append("fecha >= ?")
        params.append(desde)
    if hasta:
        cond.append("fecha <= ?")
        params.append(hasta)
    if fuente:
        cond.append("fuente = ?")
        params.append(canon_fuente(fuente))
    return (" WHERE " + " AND ".join(cond) if cond else ""), params


def leer_historial(
    conn: sqlite3.Connection,
    producto: str = None,
    desde: str = None,
    hasta: str = None,
) -> list[sqlite3.Row]:
    """Historial ordenado por fecha (un producto o todos)."""
    where, params = _filtros(producto, desde, hasta)
    return conn.execute(
        f"SELECT * FROM precios{where} ORDER BY fecha, producto", params
    ).fetchall()


def leer_ultimo(conn: sqlite3.Connection, producto: str) -> sqlite3.Row | None:
    """Último precio disponible de un producto."""
    return conn.execute(
        "SELECT * FROM precios WHERE producto = ? ORDER BY fecha DESC LIMIT 1",
        (canon_producto(producto),),
    ).fetchone()


def leer_actuales(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """Último precio de cada producto del catálogo (omite los que no tienen datos)."""
    actuales = {}
    for producto in PRODUCTOS:
        row = leer_ultimo(conn, producto)
        if row is not None:
            actuales[producto] = row
    return actuales


# ──────────────────────────────────────────────
# Noticias y ejecuciones
# ──────────────────────────────────────────────

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
    titulo_es: str = None,
) -> int | None:
    """Inserta o ignora una noticia.

    Returns:
        id del registro insertado, o None si ya existía por URL duplicada.
    """
    fetched_at = ahora_gt_iso()
    cursor = conn.execute(
        "INSERT OR IGNORE INTO noticias (url, titulo, titulo_es, medio, publicado_at, categoria, pais, relevancia, resumen_es, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (url, titulo, titulo_es, medio, publicado_at, categoria, pais, relevancia, resumen_es, fetched_at),
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
    inicio = ahora_gt_iso()
    cursor = conn.execute(
        "INSERT INTO ejecuciones (inicio, fin, modulo, ok, mensaje) VALUES (?, ?, ?, ?, ?)",
        (inicio, inicio, modulo, ok, mensaje),
    )
    conn.commit()
    return cursor.lastrowid


def obtener_noticias(
    conn: sqlite3.Connection, dias: int = 30, categoria: str = None
) -> list[sqlite3.Row]:
    """Obtiene noticias de los últimos N días (fecha GT)."""
    query = "SELECT * FROM noticias WHERE publicado_at >= ?"
    params: list = [hace_dias_gt(dias)]

    if categoria:
        query += " AND categoria = ?"
        params.append(categoria)

    query += " ORDER BY publicado_at DESC"
    return conn.execute(query, params).fetchall()


def obtener_noticia_por_url(conn: sqlite3.Connection, url: str) -> sqlite3.Row | None:
    """Verifica si una noticia ya existe por URL."""
    return conn.execute(
        "SELECT * FROM noticias WHERE url = ?", (url,)
    ).fetchone()


def obtener_ultimas_ejecuciones(
    conn: sqlite3.Connection, limite: int = 10
) -> list[sqlite3.Row]:
    """Obtiene las últimas N ejecuciones."""
    return conn.execute(
        "SELECT * FROM ejecuciones ORDER BY inicio DESC LIMIT ?", (limite,)
    ).fetchall()
