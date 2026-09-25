"""Orquestador principal y exportación JSON para gasolina-gt.

Ejecuta todos los collectors en secuencia:
  1. mem_html       → precios actuales HTML (Playwright, bypass Cloudflare)
  2. consenso_precios → validación por consenso multifuente (Q0.20 tolerancia)
  3. importar_historico → historial de precios (XLSX diario MEM)
  4. petroleo       → WTI (API OilPriceAPI)
  5. noticias       → feeds RSS clasificados

Exporta la DB a data/export/consolidado.json (único archivo del dashboard) y,
con --memoria, el historial de cada producto a data/db/<producto>.csv.

Uso:
  python main.py --all              # ejecuta todo
  python main.py --mem-html         # solo scraping HTML del MEM
  python main.py --consenso         # validación por consenso multifuente
  python main.py --historico        # solo importar histórico
  python main.py --petroleo         # solo Brent/WTI
  python main.py --noticias         # solo RSS feeds
  python main.py --export           # exporta DB a JSON
  python main.py --all --export     # ejecuta todo y exporta
"""

import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Asegurar que el root del proyecto esté en sys.path para imports relativos
if __name__ == "__main__":
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))


# ──────────────────────────────────────────────
# Configuración de logging
# ──────────────────────────────────────────────

LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(
            LOG_DIR / f"gasolina_{datetime.now().strftime('%Y%m%d')}.log",
            encoding="utf-8",
        ),
        logging.StreamHandler(sys.stdout),
    ],
)

logger = logging.getLogger("main")

# ──────────────────────────────────────────────
# Carga de configuración
# ──────────────────────────────────────────────

def cargar_config() -> dict:
    """Carga config.json del root del proyecto."""
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# Ejecutores individuales (wrappers con logging)
# ──────────────────────────────────────────────

def _ejecutar_modulo(nombre: str, funcion) -> dict:
    """Ejecuta un módulo y registra éxito/fracaso en DB + logging."""
    from collector.db import ahora_gt_iso, conectar, insertar_ejecucion

    logger.info(f"=== Iniciando modulo: {nombre} ===")
    conn = conectar()

    try:
        inicio = ahora_gt_iso()
        exec_id = insertar_ejecucion(conn, modulo=nombre, ok=1, mensaje="iniciado")

        resultado = funcion()

        conn.execute(
            "UPDATE ejecuciones SET fin = ?, mensaje = ? WHERE id = ?",
            (
                ahora_gt_iso(),
                f"completado — {resultado.get('fuente', 'desconocido')}",
                exec_id,
            ),
        )
        conn.commit()

        logger.info(f"[{nombre}] Completado. Fuente: {resultado.get('fuente')}")
        return resultado

    except Exception as exc:
        conn.execute(
            "UPDATE ejecuciones SET fin = ?, ok = 0, mensaje = ? WHERE id IN "
            "(SELECT id FROM ejecuciones WHERE modulo = ? ORDER BY inicio DESC LIMIT 1)",
            (
                ahora_gt_iso(),
                f"error: {exc}",
                nombre,
            ),
        )
        conn.commit()

        logger.error(f"[{nombre}] Error: {exc}")
        return {"fuente": "error", "error": str(exc)}
    finally:
        conn.commit()  # Forzar flush antes de cerrar
        conn.close()


# ──────────────────────────────────────────────
# Funciones de colección (wrappers con cfg)
# ──────────────────────────────────────────────

def ejecutar_mem_html(cfg: dict = None) -> dict:
    """Wrapper para mem_html.ejecutar (Playwright HTML scraping)."""
    from collector.mem_html import ejecutar as _ejecutar

    return _ejecutar()


def ejecutar_fuentes_alternas(cfg: dict = None) -> dict:
    """Wrapper para fuentes_alternas.ejecutar (GPP + Chapin TV)."""
    from collector.fuentes_alternas import ejecutar as _ejecutar

    return _ejecutar()


def ejecutar_consenso(cfg: dict = None) -> dict:
    """Wrapper para consenso_precios.ejecutar (consejo multifuente de precios)."""
    from collector.consenso_precios import ejecutar as _ejecutar

    return _ejecutar(cfg=cfg)


def ejecutar_historico(cfg: dict = None) -> dict:
    """Wrapper para importar_historico.ejecutar."""
    from collector.importar_historico import ejecutar as _ejecutar

    return _ejecutar()


