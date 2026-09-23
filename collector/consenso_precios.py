"""Validador de consenso multifuente para precios de combustible — Gasolina GT.

Algoritmo de validación por consenso:
  1. Consulta todos los precios recientes (últimos 7 días) desde la DB.
  2. Agrupa por fecha + producto.
  3. Para cada grupo, verifica si ≥2 fuentes reportan precio con diferencia ≤ Q0.20.
  4. Si hay consenso → marca como válido y actualiza campo "validado".
  5. Si no hay consenso → alerta en log + registro en tabla ejecuciones (ok=0).

Modo retry: si se activa, reintentará cada 45 min hasta lograr consenso.
  Útil para cron diario a las 3AM que espera datos actualizados de fuentes.

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
  v1.1.0 — 2026-09-23 — Modo retry: reintentos cada 45 min hasta consenso
         — Actualización automática de dashboard al lograr consenso
  v1.2.0 — 2026-09-23 — Integración Gemini AI para validación inteligente
         — Gemini evalúa contexto entre fuentes (nacional vs metro)
         — Confirma o rechaza consenso con razonamiento

Uso:
  python collector/main.py --consenso            # valida una vez
  python collector/main.py --consenso --retry    # modo retry (45min interval)
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
MIN_FUENTES_CONSENSO = 2           # mínimo de fuentes que deben coincidir
DIAS_RECENTES = 7                  # consultar precios de los últimos N días

# Fuentes consideradas válidas (debe coincidir con el campo "fuente" en DB)
FUENTES_VALIDAS = {
    "MEM PDF",
    "MEM HTML",
    "Prensa Libre",
    "GNews GT",
    "GlobalPetrolPrices",
    "Chapin TV",
}

# ──────────────────────────────────────────────
# Configuración de retry (modo diario 3AM + reintentos)
# ──────────────────────────────────────────────

RETRY_INTERVAL_SEGUNDOS = 2700       # 45 minutos entre reintentos
MAX_REINTENTOS = 8                   # máximo de intentos antes de rendirse
                                  # (8 × 45min = 6 horas, suficiente para que
                                  #  las fuentes actualicen sus datos)


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
              'MEM PDF', 'MEM HTML', 'Prensa Libre', 'GNews GT',
              'GlobalPetrolPrices', 'Chapin TV'
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
# Validación con IA (Gemini) — Consenso inteligente
# ──────────────────────────────────────────────

def _obtener_config_llm() -> dict | None:
    """Carga la config del LLM desde config.json."""
    import json as _json
    
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = _json.load(f)
        llm_cfg = cfg.get("llm", {})
        if llm_cfg.get("base_url") and llm_cfg.get("model"):
            return llm_cfg
    except Exception:
        pass
    return None


def validar_con_ia(
    fecha: str,
    producto: str,
    precios_observados: list[dict],
    resultado_reglas: dict,
) -> dict | None:
    """Usa Gemini para evaluar si los precios de múltiples fuentes son consistentes.

    Esta función es un validador INTELIGENTE que complementa las reglas básicas:
      - Considera el contexto de cada fuente (nacional vs metro)
      - Evalúa si diferencias > Q0.20 son razonables dado el origen
      - Proporciona razonamiento humano-legible

    Args:
        fecha: Fecha de observación (YYYY-MM-DD).
        producto: Nombre del producto ('superior', 'regular', 'diessel').
        precios_observados: Lista de dicts con {precio, fuente, tipo}.
        resultado_reglas: Resultado de validar_consenso_grupo (reglas básicas).

    Returns:
        Dict con {consenso, precio_valido, razonamiento} o None si falla.
    """
    llm_cfg = _obtener_config_llm()
    if not llm_cfg:
        return None

    import os as _os
    
    base_url = llm_cfg.get("base_url", "").strip()
    model = llm_cfg.get("model", "").strip()
    api_key = _os.environ.get("GEMINI_API_KEY", llm_cfg.get("api_key", "")).strip()

    if not base_url or not model:
        return None

    # Construir prompt con contexto de fuentes
    fuente_descripcion = {
        "MEM PDF": "Precios oficiales MEM (Ciudad de Guatemala, autoservicio)",
        "MEM HTML": "Precios oficiales MEM via web (Ciudad de Guatemala)",
        "GlobalPetrolPrices": "Promedio nacional Guatemala (no solo capital)",
        "Chapin TV": "Sondeo en estaciones de servicio (área metropolitana)",
        "Prensa Libre": "Reporte de prensa (área metropolitana)",
    }

    precios_text = "\n".join(
        f"- Q{p['precio']:.2f} via {p.get('fuente', 'desconocida')} ({fuente_descripcion.get(p.get('fuente', ''), 'fuente externa')})"
        for p in precios_observados
    )

    contexto = (
        f"Eres un analista de precios de combustible en Guatemala.\n\n"
        f"FECHA: {fecha}\nPRODUCTO: {producto}\n\n"
        f"Precios observados de diferentes fuentes:\n{precios_text}\n\n"
        f"Reglas de negocio:\n"
        f"- MEM reporta precios para Ciudad de Guatemala (área metropolitana)\n"
        f"- GlobalPetrolPrices reporta promedio nacional (puede diferir +/- Q1.00)\n"
        f"- Chapin TV/Prensa Libre son sondeos en estaciones (variación natural)\n"
        f"- Tolerancia esperada entre fuentes similares: Q0.20\n"
        f"- Diferencia MEM vs Nacional promedio: hasta Q1.50 es razonable\n\n"
        f"EVALUA:\n"
        f"1. ¿Los precios son consistentes? (si/parcial/no)\n"
        f"2. ¿Cuál es el precio más confiable para usar?\n"
        f"3. ¿Hay alguna fuente que deba descartarse?\n\n"
        f"RESPONDE SOLO EN JSON con estas keys:\n"
        f"- consenso: 'si', 'parcial' o 'no'\n"
        f"- precio_recomendado: numero (el mas confiable)\n"
        f"- razonamiento: texto corto explicando tu decision\n"
    )

    try:
        import requests as _requests
        
        if "generativelanguage" in base_url:
            resp = _requests.post(
                f"{base_url}/models/{model}:generateContent?key={api_key}",
                json={
                    "contents": [{
                        "parts": [
                            {"text": contexto}
                        ]
                    }],
                    "generationConfig": {"temperature": 0.2},
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["candidates"][0]["content"]["parts"][0]["text"]
        else:
            # OpenAI compatible
            resp = _requests.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Eres un analista de precios de combustible en Guatemala. Responde solo con JSON."},
                        {"role": "user", "content": contexto},
                    ],
                    "temperature": 0.2,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]

        # Parsear JSON de respuesta
        import re as _re
        json_match = _re.search(
            r"\{[^}]*consenso[^}]*precio_recomendado[^}]*razonamiento[^}]*\}",
            content, _re.DOTALL
        )
        if json_match:
            parsed = _json.loads(json_match.group())
            
            consenso_str = str(parsed.get("consenso", "")).lower()
            consenso = consenso_str in ("si", "sí") or consenso_str == "parcial"
            
            # Si es "parcial", usar precio_reglas si existe
            if consenso_str == "parcial" and resultado_reglas.get("precio_valido"):
                precio_reco = resultado_reglas["precio_valido"]
            else:
                try:
                    precio_reco = float(parsed.get("precio_recomendado"))
                except (ValueError, TypeError):
                    precio_reco = resultado_reglas.get("precio_valido")

            return {
                "consenso": consenso,
                "precio_valido": round(precio_reco, 2) if precio_reco else None,
                "razonamiento": parsed.get("razonamiento", ""),
                "fuente_ia": "gemini",
            }

    except Exception as exc:
        logger.warning(f"[consenso-ia] Error Gemini: {exc}")

    return None


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

        # ── Paso 3: Validar consenso para cada grupo (reglas básicas) ──
        validados = []
        no_consenso = []
        ia_usada = 0

        for (fecha, producto), grupo_rows in sorted(grupos.items()):
            res = validar_consenso_grupo(grupo_rows, producto, fecha)
            resultados_validacion.append(res)

            # Extraer precios con sus fuentes para la IA
            precios_para_ia = []
            for row in grupo_rows:
                fuente = row["fuente"] if "fuente" in row.keys() else "desconocida"
                precios_para_ia.append({
                    "precio": row["precio"],
                    "fuente": fuente,
                })

            if res["consenso"]:
                total_consenso += 1
                validados.append(res)
                logger.info(
                    f"[OK] {fecha} | {producto}: Q{res['precio_valido']:.2f} "
                    f"({res['detalles']})"
                )
            else:
                # ── Paso 3b: Intentar con IA como desempate ──
                if len(grupo_rows) >= 2 and _obtener_config_llm():
                    logger.info(
                        f"[consenso-ia] Evaluando {fecha} | {producto} "
                        f"con Gemini (reglas fallaron)..."
                    )
                    res_ia = validar_con_ia(
                        fecha, producto, precios_para_ia, res
                    )
                    
                    if res_ia and res_ia.get("consenso"):
                        # La IA confirma consenso → actualizar resultado
                        res["precio_valido"] = res_ia["precio_valido"]
                        res["razonamiento_ia"] = res_ia["razonamiento"]
                        res["fuente_ia"] = "gemini"
                        total_consenso += 1
                        validados.append(res)
                        no_consenso.remove(res)
                        ia_usada += 1
                        
                        logger.info(
                            f"[IA+OK] {fecha} | {producto}: Q{res['precio_valido']:.2f} "
                            f"(reglas=no, IA=si — {res_ia['razonamiento'][:50]}...)"
                        )
                    else:
                        no_consenso.append(res)
                        logger.warning(
                            f"[!] {fecha} | {producto}: Sin consenso — {res['detalles']}"
                        )
                else:
                    no_consenso.append(res)
                    logger.warning(
                        f"[!] {fecha} | {producto}: Sin consenso — {res['detalles']}"
                    )

        if ia_usada > 0:
            logger.info(f"[consenso] IA usó como desempate: {ia_usada} caso(s)")

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
            "ia_usada_desempate": ia_usada,
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
# Ejecución con reintentos (modo diario 3AM)
# ──────────────────────────────────────────────

def ejecutar_con_reintentos(
    intervalo_segundos: int = RETRY_INTERVAL_SEGUNDOS,
    max_reintentos: int = MAX_REINTENTOS,
    exportar_json: bool = True,
) -> dict:
    """Ejecuta validación de consenso con reintentos automáticos.

    Modo operativo para cron diario a las 3AM:
      1. Ejecutar consenso una vez
      2. Si NO hay consenso → esperar 45 min y reintentar
      3. Repetir hasta lograr consenso o alcanzar max_reintentos
      4. Cuando se logra consenso → exportar JSON para actualizar dashboard

    Args:
        intervalo_segundos: Segundos entre reintentos (default: 2700 = 45 min).
        max_reintentos: Máximo de intentos antes de rendirse (default: 8).
        exportar_json: Si True, exporta JSON al lograr consenso.

    Returns:
        Dict con resumen final incluyendo historial de intentos.
    """
    import time as _time

    logger.info(
        f"[consenso-retry] Iniciando modo retry: "
        f"intervalo={intervalo_segundos}s, max_intentos={max_reintentos}"
    )

    historial = []
    consenso_logrado = False

    for intento in range(1, max_reintentos + 1):
        logger.info(f"[consenso-retry] Intento {intento}/{max_reintentos}...")

        resultado = ejecutar()
        historial.append({
            "intento": intento,
            "resultado": resultado,
            "timestamp": datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00"),
        })

        # Verificar si se logró consenso
        if resultado.get("con_senso_alcanzado", 0) > 0:
            consenso_logrado = True
            logger.info(
                f"[consenso-retry] [OK] Consenso logrado en intento {intento}! "
                f"{resultado['con_senso_alcanzado']} productos validados."
            )

            # Exportar JSON para actualizar dashboard con precios de hoy
            if exportar_json:
                try:
                    from collector.main import exportar_json as _exportar
                    cfg = None
                    try:
                        from collector.main import cargar_config
                        cfg = cargar_config()
                    except Exception:
                        pass

                    _result_export = _exportar(cfg=cfg)
                    logger.info(
                        f"[consenso-retry] Dashboard actualizado con "
                        f"{_result_export.get('total_registros', 0)} registros."
                    )
                except Exception as exc:
                    logger.error(f"[consenso-retry] Error exportando JSON: {exc}")

            # Construir resultado final con historial
            return {
                "fuente": "consenso_validador",
                "modo_retry": True,
                "intento_logrado": intento,
                "total_intentos": len(historial),
                "con_senso_alcanzado": resultado["con_senso_alcanzado"],
                "alertas_registradas": resultado.get("alertas_registradas", 0),
                "exportado_json": exportar_json,
                "historial": historial,
            }

        # Si no hay consenso y quedan intentos, esperar antes de reintentar
        if intento < max_reintentos:
            logger.info(
                f"[consenso-retry] Sin consenso aún. Esperando "
                f"{intervalo_segundos}s ({intervalo_segundos/60:.0f} min)..."
            )
            _time.sleep(intervalo_segundos)

    # Si llegamos aquí, se acabaron los intentos sin consenso
    logger.warning(
        f"[consenso-retry] Agotados {max_reintentos} intentos sin consenso. "
        "Se necesitará intervención manual o espera a la próxima ejecución."
    )

    return {
        "fuente": "consenso_validador",
        "modo_retry": True,
        "intento_logrado": None,
        "total_intentos": len(historial),
        "con_senso_alcanzado": historial[-1]["resultado"].get("con_senso_alcanzado", 0) if historial else 0,
        "alertas_registradas": historial[-1]["resultado"].get("alertas_registradas", 0) if historial else 0,
        "exportado_json": False,
        "historial": historial,
    }


# ──────────────────────────────────────────────
# Entry point para ejecución directa
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import argparse as _argparse

    parser = _argparse.ArgumentParser(
        description="Gasolina GT — Validador de consenso multifuente",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="""\
