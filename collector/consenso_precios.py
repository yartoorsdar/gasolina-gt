"""Validador de consenso multifuente para precios de combustible — Gasolina GT.

Algoritmo de validación por consenso:
  1. Consulta todos los precios recientes (últimos 7 días) desde la DB.
  2. Agrupa por fecha + producto.
  3. Para cada grupo, verifica si ≥2 fuentes reportan precio con diferencia ≤ Q0.20.
  4. Si hay consenso → marca como válido y actualiza campo "validado".
  5. Si no hay consenso → alerta en log + registro en tabla ejecuciones (ok=0).

Productos obligatorios (spelling exacta): 'superior', 'regular', 'diessel'.
Tolerancia máxima entre fuentes: Q0.20 por galón.

Fuentes soportadas (extensible):
  - "MEM PDF"    → precios_mem.py
  - "MEM HTML"   → mem_html.py (Playwright)
  - "Prensa Libre" → futuro colector de prensa libre
  - "GNews GT"   → futuro colector de GNews Guatemala

Version Tracking:
  v1.0.0 — 2026-09-23 — Creación del módulo de consenso multifuente
         — Consenso ≥2 fuentes, tolerancia Q0.20
         — Alerta automática en log + tabla ejecuciones

Uso:
  python collector/main.py --consenso            # valida todo
  python collector/consenso_precios.py           # ejecución directa
"""

# ──────────────────────────────────────────────
# Imports y configuración de logging
# ──────────────────────────────────────────────

import json
import logging
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Setup de path para ejecución como __main__
if __name__ == "__main__":
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"consenso_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("consenso")

# ──────────────────────────────────────────────
# Constantes de negocio
# ──────────────────────────────────────────────

PRODUCTOS_OBLIGATORIOS = ["superior", "regular", "diessel"]
TOLERANCIA_QUETLES = 0.20          # diferencia máxima entre fuentes para consenso
MIN_FUENTES_CONSENSO = 2             # mínimo de fuentes que deben coincidir
DIAS_RECENTES = 7                    # consultar precios de los últimos N días

# Fuentes consideradas válidas (debe coincidir con el campo "fuente" en DB)
FUENTES_VALIDAS = {
    "MEM PDF",
    "MEM HTML",
    "Prensa Libre",
    "GNews GT",
}


# ──────────────────────────────────────────────
# Consulta de precios recientes desde la DB
# ──────────────────────────────────────────────