def ejecutar_petroleo(cfg: dict = None) -> dict:
    """Wrapper para petroleo.ejecutar."""
    from collector.petroleo import ejecutar as _ejecutar

    return _ejecutar(cfg=cfg)


def ejecutar_noticias(cfg: dict = None) -> dict:
    """Wrapper para noticias.ejecutar."""
    from collector.noticias import ejecutar as _ejecutar

    return _ejecutar(cfg=cfg)


# ──────────────────────────────────────────────
# Orquestador principal
# ──────────────────────────────────────────────

def ejecutar_todo(cfg: dict = None, exportar: bool = False) -> list[dict]:
    """Ejecuta todos los collectors en secuencia.

    Args:
        cfg: Configuración cargada desde config.json (opcional).
        exportar: Si True, exporta la DB a JSON después de ejecutar todo.

    Returns:
        Lista de resultados de cada módulo ejecutado.
    """
    if cfg is None:
        cfg = cargar_config()

    logger.info("=" * 60)
    logger.info("gasolina-gt — Orquestador principal")
    logger.info("=" * 60)

    modulos = [
        ("mem_html", ejecutar_mem_html),
        ("consenso_precios", ejecutar_consenso),
        ("historico", ejecutar_historico),
        ("petroleo", ejecutar_petroleo),
        ("noticias", ejecutar_noticias),
    ]

    resultados = []
    for nombre, funcion in modulos:
        try:
            resultado = _ejecutar_modulo(nombre, funcion)
            resultados.append({"modulo": nombre, "resultado": resultado})
        except Exception as exc:
            logger.error(f"Fallo ejecutando {nombre}: {exc}")
            resultados.append(
                {"modulo": nombre, "resultado": {"fuente": "error", "error": str(exc)}}
            )

    # Resumen final
    logger.info("-" * 60)
    logger.info("Resumen de ejecución:")
    for r in resultados:
        mod = r["modulo"]
        res = r["resultado"]
        fuente = res.get("fuente", "?")
        insertados = res.get("insertados", 0) or 0
        logger.info(f"  {mod}: fuente={fuente}, insertados={insertados}")

    # Exportar si se solicitó
    if exportar:
        logger.info("Exportando datos a JSON...")
        exportar_json(cfg=cfg)

    return resultados


# ──────────────────────────────────────────────
# Exportación a JSON
# ──────────────────────────────────────────────

# Un año se promedia con datos diarios solo si están casi completos; si no
# (ej. 2026 con huecos), el promedio de los meses oficiales es más fiel.
DIAS_MIN_ANUAL_DIARIO = 300


def _promedios_anuales(
    conn: sqlite3.Connection, producto: str, semilla: list[dict], mensual: list[dict] = ()
) -> list[dict]:
    """Promedio por año, por prioridad de fuente:

    1. diario   — si el año tiene >= DIAS_MIN_ANUAL_DIARIO días con dato
    2. mensual  — promedio de los meses oficiales disponibles del año
    3. semilla  — valor anual histórico (años sin diarios ni mensuales)
    4. diario parcial — último recurso si no hay nada mejor
    """
    diario = {
        int(r[0]): {"anio": int(r[0]), "promedio": round(r[1], 2), "dias": r[2], "meses": None, "fuente": "diario"}
        for r in conn.execute(
            "SELECT substr(fecha, 1, 4), avg(precio), count(*) FROM precios "
            "WHERE producto = ? GROUP BY 1",
            (producto,),
        )
    }
    meses: dict[int, list[float]] = {}
    fuente_mensual = {}
    for m in mensual:
        if m["producto"] == producto:
            meses.setdefault(m["anio"], []).append(m["promedio"])
            fuente_mensual[m["anio"]] = m["fuente"]

    anual = {}
    for anio in set(diario) | set(meses) | {s["anio"] for s in semilla if s["producto"] == producto}:
        if anio in diario and diario[anio]["dias"] >= DIAS_MIN_ANUAL_DIARIO:
            anual[anio] = diario[anio]
        elif anio in meses:
            v = meses[anio]
            anual[anio] = {"anio": anio, "promedio": round(sum(v) / len(v), 2), "dias": None,
                           "meses": len(v), "fuente": fuente_mensual[anio]}
        elif any(s["producto"] == producto and s["anio"] == anio for s in semilla):
            s = next(s for s in semilla if s["producto"] == producto and s["anio"] == anio)
            anual[anio] = {"anio": anio, "promedio": s["promedio"], "dias": None, "meses": None, "fuente": s["fuente"]}
        else:
            anual[anio] = diario[anio]
    return [anual[a] for a in sorted(anual)]


