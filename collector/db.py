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
# `modalidades`: la primera es la PRINCIPAL (la que se lee si no se pide otra).
_MOD_COMBUSTIBLE = ("autoservicio", "servicio_completo")
PRODUCTOS = {
    "regular":  {"nombre": "Regular",  "categoria": "combustible", "unidad": "GTQ/galón",  "archivo": "regular",  "orden": 1, "modalidades": _MOD_COMBUSTIBLE},
    "superior": {"nombre": "Superior", "categoria": "combustible", "unidad": "GTQ/galón",  "archivo": "superior", "orden": 2, "modalidades": _MOD_COMBUSTIBLE},
    "diésel":   {"nombre": "Diésel",   "categoria": "combustible", "unidad": "GTQ/galón",  "archivo": "diesel",   "orden": 3, "modalidades": _MOD_COMBUSTIBLE},
    "wti":      {"nombre": "WTI",      "categoria": "petroleo",    "unidad": "USD/barril", "archivo": "wti",      "orden": 4, "modalidades": ("spot",)},
}

COMBUSTIBLES = tuple(p for p, d in PRODUCTOS.items() if d["categoria"] == "combustible")

MODALIDADES = {
    "autoservicio": "Autoservicio",
    "servicio_completo": "Servicio completo",
    "spot": "Spot",
}

# Claves ya normalizadas: minúsculas, '_' y '-' → espacio.
_MODALIDADES_ALIAS = {
    "autoservicio": "autoservicio", "auto servicio": "autoservicio", "as": "autoservicio",
    "self service": "autoservicio",
    "servicio completo": "servicio_completo", "sc": "servicio_completo", "full service": "servicio_completo",
    "spot": "spot",
}


def modalidad_principal(producto: str) -> str:
    """Modalidad por defecto de un producto (autoservicio / spot)."""
    return PRODUCTOS[canon_producto(producto)]["modalidades"][0]


