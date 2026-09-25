"""Memoria persistente de precios: un CSV por producto, commitado en git.

La DB de CI es efímera (cada run nace vacía). Esta carpeta es la fuente de
verdad del historial entre runs:

  data/db/
    regular.csv       ← historial diario de cada producto del catálogo
    superior.csv         (mismas columnas en todos: fecha,precio,fuente)
    diesel.csv
    wti.csv
    mensual.csv       ← promedios mensuales oficiales MEM (autoservicio,
                         Ciudad Capital) 2020-01 → (anio,mes,producto,promedio,fuente)
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

_COLUMNAS = ("fecha", "precio", "fuente")


def _ruta(producto: str, carpeta: Path) -> Path:
    from collector.db import PRODUCTOS
    return carpeta / f"{PRODUCTOS[producto]['archivo']}.csv"


def exportar_memoria(conn=None, carpeta: Path | None = None) -> dict[str, int]:
    """Vuelca el historial de cada producto a su CSV (sobrescribe).

    Returns:
        {producto: filas escritas}
    """
    from collector.db import PRODUCTOS, conectar, leer_historial  # perezoso: runnable directo

    carpeta = carpeta or MEMORIA_DIR
    carpeta.mkdir(parents=True, exist_ok=True)
    propia = conn is None
    conn = conn or conectar()

    escritas = {}
    for producto in PRODUCTOS:
        rows = leer_historial(conn, producto)
        with open(_ruta(producto, carpeta), "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f, lineterminator="\n")
            writer.writerow(_COLUMNAS)
            for r in rows:
                writer.writerow([r["fecha"], repr(float(r["precio"])), r["fuente"]])
        escritas[producto] = len(rows)

    if propia:
        conn.close()
    print(f"[memoria] Exportado → {carpeta}: "
          + ", ".join(f"{p}={n}" for p, n in escritas.items()))
    return escritas


def importar_memoria(conn=None, carpeta: Path | None = None) -> dict[str, int]:
    """Siembra la tabla precios desde los CSV de cada producto.

    No pisa filas existentes (sobrescribir=False): lo de hoy lo definen
    después los colectores. Producto sin CSV = primer ciclo, se omite.

    Returns:
        {producto: filas insertadas}
    """
    from collector.db import PRODUCTOS, conectar, guardar_precios  # perezoso: runnable directo

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

    if propia:
        conn.close()
    print(f"[memoria] Importado ← {carpeta}: "
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