def construir_consolidado(conn: sqlite3.Connection, dias_historial: int = 365) -> dict:
    """Arma el JSON único del dashboard. Misma forma para todos los productos:

    productos.<codigo> = {nombre, categoria, unidad, orden,
                          actual: {fecha, precio, fuente, fetched_at} | null,
                          historial: [{fecha, precio}]  (últimos N días),
                          mensual: [{anio, mes, promedio}]  (oficial MEM),
                          anual: [{anio, promedio, dias, meses, fuente}],
                          modalidades: {<modalidad>: {nombre, actual, consenso, historial (30 días)}}}

    `actual`/`historial` de primer nivel = modalidad principal (autoservicio / spot).
    consenso = veredicto del consejo multifuente {fecha, precio, confianza,
    n_coinciden, n_fuentes, fuentes:[{medio, precio, fecha, url, coincide, peso}]}.
    """
    from collector import noticias as _noticias_mod
    from collector.consenso_precios import precision_fuentes
    from collector.db import (
        COMBUSTIBLES, MODALIDADES, PRODUCTOS, ahora_gt_iso, hace_dias_gt, hoy_gt,
        leer_consenso, leer_historial, leer_ultimo, obtener_noticias,
    )
    from collector.memoria import leer_mensual, leer_semilla_anual

    hoy = hoy_gt()
    desde = hace_dias_gt(dias_historial)
    semilla = leer_semilla_anual()
    mensual = leer_mensual()

    def _actual(row):
        return {"fecha": row["fecha"], "precio": row["precio"], "fuente": row["fuente"],
                "fetched_at": row["fetched_at"]} if row else None

    def _consenso(row):
        if not row:
            return None
        return {"fecha": row["fecha"], "precio": row["precio"], "confianza": row["confianza"],
                "n_coinciden": row["n_coinciden"], "n_fuentes": row["n_fuentes"],
                "fuentes": json.loads(row["fuentes"])}

    desde_mod = hace_dias_gt(30)
    productos = {}
    for codigo, meta in sorted(PRODUCTOS.items(), key=lambda kv: kv[1]["orden"]):
        ultimo = leer_ultimo(conn, codigo)
        productos[codigo] = {
            "nombre": meta["nombre"],
            "categoria": meta["categoria"],
            "unidad": meta["unidad"],
            "orden": meta["orden"],
            "actual": {
                "fecha": ultimo["fecha"],
                "precio": ultimo["precio"],
                "fuente": ultimo["fuente"],
                "fetched_at": ultimo["fetched_at"],
            } if ultimo else None,
            "historial": [
                {"fecha": r["fecha"], "precio": r["precio"]}
                for r in leer_historial(conn, codigo, desde=desde)
            ],
            "mensual": [
                {"anio": m["anio"], "mes": m["mes"], "promedio": m["promedio"]}
                for m in mensual if m["producto"] == codigo
            ],
            "anual": _promedios_anuales(conn, codigo, semilla, mensual),
            "modalidades": {
                mod: {
                    "nombre": MODALIDADES[mod],
                    "actual": _actual(leer_ultimo(conn, codigo, mod)),
                    "consenso": _consenso(leer_consenso(conn, codigo, mod)),
                    "historial": [
                        {"fecha": r["fecha"], "precio": r["precio"]}
                        for r in leer_historial(conn, codigo, desde=desde_mod, modalidad=mod)
                    ],
                }
                for mod in meta["modalidades"]
            },
        }

    # Guardia de frescura (solo combustibles: un WTI de hoy no vuelve
    # "frescos" a precios de 2024).
    max_fecha = max(
        (productos[c]["actual"]["fecha"] for c in COMBUSTIBLES if productos[c]["actual"]),
        default="",
    )
    try:
        dias = (datetime.strptime(hoy, "%Y-%m-%d") - datetime.strptime(max_fecha, "%Y-%m-%d")).days
    except ValueError:
        dias = 999
    frescos = dias <= 30
    if not frescos:
        logger.warning(
            f"[export] PRECIOS DESACTUALIZADOS: max fecha '{max_fecha}' "
            f"(hace {dias}d, hoy {hoy}). Revisar colectores."
        )

    # Noticias: solo relevantes (mismo filtro que el colector: una DB vieja
    # puede traer columnas o deportes) y de los últimos DIAS_MAX_NOTICIA días.
    # Top 15 (el dashboard muestra 10): primero las que ya tienen título en español, luego relevancia
    # LLM (impacto GT) y fecha.
    noticias = [
        dict(r) for r in obtener_noticias(conn, dias=_noticias_mod.DIAS_MAX_NOTICIA)
        if _noticias_mod.es_relevante(r["titulo"], r["resumen_es"])
    ]
    ordenadas = sorted(
        noticias,
        key=lambda n: (bool(n.get("titulo_es")), n.get("relevancia") or 0, n.get("publicado_at") or ""),
        reverse=True,
    )
    # Defensa: una por hecho también aquí (DB local vieja o runs sin LLM).
    # El colector ya agrupó con LLM; esto solo quita casi-copias léxicas.
    top = _noticias_mod.quitar_duplicados(ordenadas)[:15]
    # Diagnóstico LLM (ground truth desde DB: cuántas de hoy traen español).
    total_hoy = conn.execute(
        "SELECT COUNT(*) FROM noticias WHERE substr(fetched_at,1,10)=?", (hoy,)
    ).fetchone()[0]
    es_hoy = conn.execute(
        "SELECT COUNT(*) FROM noticias WHERE substr(fetched_at,1,10)=? AND titulo_es IS NOT NULL",
        (hoy,),
    ).fetchone()[0]

    consejo = {
        "precision_fuentes": precision_fuentes(conn),
        "observaciones_7d": conn.execute(
            "SELECT COUNT(*) FROM observaciones WHERE fecha >= ?", (hace_dias_gt(7),)).fetchone()[0],
        "notas_leidas": conn.execute("SELECT COUNT(*) FROM articulos").fetchone()[0],
        "medios": sorted({r[0] for r in conn.execute("SELECT DISTINCT medio FROM observaciones")}),
    }

    return {
        "version": 3,
        "actualizado_at": ahora_gt_iso(),
        "precios_actualizados": frescos,
        "max_fecha_precios": max_fecha,
        "productos": productos,
        "consejo": consejo,
        "noticias": {
            "total": len(noticias),
            "top": top,
            "llm": {
                "titulos_es_hoy": es_hoy,
                "total_hoy": total_hoy,
                "error": getattr(_noticias_mod, "_last_llm_error", None),
                "key_fp": getattr(_noticias_mod, "_llm_key_fp", None),
            },
        },
    }


