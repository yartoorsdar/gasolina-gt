"""Orquestador principal y exportación JSON para gasolina-gt.

Ejecuta todos los collectors en secuencia:
  1. precios_mem    → precios actuales de combustible (PDF MEM)
  2. mem_html       → precios actuales HTML (Playwright, bypass Cloudflare)
  3. consenso_precios → validación por consenso multifuente (Q0.20 tolerancia)
  4. importar_historico → historial de precios (XLSX diario MEM)
  5. petroleo       → WTI (API OilPriceAPI)
  6. noticias       → feeds RSS clasificados

También exporta la DB a archivos JSON para uso externo o web.

Uso:
  python main.py --all              # ejecuta todo
  python main.py --precios-mem      # solo precios MEM actuales (PDF)
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
    from collector.db import conectar, insertar_ejecucion

    logger.info(f"=== Iniciando modulo: {nombre} ===")
    conn = conectar()

    try:
        inicio = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")
        exec_id = insertar_ejecucion(conn, modulo=nombre, ok=1, mensaje="iniciado")

        resultado = funcion()

        conn.execute(
            "UPDATE ejecuciones SET fin = ?, mensaje = ? WHERE id = ?",
            (
                datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00"),
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
                datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00"),
                f"error: {exc}",
                nombre,
            ),
        )
        conn.commit()

        logger.error(f"[{nombre}] Error: {exc}")
        return {"fuente": "error", "error": str(exc)}
    finally:
        conn.close()


# ──────────────────────────────────────────────
# Funciones de colección (wrappers con cfg)
# ──────────────────────────────────────────────

def ejecutar_precios_mem(cfg: dict = None) -> dict:
    """Wrapper para precios_mem.ejecutar que pasa la config."""
    from collector.precios_mem import ejecutar as _ejecutar

    # Patch: necesitamos pasar cfg a guardar_precios_en_db, pero ejecutar() no lo hace directamente. La solución es setear un global temporal o modificar el módulo. Para mantener compatibilidad, usamos la versión que ya funciona (precios_mem.ejecutar usa su propia carga de config).
    return _ejecutar()


def ejecutar_mem_html(cfg: dict = None) -> dict:
    """Wrapper para mem_html.ejecutar (Playwright HTML scraping)."""
    from collector.mem_html import ejecutar as _ejecutar

    return _ejecutar()


def ejecutar_fuentes_alternas(cfg: dict = None) -> dict:
    """Wrapper para fuentes_alternas.ejecutar (GPP + Chapin TV)."""
    from collector.fuentes_alternas import ejecutar as _ejecutar

    return _ejecutar()


def ejecutar_consenso(cfg: dict = None) -> dict:
    """Wrapper para consenso_precios.ejecutar (validación multifuente)."""
    from collector.consenso_precios import ejecutar as _ejecutar

    return _ejecutar()


def ejecutar_consenso_retry(cfg: dict = None) -> dict:
    """Wrapper para consenso_precios.ejecutar_con_reintentos (modo retry)."""
    from collector.consenso_precios import (
        MAX_REINTENTOS,
        RETRY_INTERVAL_SEGUNDOS,
        ejecutar_con_reintentos as _ejecutar,
    )

    return _ejecutar(
        intervalo_segundos=RETRY_INTERVAL_SEGUNDOS,
        max_reintentos=MAX_REINTENTOS,
        exportar_json=True,
    )


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
        ("precios_mem", ejecutar_precios_mem),
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

def _fila_a_dict(fila, columns=None):
    """Convierte una fila SQLite (sqlite3.Row o tuple) a dict serializable."""
    if fila is None:
        return {}

    # sqlite3.Row tiene keys() y funciona como dict
    if hasattr(fila, "keys"):
        data = {}
        for key in fila.keys():
            val = fila[key]
            # Convertir tipos no-serializables a str
            if isinstance(val, (datetime,)):
                data[key] = val.isoformat()
            elif val is None:
                data[key] = None
            else:
                data[key] = val
        return data

    # Tuple o list — usar columns si se proporcionan
    if columns:
        return {col: (val if val is not None else None) for col, val in zip(columns, fila)}

    return dict(enumerate(fila))


def exportar_json(cfg: dict = None, output_dir: Path = None, conn: sqlite3.Connection = None) -> dict:
    """Exporta toda la base de datos a archivos JSON.

    Archivos generados en data/export/:
      - precios_combustible.json  (todos los precios con regimen y impuestos)
      - petroleo.json             (WTI históricos)
      - noticias.json             (noticias RSS clasificadas)
      - resumen.json              (resumen para dashboard web)

    Args:
        cfg: Configuración cargada desde config.json (opcional).
        output_dir: Directorio de salida (default: data/export/).
        conn: Conexión DB existente (para tests, None = crea conexión propia).

    Returns:
        Dict con rutas de archivos generados y conteos.
    """
    if cfg is None:
        cfg = cargar_config()

    export_dir = output_dir or Path(__file__).resolve().parent.parent / "data" / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    from collector.db import (
        conectar,
        obtener_precios_actuales,
        obtener_historial_precios,
        obtener_ultimo_precio,
        obtener_noticias,
    )

    if conn is None:
        conn = conectar()

    ahora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")

    resultados_export = {
        "archivos": {},
        "total_registros": 0,
        "generado_at": ahora,
    }

    # ── 1. Precios actuales de combustible y petróleo ──
    precios_actuales_rows = obtener_precios_actuales(conn)
    precios_actuales = [_fila_a_dict(r) for r in precios_actuales_rows]
    path_precios = export_dir / "precios_combustible.json"
    _write_json(path_precios, {"precios": precios_actuales, "actualizado_at": ahora})
    resultados_export["archivos"]["precios_combustible"] = str(path_precios)
    resultados_export["total_registros"] += len(precios_actuales)

    # ── 2. Historial de precios (combustible + petróleo, últimos 365 días) ──
    historial = []
    for producto in ["superior", "regular", "diésel", "wti"]:
        rows = obtener_historial_precios(conn, producto=producto, dias=365)
        historial.extend(_fila_a_dict(r) for r in rows)

    path_historico = export_dir / "historial_precios.json"
    _write_json(
        path_historico,
        {
            "datos": historial,
            "productos": list(set(d.get("producto") for d in historial if d)),
            "total_registros": len(historial),
            "actualizado_at": ahora,
        },
    )
    resultados_export["archivos"]["historial_precios"] = str(path_historico)
    resultados_export["total_registros"] += len(historial)

    # ── 3. Petróleo actual (wti desde la única tabla) ──
    petroleo_actual = []
    row = obtener_ultimo_precio(conn, "wti")
    if row:
        petroleo_actual.append({
            "referencia": "wti",
            "fecha": row["fecha"],
            "usd_barril": row["precio"],
            "fuente": row["fuente"],
        })
    path_petroleo = export_dir / "petroleo.json"
    _write_json(
        path_petroleo, {"precios": petroleo_actual, "actualizado_at": ahora}
    )
    resultados_export["archivos"]["petroleo"] = str(path_petroleo)
    resultados_export["total_registros"] += len(petroleo_actual)

    # ── 4. Historial de petróleo (últimos 90 días, solo wti) ──
    hist_petroleo = []
    rows = obtener_historial_precios(conn, producto="wti", dias=90)
    hist_petroleo.extend(_fila_a_dict(r) for r in rows)

    path_hist_petroleo = export_dir / "historial_petroleo.json"
    _write_json(
        path_hist_petroleo,
        {
            "datos": hist_petroleo,
            "total_registros": len(hist_petroleo),
            "actualizado_at": ahora,
        },
    )
    resultados_export["archivos"]["historial_petroleo"] = str(path_hist_petroleo)
    resultados_export["total_registros"] += len(hist_petroleo)

    # ── 5. Noticias recientes (últimos 30 días) ──
    noticias_rows = obtener_noticias(conn, dias=30)
    noticias = [_fila_a_dict(r) for r in noticias_rows]
    path_noticias = export_dir / "noticias.json"
    _write_json(
        path_noticias, {"noticias": noticias, "total_registros": len(noticias)}
    )
    resultados_export["archivos"]["noticias"] = str(path_noticias)
    resultados_export["total_registros"] += len(noticias)

    # ── 6. Resumen para dashboard web ──
    resumen = {
        "actualizado_at": ahora,
        "precios_combustible": precios_actuales,
        "petroleo": petroleo_actual,
        "noticias_count": len(noticias),
        "ultimas_noticias": noticias[:10],  # top 10 más recientes
    }

    path_resumen = export_dir / "resumen.json"
    _write_json(path_resumen, resumen)
    resultados_export["archivos"]["resumen"] = str(path_resumen)

    conn.close()

    logger.info(
        f"Exportación completada: {resultados_export['total_registros']} registros -> {export_dir}"
    )
    return resultados_export


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
  python main.py --precios-mem          Solo precios MEM actuales
  python main.py --historico            Solo importar histórico (XLSX)
  python main.py --petroleo             Solo WTI (OilPriceAPI)
  python main.py --noticias             Solo feeds RSS
  python main.py --export               Exporta DB a JSON
  python main.py --all --export         Ejecuta todo y exporta
        """,
    )

    parser.add_argument(
        "--all", action="store_true", help="Ejecutar todos los colectores"
    )
    parser.add_argument("--precios-mem", action="store_true", help="Precios MEM actuales (PDF)")
    parser.add_argument("--mem-html", action="store_true", help="Precios MEM HTML (Playwright)")
    parser.add_argument(
        "--consenso", action="store_true",
        help="Validación por consenso multifuente (Q0.20 tolerancia)",
    )
    parser.add_argument(
        "--consenso-retry", action="store_true",
        help="Consenso con reintentos: 45min hasta lograr consenso + exportar JSON",
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

    args = parser.parse_args()

    # Si no se especifica nada, ejecutar todo por defecto
    if not any([args.all, args.precios_mem, args.mem_html, args.consenso,
                args.consenso_retry, args.historico, args.petroleo, args.noticias,
                args.alternos]):
        args.all = True

    cfg = cargar_config()

    # Ejecutar colectores solicitados
    modulos_activas = []
    if args.all or args.precios_mem:
        modulos_activas.append(("precios_mem", ejecutar_precios_mem))
    if args.all or args.mem_html:
        modulos_activas.append(("mem_html", ejecutar_mem_html))
    if args.all or args.alternos:
        modulos_activas.append(("fuentes_alternas", ejecutar_fuentes_alternas))
    if args.all or args.consenso_retry:
        # --consenso-retry ejecuta el modo retry (reemplaza a consenso normal)
        modulos_activas.append(("consenso_retry", ejecutar_consenso_retry))
    elif args.all or args.consenso:
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

    # Exportar si se solicitó
    if args.export and resultados:
        exportar_json(cfg=cfg)

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


if __name__ == "__main__":
    main()