Ejemplos:
  python collector/consenso_precios.py              # Valida una vez
  python collector/consenso_precios.py --retry      # Modo retry (45min interval)
  python collector/main.py --consenso                # Desde orchestrador
  python collector/main.py --consenso --retry        # Con reintentos
        """,
    )

    parser.add_argument(
        "--retry", action="store_true",
        help="Modo retry: reintentar cada 45 min hasta lograr consenso",
    )
    parser.add_argument(
        "--interval-min", type=int, default=45,
        help="Intervalo en minutos entre reintentos (default: 45)",
    )

    args = parser.parse_args()

    print("=" * 60)
    print("Validador de Consenso Multifuente — Gasolina GT")
    print(f"Tolerancia: Q{TOLERANCIA_QUETLES:.2f} | "
          f"Fuentes mínimas: {MIN_FUENTES_CONSENSO}")
    if args.retry:
        print(f"Modo RETRY: intervalo={args.interval_min}min, "
              f"max_intentos={MAX_REINTENTOS}")
    print("=" * 60)

    if args.retry:
        intervalo_seg = args.interval_min * 60
        resultado = ejecutar_con_reintentos(
            intervalo_segundos=intervalo_seg,
            max_reintentos=MAX_REINTENTOS,
            exportar_json=True,
        )
    else:
        resultado = ejecutar()

    print(f"\nFuente: {resultado['fuente']}")
    if resultado.get("modo_retry"):
        intento = resultado.get("intento_logrado")
        total = resultado.get("total_intentos", 0)
        print(f"Modo retry: consenso en intento {intento}/{total}"
              if intento else f"Modo retry: sin consenso después de {total} intentos")
    else:
        print(
            f"Con consenso: {resultado.get('con_senso_alcanzado', 0)}/"
            f"{resultado.get('grupos_evaluados', 0)}"
        )
    print(f"Alertas: {resultado.get('alertas_registradas', 0)}")

    if resultado.get("error"):
        print(f"Error: {resultado['error']}")