def exportar_json(cfg: dict = None, output_dir: Path = None, conn: sqlite3.Connection = None) -> dict:
    """Exporta la DB a data/export/consolidado.json (único archivo del dashboard).

    Args:
        cfg: se acepta por compatibilidad de llamadas; no se usa.
        output_dir: Directorio de salida (default: data/export/).
        conn: Conexión DB existente (para tests, None = crea conexión propia).

    Returns:
        Dict con ruta del archivo generado y conteo de registros.
    """
    from collector.db import conectar

    export_dir = output_dir or Path(__file__).resolve().parent.parent / "data" / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    if conn is None:
        conn = conectar()

    consolidado = construir_consolidado(conn)
    conn.close()

    path = export_dir / "consolidado.json"
    _write_json(path, consolidado)

    total = sum(len(p["historial"]) for p in consolidado["productos"].values())
    total += consolidado["noticias"]["total"]
    logger.info(f"Exportación completada: {total} registros -> {path}")
    return {
        "archivos": {"consolidado": str(path)},
        "total_registros": total,
        "generado_at": consolidado["actualizado_at"],
    }


def _write_json(path: Path, data: dict) -> None:
    """Escribe un dict como JSON formateado con UTF-8."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────

def main():
    """Entry point para la línea de comandos."""
    import argparse as _argparse

    parser = _argparse.ArgumentParser(
        description="gasolina-gt — Orquestador de colectores de datos",
        formatter_class=_argparse.RawDescriptionHelpFormatter,
        epilog="""\
