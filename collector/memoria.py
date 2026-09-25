"""Memoria persistente de precios (CSV commitado en git).

La DB de CI es efímera: cada run nace vacía y regenera los JSONs solo con lo
que recolectó ese día — el historial se "reiniciaba" a la vista de Vercel.

Este módulo rompe el ciclo:
  1. `importar_memoria()` al INICIO del run: restaura toda la tabla precios
     desde data/memory/precios.csv (lo que runs anteriores acumularon).
  2. Los colectores agregan/actualizan el día de hoy (sus patrones
     delete-hoy-antes-de-insertar ya lo garantizan: re-ejecutar un mismo
     día ACTUALIZA el precio, no inserta ni duplica).
  3. `exportar_memoria()` al FINAL: vuelca la tabla completa de vuelta al
     CSV → se commitea junto con los JSONs y el próximo run parte de ahí.

El archivo es determinista (orden fijo por fecha+producto, sin timestamps):
si nada cambió, sale byte-idéntico y git no genera diff.
"""

import csv
from pathlib import Path

_MEMORIA_DIR = Path(__file__).resolve().parent.parent / "data" / "memory"
MEMORIA_CSV = _MEMORIA_DIR / "precios.csv"

_COLUMNAS = ("fecha", "producto", "precio", "fuente")


def exportar_memoria(conn=None, path: Path | None = None) -> Path:
    """Vuelca la tabla `precios` completa al CSV (sobrescribe).

    Determinista: orden fijo + sin fetched_at → mismo contenido = mismos bytes.
    Devuelve la ruta del archivo escrito.
    """
    path = path or MEMORIA_CSV
    from collector.db import canon_producto, conectar  # perezoso: runnable directo

    if conn is None:
        conn = conectar()
        propia = True
    else:
        propia = False

    rows = conn.execute(
        "SELECT fecha, producto, precio, fuente, fetched_at FROM precios"
    ).fetchall()

    # Deduplicar por (fecha, canónico): la tabla puede guardar dos grafías del
    # mismo día ('diessel' de un import antiguo + 'diésel' de uno nuevo). La
    # memoria conserva UNA fila: la más recién importada (fetched_at ISO con el
    # mismo offset → comparación lexicográfica = cronológica).
    mejor = {}
    for r in rows:
        clave = (r["fecha"], canon_producto(r["producto"]))
        prev = mejor.get(clave)
        if prev is None or r["fetched_at"] > prev["fetched_at"]:
            mejor[clave] = r

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, lineterminator="\n", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(_COLUMNAS)
        for clave in sorted(mejor):
            r = mejor[clave]
            # Productos canónicos en el archivo (diessel→diésel): la memoria y
            # el dashboard hablan el mismo idioma; sin esto las grafías
            # duplicadas crearían pares repetidos al re-importar.
            writer.writerow(
                [r["fecha"], canon_producto(r["producto"]), repr(float(r["precio"])), r["fuente"]]
            )

    if propia:
        conn.close()
    n_dup = len(rows) - len(mejor)
    print(f"[memoria] Exportadas {len(mejor)} filas únicas"
          + (f" ({n_dup} grafías duplicadas deduplicadas)" if n_dup else "")
          + f" → {path}")
    return path


def importar_memoria(path: Path | None = None, conn=None) -> int:
    """Restaura la tabla `precios` desde el CSV (insert-or-ignore).

    Semántica: la memoria SIEMBRA el historial; los valores del día de hoy los
    definen después los colectores. Si una fila ya existe con otro valor
    (edición manual local), se conserva — no se pisa trabajo nuevo.

    Devuelve cuántas filas se insertaron (0 si no hay archivo: primer ciclo).
    """
    path = path or MEMORIA_CSV
    from collector.db import canon_producto, conectar, insertar_precio  # perezoso: runnable directo

    if not path.exists():
        print(f"[memoria] Sin archivo {path.name} (primer ciclo) — nada que importar")
        return 0

    if conn is None:
        conn = conectar()
        propia = True
    else:
        propia = False

    insertadas = 0
    malas = 0
    with open(path, "r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                fecha = (row.get("fecha") or "").strip()
                producto = canon_producto((row.get("producto") or "").strip())
                precio = float(row.get("precio"))
                fuente = (row.get("fuente") or "memoria").strip()
                if not fecha or not producto:
                    raise ValueError(f"fila incompleta: {row}")
                row_id = insertar_precio(conn, fecha, producto, precio, fuente)
                if row_id is not None:
                    insertadas += 1
            except Exception as exc:
                malas += 1
                print(f"[memoria] Fila malformada (se omite): {exc}")

    if propia:
        conn.close()
    print(f"[memoria] Importadas {insertadas} filas desde {path.name}"
          + (f" ({malas} omitidas)" if malas else ""))
    return insertadas


if __name__ == "__main__":
    # Ejecución directa: asegurar root del proyecto en sys.path (imports perezosos).
    import sys
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    # Uso directo: `python collector/memoria.py` → importa y re-exporta.
    n = importar_memoria()
    exportar_memoria()
    print(f"[memoria] ciclo completo ({n} filas importadas)")
