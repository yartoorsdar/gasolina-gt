"""Memoria persistente de precios: un CSV por producto, commitado en git.

La DB de CI es efímera (cada run nace vacía). Esta carpeta es la fuente de
verdad del historial entre runs:

  data/db/
    regular.csv       ← historial diario de cada producto del catálogo
    superior.csv         (mismas columnas en todos: fecha,modalidad,precio,fuente)
    diesel.csv
    wti.csv
    mensual.csv       ← promedios mensuales oficiales MEM (autoservicio,
                         Ciudad Capital) 2020-01 → (anio,mes,producto,promedio,fuente)
    observaciones.csv ← precios encontrados por fuente (insumo del consejo)
    articulos.csv     ← notas ya leídas (no se reprocesan)
    consenso.csv      ← veredicto diario del consejo (precio + confianza)
    anual_semilla.csv ← promedios anuales de años SIN datos diarios ni mensuales
                         (anio,producto,promedio,fuente)

Ciclo:
  1. `importar_memoria()` al INICIO del run: siembra la tabla precios desde
     los CSV (sin pisar filas existentes).
  2. Los colectores insertan/actualizan con `db.guardar_precios` (upsert).
  3. `exportar_memoria()` al FINAL: vuelca cada producto a su CSV.

Archivos deterministas (orden por fecha, sin timestamps): si nada cambió,
salen byte-idénticos y git no genera diff.
"""

import csv
from pathlib import Path

MEMORIA_DIR = Path(__file__).resolve().parent.parent / "data" / "db"
SEMILLA_ANUAL_CSV = MEMORIA_DIR / "anual_semilla.csv"
MENSUAL_CSV = MEMORIA_DIR / "mensual.csv"

_COLUMNAS = ("fecha", "modalidad", "precio", "fuente")

# Tablas del consejo que también persisten en git (misma forma genérica:
# columnas fijas, orden fijo → archivo determinista). Se omiten ids y
# timestamps de cálculo para no generar diffs sin cambios reales.
TABLAS_MEMORIA = {
    "observaciones": {
        "columnas": ("fecha", "producto", "modalidad", "precio", "tipo", "medio", "url", "cita", "extractor", "fetched_at"),
        "orden": "fecha, producto, modalidad, medio, url, tipo",
    },
    "articulos": {
        "columnas": ("url", "medio", "titulo", "publicado", "procesado_at", "extractor", "n_obs", "error"),
        "orden": "url",
    },
    "consenso": {
        "columnas": ("fecha", "producto", "modalidad", "precio", "confianza", "n_coinciden", "n_fuentes", "fuentes"),
        "orden": "fecha, producto, modalidad",
    },
}


def _ruta(producto: str, carpeta: Path) -> Path:
    from collector.db import PRODUCTOS
    return carpeta / f"{PRODUCTOS[producto]['archivo']}.csv"


def exportar_memoria(conn=None, carpeta: Path | None = None) -> dict[str, int]:
    """Vuelca el historial de cada producto (todas sus modalidades) a su CSV y
    las tablas del consejo a <tabla>.csv (sobrescribe).

    Returns:
        {producto|tabla: filas escritas}
    """
    from collector.db import PRODUCTOS, conectar, leer_historial  # perezoso: runnable directo

    carpeta = carpeta or MEMORIA_DIR
    carpeta.mkdir(parents=True, exist_ok=True)
    propia = conn is None
    conn = conn or conectar()

    escritas = {}
    for producto in PRODUCTOS:
        rows = leer_historial(conn, producto, todas=True)
        with open(_ruta(producto, carpeta), "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(_COLUMNAS)
            for r in rows:
                writer.writerow([r["fecha"], r["modalidad"], repr(float(r["precio"])), r["fuente"]])
        escritas[producto] = len(rows)

    for tabla, meta in TABLAS_MEMORIA.items():
        cols = meta["columnas"]
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM {tabla} ORDER BY {meta['orden']}").fetchall()
        with open(carpeta / f"{tabla}.csv", "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(cols)
            writer.writerows([["" if v is None else v for v in r] for r in rows])
        escritas[tabla] = len(rows)

    if propia:
        conn.close()
    print(f"[memoria] Exportado a {carpeta}: "
          + ", ".join(f"{p}={n}" for p, n in escritas.items()))
    return escritas


def importar_memoria(conn=None, carpeta: Path | None = None) -> dict[str, int]:
    """Siembra precios (por producto) y tablas del consejo desde los CSV.

    No pisa filas existentes: lo de hoy lo definen después los colectores.
    Archivo ausente = primer ciclo, se omite. CSV de precios sin columna
    `modalidad` (formato anterior) = modalidad principal del producto.

    Returns:
        {producto|tabla: filas insertadas}
    """
    from collector.db import PRODUCTOS, ahora_gt_iso, conectar, guardar_precios  # perezoso

    carpeta = carpeta or MEMORIA_DIR
    propia = conn is None
    conn = conn or conectar()

    insertadas = {}
    for producto in PRODUCTOS:
        path = _ruta(producto, carpeta)
        if not path.exists():
            insertadas[producto] = 0
            continue
        with open(path, "r", encoding="utf-8") as f:
            filas = [dict(r, producto=producto) for r in csv.DictReader(f)]
        conteo = guardar_precios(conn, filas, sobrescribir=False)
        insertadas[producto] = conteo["insertados"]
        if conteo["invalidos"]:
            print(f"[memoria] {path.name}: {conteo['invalidos']} fila(s) inválida(s) omitida(s)")

    for tabla, meta in TABLAS_MEMORIA.items():
        path = carpeta / f"{tabla}.csv"
        if not path.exists():
            insertadas[tabla] = 0
            continue
        with open(path, "r", encoding="utf-8") as f:
            filas = [{k: (v if v != "" else None) for k, v in r.items()} for r in csv.DictReader(f)]
        cols = list(meta["columnas"])
        if tabla == "consenso":
            cols.append("calculado_at")
            for r in filas:
                r["calculado_at"] = ahora_gt_iso()
        antes = conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
        conn.executemany(
            f"INSERT OR IGNORE INTO {tabla} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [[r.get(c) for c in cols] for r in filas],
        )
        conn.commit()
        insertadas[tabla] = conn.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0] - antes

    if propia:
        conn.close()
    print(f"[memoria] Importado desde {carpeta}: "
          + ", ".join(f"{p}={n}" for p, n in insertadas.items()))
    return insertadas


def leer_mensual(path: Path | None = None) -> list[dict]:
    """Promedios mensuales oficiales (MEM, autoservicio, Ciudad Capital)."""
    path = path or MENSUAL_CSV
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [
            {"anio": int(r["anio"]), "mes": int(r["mes"]), "producto": r["producto"],
             "promedio": float(r["promedio"]), "fuente": r["fuente"]}
            for r in csv.DictReader(f)
        ]


def leer_semilla_anual(path: Path | None = None) -> list[dict]:
    """Promedios anuales de años sin datos diarios (solo lectura)."""
    path = path or SEMILLA_ANUAL_CSV
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [
            {"anio": int(r["anio"]), "producto": r["producto"],
             "promedio": float(r["promedio"]), "fuente": r["fuente"]}
            for r in csv.DictReader(f)
        ]


if __name__ == "__main__":
    # Ejecución directa: asegurar root del proyecto en sys.path (imports perezosos).
    import sys
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    # Uso directo: `python collector/memoria.py` → importa y re-exporta.
    importar_memoria()
    exportar_memoria()