def canon_modalidad(modalidad: str | None, producto: str) -> str | None:
    """Normaliza la modalidad; None/vacía = la principal del producto.

    Devuelve None si la modalidad no aplica a ese producto (fila inválida).
    """
    producto = canon_producto(producto)
    if producto not in PRODUCTOS:
        return None
    if not modalidad:
        return modalidad_principal(producto)
    clave = str(modalidad).strip().lower().replace("_", " ").replace("-", " ")
    m = _MODALIDADES_ALIAS.get(clave)
    return m if m in PRODUCTOS[producto]["modalidades"] else None

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
    # Veredicto del consejo multifuente: por debajo del dato oficial del MEM
    # (si el MEM publica ese día, gana el MEM), por encima de una sola fuente.
    "consejo": ("Consejo", 2),
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
            fetched_at TEXT NOT NULL,
            modalidad TEXT NOT NULL DEFAULT 'autoservicio'
        );

        -- Cada precio encontrado en una fuente (insumo del consejo)
        CREATE TABLE IF NOT EXISTS observaciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fecha TEXT NOT NULL,          -- fecha a la que corresponde el precio
            producto TEXT NOT NULL,
            modalidad TEXT NOT NULL,
            precio REAL NOT NULL,
            tipo TEXT NOT NULL,           -- monitoreado | referencia
            medio TEXT NOT NULL,          -- dominio o nombre de la fuente
            url TEXT NOT NULL,
            cita TEXT,                    -- frase de donde salió (verificable)
            extractor TEXT NOT NULL,      -- llm | regex | api
            fetched_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_observaciones_clave
            ON observaciones(url, producto, modalidad, fecha, tipo);

        -- Notas ya leídas (no se vuelven a procesar ni a gastar LLM)
        CREATE TABLE IF NOT EXISTS articulos (
            url TEXT PRIMARY KEY,
            medio TEXT NOT NULL,
            titulo TEXT,
            publicado TEXT,
            procesado_at TEXT NOT NULL,
            extractor TEXT,
            n_obs INTEGER NOT NULL DEFAULT 0,
            error TEXT
        );

        -- Veredicto del consejo por día, producto y modalidad
        CREATE TABLE IF NOT EXISTS consenso (
            fecha TEXT NOT NULL,
            producto TEXT NOT NULL,
            modalidad TEXT NOT NULL,
            precio REAL NOT NULL,
            confianza TEXT NOT NULL,      -- alta | media | baja
            n_coinciden INTEGER NOT NULL,
            n_fuentes INTEGER NOT NULL,
            fuentes TEXT NOT NULL,        -- JSON [{medio, precio, fecha, url, coincide}]
            calculado_at TEXT NOT NULL,
            PRIMARY KEY (fecha, producto, modalidad)
        );

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
    # Migraciones para DBs existentes (CREATE IF NOT EXISTS no agrega columnas)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(noticias)").fetchall()]
    if "titulo_es" not in cols:
        conn.execute("ALTER TABLE noticias ADD COLUMN titulo_es TEXT")
    cols = [r[1] for r in conn.execute("PRAGMA table_info(precios)").fetchall()]
    if "modalidad" not in cols:
        conn.execute("ALTER TABLE precios ADD COLUMN modalidad TEXT NOT NULL DEFAULT 'autoservicio'")
    # Clave única = (fecha, producto, modalidad). La vieja (fecha, producto)
    # impediría guardar autoservicio y servicio completo del mismo día.
    conn.execute("DROP INDEX IF EXISTS idx_precios_fecha_prod")
    _normalizar_precios(conn)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_precios_clave ON precios(fecha, producto, modalidad)"
    )
    # Una vista de historial por producto, generada desde el catálogo
    # (se recrea: las viejas no tenían la columna modalidad)
    for codigo, d in PRODUCTOS.items():
        conn.execute(f"DROP VIEW IF EXISTS historial_{d['archivo']}")
        conn.execute(
            f"CREATE VIEW historial_{d['archivo']} AS "
            f"SELECT fecha, modalidad, precio, fuente, fetched_at FROM precios "
            f"WHERE producto = '{codigo}' ORDER BY fecha, modalidad"
        )
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
            "(SELECT 1 FROM precios p2 WHERE p2.producto = ? AND p2.fecha = precios.fecha "
            "AND p2.modalidad = precios.modalidad)",
            (alias, canon),
        )
        conn.execute("UPDATE precios SET producto = ? WHERE producto = ?", (canon, alias))
    conn.execute("DELETE FROM precios WHERE producto NOT IN (SELECT codigo FROM productos)")
    # Modalidad principal para productos de una sola modalidad (wti → spot)
    for codigo, d in PRODUCTOS.items():
        if len(d["modalidades"]) == 1:
            conn.execute(
                "UPDATE precios SET modalidad = ? WHERE producto = ? AND modalidad != ?",
                (d["modalidades"][0], codigo, d["modalidades"][0]),
            )
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
    """Inserta o actualiza precios de cualquier producto y modalidad.

    Args:
        filas: iterable de dicts {producto, fecha, precio[, modalidad][, fuente]}.
            Sin modalidad = la principal del producto (autoservicio / spot).
        fuente: fuente por defecto si la fila no trae la suya.
        sobrescribir: False = solo sembrar (no toca filas existentes; para
            restaurar memoria sin pisar datos más nuevos).

    Por cada (producto, modalidad, fecha):
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
        modalidad = canon_modalidad(f.get("modalidad"), producto)
        fecha = (f.get("fecha") or "").strip()
        src = canon_fuente(f.get("fuente") or fuente)
        try:
            precio = float(f.get("precio"))
        except (TypeError, ValueError):
            precio = 0.0
        if producto not in PRODUCTOS or modalidad is None or not _FECHA_RE.match(fecha) or precio <= 0:
            conteo["invalidos"] += 1
            continue

        clave = (producto, modalidad, fecha)
        actual = conn.execute(
            "SELECT precio, fuente FROM precios WHERE producto = ? AND modalidad = ? AND fecha = ?",
            clave,
        ).fetchone()

        if actual is None:
            conn.execute(
                "INSERT INTO precios (fecha, producto, modalidad, precio, fuente, fetched_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fecha, producto, modalidad, precio, src, ahora),
            )
            conteo["insertados"] += 1
        elif not sobrescribir or prioridad_fuente(src) < prioridad_fuente(actual[1]):
            conteo["descartados"] += 1
        elif actual[0] == precio and actual[1] == src:
            conteo["sin_cambio"] += 1
        else:
            conn.execute(
                "UPDATE precios SET precio = ?, fuente = ?, fetched_at = ? "
                "WHERE producto = ? AND modalidad = ? AND fecha = ?",
                (precio, src, ahora, *clave),
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
    modalidad: str = None,
) -> int:
    """Borra precios con los filtros dados (todos opcionales, fechas inclusivas).

    Sin `modalidad` borra TODAS las modalidades que coincidan.

    Returns:
        Cantidad de filas borradas.
    """
    where, params = _filtros(producto, desde, hasta, fuente, modalidad)
    cursor = conn.execute(f"DELETE FROM precios{where}", params)
    conn.commit()
    return cursor.rowcount


# ──────────────────────────────────────────────
# Precios — lectura
# ──────────────────────────────────────────────

def _filtros(producto=None, desde=None, hasta=None, fuente=None, modalidad=None) -> tuple[str, list]:
    """WHERE común a lecturas y borrados (mismos filtros en todas partes)."""
    cond, params = [], []
    if producto:
        cond.append("producto = ?")
        params.append(canon_producto(producto))
    if modalidad:
        cond.append("modalidad = ?")
        params.append(canon_modalidad(modalidad, producto) if producto else modalidad)
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
    modalidad: str = None,
    todas: bool = False,
) -> list[sqlite3.Row]:
    """Historial ordenado por fecha (un producto o todos).

    Por defecto solo la modalidad PRINCIPAL de cada producto; `modalidad=` pide
    una concreta y `todas=True` devuelve todas las modalidades.
    """
    if todas:
        where, params = _filtros(producto, desde, hasta)
    elif modalidad:
        where, params = _filtros(producto, desde, hasta, modalidad=modalidad)
    else:
        where, params = _filtros(producto, desde, hasta)
        principales = sorted({d["modalidades"][0] for d in PRODUCTOS.values()})
        cond = f"modalidad IN ({','.join('?' * len(principales))})"
        where = f"{where} AND {cond}" if where else f" WHERE {cond}"
        params += principales
    return conn.execute(
        f"SELECT * FROM precios{where} ORDER BY fecha, producto, modalidad", params
    ).fetchall()


def leer_ultimo(conn: sqlite3.Connection, producto: str, modalidad: str = None) -> sqlite3.Row | None:
    """Último precio disponible de un producto (modalidad principal si no se indica)."""
    producto = canon_producto(producto)
    modalidad = canon_modalidad(modalidad, producto)
    return conn.execute(
        "SELECT * FROM precios WHERE producto = ? AND modalidad = ? ORDER BY fecha DESC LIMIT 1",
        (producto, modalidad),
    ).fetchone()


def leer_actuales(conn: sqlite3.Connection, modalidad: str = None) -> dict[str, sqlite3.Row]:
    """Último precio de cada producto del catálogo (omite los que no tienen datos).

    `modalidad` solo aplica a productos que la tengan (ej. servicio_completo
    no existe para wti → se omite).
    """
    actuales = {}
    for producto, d in PRODUCTOS.items():
        if modalidad and canon_modalidad(modalidad, producto) is None:
            continue
        row = leer_ultimo(conn, producto, modalidad)
        if row is not None:
            actuales[producto] = row
    return actuales


# ──────────────────────────────────────────────
# Consejo de precios: observaciones, notas procesadas y veredictos
# ──────────────────────────────────────────────

TIPOS_OBSERVACION = ("monitoreado", "referencia")
PRECIO_COMBUSTIBLE_MIN, PRECIO_COMBUSTIBLE_MAX = 10.0, 120.0  # Q/galón plausibles


def guardar_observaciones(conn: sqlite3.Connection, filas) -> dict:
    """Guarda observaciones de precio (insert-or-ignore por url+producto+modalidad+fecha+tipo).

    Valida: producto/modalidad del catálogo, tipo conocido, fecha ISO y precio
    en rango plausible. Devuelve {insertadas, duplicadas, invalidas}.
    """
    conteo = {"insertadas": 0, "duplicadas": 0, "invalidas": 0}
    ahora = ahora_gt_iso()
    for f in filas:
        producto = canon_producto(f.get("producto") or "")
        modalidad = canon_modalidad(f.get("modalidad"), producto) if f.get("modalidad") else None
        fecha = (f.get("fecha") or "").strip()
        tipo = (f.get("tipo") or "").strip().lower()
        try:
            precio = round(float(f.get("precio")), 2)
        except (TypeError, ValueError):
            precio = 0.0
        if (producto not in COMBUSTIBLES or modalidad is None or tipo not in TIPOS_OBSERVACION
                or not _FECHA_RE.match(fecha)
                or not PRECIO_COMBUSTIBLE_MIN <= precio <= PRECIO_COMBUSTIBLE_MAX
                or not f.get("url") or not f.get("medio")):
            conteo["invalidas"] += 1
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO observaciones "
            "(fecha, producto, modalidad, precio, tipo, medio, url, cita, extractor, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (fecha, producto, modalidad, precio, tipo, f["medio"], f["url"],
             (f.get("cita") or "")[:300], f.get("extractor") or "manual", f.get("fetched_at") or ahora),
        )
        conteo["insertadas" if cur.rowcount else "duplicadas"] += 1
    conn.commit()
    return conteo


def leer_observaciones(
    conn: sqlite3.Connection, producto: str = None, modalidad: str = None,
    desde: str = None, hasta: str = None,
) -> list[sqlite3.Row]:
    """Observaciones filtradas, ordenadas por fecha y medio."""
    cond, params = [], []
    if producto:
        cond.append("producto = ?")
        params.append(canon_producto(producto))
    if modalidad:
        cond.append("modalidad = ?")
        params.append(modalidad)
    if desde:
        cond.append("fecha >= ?")
        params.append(desde)
    if hasta:
        cond.append("fecha <= ?")
        params.append(hasta)
    where = (" WHERE " + " AND ".join(cond)) if cond else ""
    return conn.execute(
        f"SELECT * FROM observaciones{where} ORDER BY fecha, medio, producto, modalidad", params
    ).fetchall()


def articulo_procesado(conn: sqlite3.Connection, url: str) -> bool:
    """¿La nota ya se leyó en un run anterior?"""
    return conn.execute("SELECT 1 FROM articulos WHERE url = ?", (url,)).fetchone() is not None


def registrar_articulo(
    conn: sqlite3.Connection, url: str, medio: str, titulo: str = None, publicado: str = None,
    extractor: str = None, n_obs: int = 0, error: str = None,
) -> None:
    """Marca una nota como procesada (con cuántas observaciones dio o su error)."""
    conn.execute(
        "INSERT OR REPLACE INTO articulos (url, medio, titulo, publicado, procesado_at, extractor, n_obs, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (url, medio, titulo, publicado, ahora_gt_iso(), extractor, n_obs, error),
    )
    conn.commit()


def guardar_consenso(conn: sqlite3.Connection, veredicto: dict) -> None:
    """Guarda (reemplaza) el veredicto del consejo de un día/producto/modalidad."""
    import json as _json
    conn.execute(
        "INSERT OR REPLACE INTO consenso "
        "(fecha, producto, modalidad, precio, confianza, n_coinciden, n_fuentes, fuentes, calculado_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (veredicto["fecha"], canon_producto(veredicto["producto"]), veredicto["modalidad"],
         veredicto["precio"], veredicto["confianza"], veredicto["n_coinciden"], veredicto["n_fuentes"],
         _json.dumps(veredicto["fuentes"], ensure_ascii=False, sort_keys=True), ahora_gt_iso()),
    )
    conn.commit()


def leer_consenso(conn: sqlite3.Connection, producto: str, modalidad: str) -> sqlite3.Row | None:
    """Último veredicto del consejo para un producto/modalidad."""
    return conn.execute(
        "SELECT * FROM consenso WHERE producto = ? AND modalidad = ? ORDER BY fecha DESC LIMIT 1",
        (canon_producto(producto), modalidad),
    ).fetchone()


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