def obtener_precios_recentes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Obtiene todos los precios de combustible de los últimos N días.

    Returns:
        Lista de rows SQLite con todas las columnas de precios_combustible.
    """
    query = """
        SELECT * FROM precios_combustible
        WHERE fecha_observacion >= date('now', ?)
          AND producto IN ('superior', 'regular', 'diessel')
          AND fuente IN (
              'MEM PDF', 'MEM HTML', 'Prensa Libre', 'GNews GT'
          )
        ORDER BY fecha_observacion, producto, fuente
    """
    rows = conn.execute(query, (f"-{DIAS_RECENTES} days",)).fetchall()
    return rows


# ──────────────────────────────────────────────
# Agrupación por fecha + producto
# ──────────────────────────────────────────────

def agrupar_precios(rows: list[sqlite3.Row]) -> dict:
    """Agrupa precios por (fecha, producto) → {('2026-09-21', 'superior'): [row, row]}.

    Returns:
        Dict con keys = tupla (fecha, producto), values = lista de rows.
    """
    grupos: dict[tuple[str, str], list[sqlite3.Row]] = {}

    for row in rows:
        fecha = row["fecha_observacion"]
        producto = row["producto"]
        clave = (fecha, producto)

        if clave not in grupos:
            grupos[clave] = []
        grupos[clave].append(row)

    return grupos


# ──────────────────────────────────────────────
# Algoritmo de consenso principal
# ──────────────────────────────────────────────

def validar_consenso_grupo(
    grupo_rows: list[sqlite3.Row], producto: str, fecha: str
) -> dict:
    """Evalúa si un grupo de precios tiene consenso.

    Regla: al menos MIN_FUENTES_CONSENSO fuentes deben reportar precio
    con diferencia máxima de TOLERANCIA_QUETLES entre sí.

    Args:
        grupo_rows: Lista de rows SQLite para esta fecha+producto.
        producto: Nombre del producto (ej: 'superior').
        fecha: Fecha observación (ej: '2026-09-21').

    Returns:
        Dict con resultado de la validación:
          {
            "fecha": str,
            "producto": str,
            "consenso": bool,
            "precio_valido": float | None,
            "fuentes_coinciden": int,
            "precios_observados": list[float],
            "detalles": str,
          }
    """
    if len(grupo_rows) < MIN_FUENTES_CONSENSO:
        precios = [r["precio"] for r in grupo_rows]
        return {
            "fecha": fecha,
            "producto": producto,
            "consenso": False,
            "precio_valido": None,
            "fuentes_coinciden": len(grupo_rows),
            "precios_observados": precios,
            "detalles": (
                f"Solo {len(grupo_rows)} fuente(s) para {producto} "
                f"(requiere {MIN_FUENTES_CONSENSO})"
            ),
        }

    # Extraer precios únicos (redondeados a 2 decimales)
    precios_unicos = set()
    for row in grupo_rows:
        precios_unicos.add(round(row["precio"], 2))

    precios_list = sorted(precios_unicos)

    if not precios_list:
        return {
            "fecha": fecha,
            "producto": producto,
            "consenso": False,
            "precio_valido": None,
            "fuentes_coinciden": 0,
            "precios_observados": [],
            "detalles": "Sin precios para validar",
        }

    # Buscar clusters de precios dentro de la tolerancia
    # Estrategia: agrupar precios donde cada uno está dentro de tolerancia
    # del anterior (cadena), luego verificar cuántas fuentes caen en el mejor cluster.
    
    mejor_cluster = [precios_list[0]]
    cluster_actual = [precios_list[0]]

    for precio in precios_list[1:]:
        # Verificar si el nuevo precio está dentro de tolerancia del anterior en la cadena
        diff_con_anterior = precio - cluster_actual[-1]
        
        if diff_con_anterior <= TOLERANCIA_QUETLES:
            cluster_actual.append(precio)
        else:
            # Guardar cluster actual si es el mejor encontrado hasta ahora
            if len(cluster_actual) > len(mejor_cluster):
                mejor_cluster = list(cluster_actual)
            cluster_actual = [precio]

    # Verificar último cluster
    if len(cluster_actual) > len(mejor_cluster):
        mejor_cluster = list(cluster_actual)

    # Contar cuántas fuentes reportan precios dentro del mejor cluster
    # IMPORTANTE: contar filas (fuentes), no valores únicos
    fuentes_en_cluster = 0
    precio_valido = None

    for row in grupo_rows:
        # Verificar si este precio está dentro de tolerancia de AL MENOS UN
        # precio en el mejor cluster
        esta_en_cluster = any(
            abs(row["precio"] - c) <= TOLERANCIA_QUETLES 
            for c in mejor_cluster
        )
        if esta_en_cluster:
            fuentes_en_cluster += 1
            precio_valido = round(row["precio"], 2)

    consenso_alcanzado = fuentes_en_cluster >= MIN_FUENTES_CONSENSO
    
    # Solo asignar precio_valido si se alcanzó consenso
    if not consenso_alcanzado:
        precio_valido = None

    return {
        "fecha": fecha,
        "producto": producto,
        "consenso": consenso_alcanzado,
        "precio_valido": precio_valido,
        "fuentes_coinciden": fuentes_en_cluster,
        "precios_observados": precios_list,
        "detalles": (
            f"{fuentes_en_cluster}/{len(grupo_rows)} fuentes dentro de tolerancia. "
            f"Cluster: {mejor_cluster}" if mejor_cluster else "Sin cluster válido"
        ),
    }


# ──────────────────────────────────────────────
# Actualización de estado en DB (validado / no validado)
# ──────────────────────────────────────────────

def marcar_precios_validados(
    conn: sqlite3.Connection,
    resultados: list[dict],
) -> int:
    """Marca los precios válidos en la tabla ejecuciones como confirmación.

    Nota: No modificamos la tabla precios_combustible directamente porque
    no tiene campo "validado". En su lugar, registramos cada validación
    como una entrada en una tabla temporal o en el log de ejecuciones.

    Returns:
        Número de precios marcados como válidos.
    """
    marcados = 0

    for res in resultados:
        if res["consenso"]:
            # Registrar validación exitosa
            try:
                from collector.db import insertar_ejecucion

                exec_id = insertar_ejecucion(
                    conn=conn,
                    modulo="consenso",
                    ok=1,
                    mensaje=(
                        f"VALIDADO — {res['producto']} "
                        f"{res['fecha']} = Q{res['precio_valido']:.2f} "
                        f"({res['fuentes_coinciden']} fuentes)"
                    ),
                )
                marcados += 1
            except Exception as exc:
                logger.warning(f"Error marcando validación: {exc}")

    return marcados


# ──────────────────────────────────────────────
# Alerta de no consenso
# ──────────────────────────────────────────────

def registrar_alerta_no_consenso(
    conn: sqlite3.Connection, fallidos: list[dict]
) -> int:
    """Registra en DB y log cada caso donde NO se alcanzó consenso.

    Returns:
        Número de alertas registradas.
    """
    if not fallidos:
        return 0

    from collector.db import insertar_ejecucion

    alertas = 0
    for fallo in fallidos:
        mensaje_alerta = (
            f"ALERTA CONSENSO — {fallo['producto']} "
            f"{fallo['fecha']}: "
            f"fuentes={fallo['fuentes_coinciden']}, "
            f"precios={fallo['precios_observados']}"
        )

        try:
            insertar_ejecucion(
                conn=conn,
                modulo="consenso_alerta",
                ok=0,
                mensaje=mensaje_alerta,
            )
            alertas += 1
            logger.warning(mensaje_alerta)
        except Exception as exc:
            logger.error(f"Error registrando alerta: {exc}")

    return alertas


# ──────────────────────────────────────────────
# Orquestador principal — EJECUTAR()
# ──────────────────────────────────────────────

def ejecutar() -> dict:
    """Función principal que main.py llama para validar consenso.

    Returns:
        Dict con resumen de la ejecución, siempre incluye key "fuente".
    """
    resultados_validacion = []
    total_precios = 0
    total_consenso = 0
    total_alertas = 0

    try:
        # ── Paso 1: Conectar DB y obtener precios recientes ──
        from collector.db import conectar

        conn = conectar()
        rows = obtener_precios_recentes(conn)
        total_precios = len(rows)

        if not rows:
            logger.info("[consenso] No hay precios recientes para validar.")
            conn.close()
            return {
                "fuente": "consenso_validador",
                "total_consultados": 0,
                "con_senso_alcanzado": 0,
                "alertas": 0,
                "detalles": [],
            }

        logger.info(
            f"[consenso] Consultando {total_precios} precios de los últimos "
            f"{DIAS_RECENTES} días..."
        )

        # ── Paso 2: Agrupar por fecha + producto ──
        grupos = agrupar_precios(rows)
        logger.info(f"[consenso] {len(grupos)} grupos (fecha × producto) encontrados.")

        # ── Paso 3: Validar consenso para cada grupo ──
        validados = []
        no_consenso = []

        for (fecha, producto), grupo_rows in sorted(grupos.items()):
            res = validar_consenso_grupo(grupo_rows, producto, fecha)
            resultados_validacion.append(res)

            if res["consenso"]:
                total_consenso += 1
                validados.append(res)
                logger.info(
                    f"[OK] {fecha} | {producto}: Q{res['precio_valido']:.2f} "
                    f"({res['detalles']})"
                )
            else:
                no_consenso.append(res)
                logger.warning(
                    f"[!] {fecha} | {producto}: Sin consenso — {res['detalles']}"
                )

        # ── Paso 4: Marcar validados y registrar alertas ──
        marcados = marcar_precios_validados(conn, validados)
        total_alertas = registrar_alerta_no_consenso(conn, no_consenso)

        conn.close()

        logger.info(
            f"[consenso] Resumen: {total_consenso}/{len(grupos)} con consenso. "
            f"{total_alertas} alerta(s)."
        )

        return {
            "fuente": "consenso_validador",
            "total_consultados": total_precios,
            "grupos_evaluados": len(grupos),
            "con_senso_alcanzado": total_consenso,
            "sin_consenso": len(no_consenso),
            "alertas_registradas": total_alertas,
            "precio_validado": marcados,
            "detalles": resultados_validacion,
        }

    except Exception as exc:
        logger.error(f"[consenso] Error fatal: {exc}")
        return {
            "fuente": "consenso_validador",
            "error": str(exc),
            "total_consultados": 0,
            "con_senso_alcanzado": 0,
            "alertas": 0,
            "detalles": [],
        }


# ──────────────────────────────────────────────
# Entry point para ejecución directa
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Validador de Consenso Multifuente — Gasolina GT")
    print(f"Tolerancia: Q{TOLERANCIA_QUETLES:.2f} | "
          f"Fuentes mínimas: {MIN_FUENTES_CONSENSO}")
    print("=" * 60)

    resultado = ejecutar()

    print(f"\nFuente: {resultado['fuente']}")
    print(f"Total consultados: {resultado.get('total_consultados', 0)}")
    print(
        f"Con consenso: {resultado.get('con_senso_alcanzado', 0)}/"
        f"{resultado.get('grupos_evaluados', 0)}"
    )
    print(f"Alertas: {resultado.get('alertas_registradas', 0)}")

    if resultado.get("error"):
        print(f"Error: {resultado['error']}")