Ejemplos:
  python main.py --all                  Ejecuta todos los colectores
  python main.py --historico            Solo importar histórico (XLSX)
  python main.py --petroleo             Solo WTI (OilPriceAPI)
  python main.py --noticias             Solo feeds RSS
  python main.py --export               Exporta DB a JSON
  python main.py --all --export         Ejecuta todo y exporta
  python main.py --memoria --alternos --petroleo --noticias --export
                                         Memoria persistente + ciclo de CI
        """,
    )

    parser.add_argument(
        "--all", action="store_true", help="Ejecutar todos los colectores"
    )
    parser.add_argument("--mem-html", action="store_true", help="Precios MEM HTML (Playwright)")
    parser.add_argument(
        "--consenso", action="store_true",
        help="Validación por consenso multifuente (Q0.20 tolerancia)",
    )
    parser.add_argument("--historico", action="store_true", help="Importar histórico (XLSX)")
    parser.add_argument("--petroleo", action="store_true", help="WTI (OilPriceAPI)")
    parser.add_argument("--noticias", action="store_true", help="Feeds RSS")
    parser.add_argument(
        "--alternos", action="store_true",
        help="Fuentes alternas: GlobalPetrolPrices + Chapin TV",
    )
    parser.add_argument(
        "--export", action="store_true", help="Exportar base de datos a JSON"
    )
    parser.add_argument(
        "--memoria", action="store_true",
        help="Memoria persistente: importar data/db/<producto>.csv al inicio "
             "y re-exportarlo al final (la CI nace con DB vacía; así no se reinicia el historial)",
    )

    args = parser.parse_args()

    # Si no se especifica nada, ejecutar todo por defecto
    if not any([args.all, args.mem_html, args.consenso,
                args.historico, args.petroleo, args.noticias,
                args.alternos, args.memoria]):
        args.all = True

    cfg = cargar_config()

    # ── Memoria persistente: restaurar la tabla precios ANTES de que los
    # colectores escriban. La memoria siembra el historial; lo de HOY lo
    # definen las fuentes vivas (sus patrones delete-hoy-antes-de-insertar ya
    # garantizan "re-ejecutar un día = actualizar, no duplicar"). ──
    if args.memoria:
        from collector.memoria import importar_memoria
        logger.info("=== Restaurando memoria persistente ===")
        importar_memoria()

    # Ejecutar colectores solicitados
    modulos_activas = []
    if args.all or args.mem_html:
        modulos_activas.append(("mem_html", ejecutar_mem_html))
    if args.all or args.alternos:
        modulos_activas.append(("fuentes_alternas", ejecutar_fuentes_alternas))
    if args.all or args.consenso:
        modulos_activas.append(("consenso_precios", ejecutar_consenso))
    if args.all or args.historico:
        modulos_activas.append(("historico", ejecutar_historico))
    if args.all or args.petroleo:
        modulos_activas.append(("petroleo", ejecutar_petroleo))
    if args.all or args.noticias:
        modulos_activas.append(("noticias", ejecutar_noticias))

    resultados = []
    
    for nombre, funcion in modulos_activas:
        try:
            resultado = _ejecutar_modulo(nombre, funcion)
            resultados.append({"modulo": nombre, "resultado": resultado})
            
        except Exception as exc:
            logger.error(f"Fallo ejecutando {nombre}: {exc}")
            resultados.append(
                {"modulo": nombre, "resultado": {"fuente": "error", "error": str(exc)}}
            )

    # Exportar si se solicitó (y regenerar la memoria persistente junto con él)
    if args.export and resultados:
        exportar_json(cfg=cfg)
        if args.memoria:
            from collector.memoria import exportar_memoria
            logger.info("=== Regenerando memoria persistente ===")
            exportar_memoria()

    # Imprimir resumen
    logger.info("-" * 60)
    logger.info("Resumen:")
    for r in resultados:
        mod = r["modulo"]
        res = r["resultado"]
        fuente = res.get("fuente", "?")
        insertados = res.get("insertados", 0) or 0
        logger.info(f"  {mod}: fuente={fuente}, insertados={insertados}")

    if not resultados:
        # Solo exportar sin ejecutar colectores
        logger.info("Ejecutando solo exportación...")
        exportar_json(cfg=cfg)
        if args.memoria:
            from collector.memoria import exportar_memoria
            logger.info("=== Regenerando memoria persistente ===")
            exportar_memoria()


if __name__ == "__main__":
    main()
