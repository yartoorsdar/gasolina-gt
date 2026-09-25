"""Obtiene precios de petróleo WTI desde OilPriceAPI (tiempo real).

Fuente: https://www.oilpriceapi.com/
Endpoint: /v1/prices/latest?by_code=WTI_CRUDE_USD

El dashboard siempre muestra el precio más reciente de OilPriceAPI (hoy).
"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

# Asegurar que el root del proyecto esté en sys.path
if __name__ == "__main__":
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in os.sys.path:
        os.sys.path.insert(0, str(_root))

import requests
from dotenv import load_dotenv

_project_root = Path(__file__).resolve().parent.parent
_dotenv_path = _project_root / ".env"
if _dotenv_path.exists():
    load_dotenv(_dotenv_path)


# ──────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────

DEFAULT_API_KEY_ENV = "OILPRICEAPI_KEY"
BASE_URL = "https://api.oilpriceapi.com/v1/prices/latest"
HISTORICAL_URL = "https://api.oilpriceapi.com/v1/prices/historical"

CODES = {
    "wti":   "WTI_CRUDE_USD",
}


# ──────────────────────────────────────────────
# Helpers de API key y sesión
# ──────────────────────────────────────────────

def obtener_api_key(cfg: dict = None) -> str | None:
    """Obtiene la API key desde config.json o variable de entorno."""
    if cfg is not None:
        env_var = cfg.get("petroleo", {}).get("oilpriceapi_key_env", DEFAULT_API_KEY_ENV)
        return os.environ.get(env_var)
    return os.environ.get(DEFAULT_API_KEY_ENV)


def crear_session(api_key: str) -> requests.Session:
    """Crea una sesión con la API key de OilPriceAPI."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    })
    return session


# ──────────────────────────────────────────────
# Fetch desde OilPriceAPI
# ──────────────────────────────────────────────

def fetch_precio_petroleo(code: str, api_key: str) -> dict | None:
    """Obtiene el precio más reciente de un commodity.

    Args:
        code: Código del commodity (ej: "BRENT_CRUDE_USD").
        api_key: Clave de la API.

    Returns:
        Dict con keys: fecha, usd_barril, referencia, o None si falla.
    """
    session = crear_session(api_key)
    url = f"{BASE_URL}?by_code={code}"

    try:
        resp = session.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"[petroleo] Error al fetchear '{code}': {exc}")
        return None
    except ValueError as exc:
        print(f"[petroleo] JSON inválido para '{code}': {exc}")
        return None

    status = data.get("status")
    if status != "success":
        print(f"[petroleo] Status no success para '{code}': {data.get('message', '')}")
        return None

    petro_data = data.get("data", {})
    precio = petro_data.get("price")

    if precio is None:
        print(f"[petroleo] Sin precio para '{code}'")
        return None

    # Referencia siempre WTI
    referencia = "wti"

    # Fecha del response (created_at o as_of)
    created_at = petro_data.get("created_at", "") or petro_data.get("as_of", "")
    fecha_normalizada = ""
    if created_at:
        try:
            dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            fecha_normalizada = dt.strftime("%Y-%m-%d")
        except (ValueError, AttributeError):
            fecha_normalizada = created_at[:10]

    return {
        "fecha": fecha_normalizada or datetime.now(timezone(timedelta(hours=-6))).strftime("%Y-%m-%d"),
        "usd_barril": round(float(precio), 2),
        "referencia": referencia,
    }


def fetch_precios_petroleo(codes: dict = None, api_key: str = "") -> list[dict]:
    """Obtiene los precios más recientes de Brent y WTI."""
    if codes is None:
        codes = CODES

    resultados = []

    for ref, code in codes.items():
        print(f"[petroleo] Fetching {ref} ({code})...")
        data = fetch_precio_petroleo(code, api_key)
        if data:
            resultados.append(data)
            print(f"  -> {ref}: ${data['usd_barril']:.2f}/bbl ({data['fecha']})")

    return resultados


# ──────────────────────────────────────────────
# Integración con DB
# ──────────────────────────────────────────────

def guardar_precios_petroleo(precios: list[dict], cfg: dict = None) -> int:
    """Guarda precios de petróleo WTI en la DB (acumula historial diario)."""
    if cfg is None:
        import json
        config_path = _project_root / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    from collector.db import conectar, guardar_precios, hoy_gt
    conn = conectar()

    # Upsert: re-ejecutar hoy ACTUALIZA el WTI del día (historial intacto)
    hoy = hoy_gt()
    filas = [{"producto": "wti", "fecha": hoy, "precio": p["usd_barril"]} for p in precios]
    conteo = guardar_precios(conn, filas, fuente="OilPriceAPI")
    conn.close()
    return conteo["insertados"] + conteo["actualizados"]


def fetch_historial_wti(api_key: str, period: str = "past_month") -> list[dict]:
    """Serie diaria de WTI (promedio diario, solo días de mercado).

    period: past_week | past_month | past_year (past_year ≈ 500 días).

    Returns:
        [{fecha, usd_barril}] ordenada por fecha; [] si la API falla.
    """
    session = crear_session(api_key)
    url = f"{HISTORICAL_URL}?by_code=WTI_USD&period={period}&interval=daily"
    try:
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"[petroleo] Error historial WTI: {exc}")
        return []
    if data.get("status") != "success":
        print(f"[petroleo] Historial WTI sin éxito: {data.get('message', '')}")
        return []

    serie = {}
    for p in (data.get("data") or {}).get("prices") or []:
        fecha = (p.get("created_at") or "")[:10]
        precio = p.get("price")
        if fecha and precio is not None and not p.get("synthetic"):
            serie[fecha] = round(float(precio), 2)
    return [{"fecha": f, "usd_barril": v} for f, v in sorted(serie.items())]


def guardar_serie_petroleo(serie: list[dict]) -> int:
    """Guarda una serie diaria de WTI con SUS fechas (upsert)."""
    from collector.db import conectar, guardar_precios
    conn = conectar()
    filas = [{"producto": "wti", "fecha": p["fecha"], "precio": p["usd_barril"]} for p in serie]
    conteo = guardar_precios(conn, filas, fuente="OilPriceAPI")
    conn.close()
    return conteo["insertados"] + conteo["actualizados"]


def obtener_petroleo_actual(conn) -> list[dict]:
    """Obtiene el precio más reciente de wti desde la única tabla precios."""
    row = conn.execute(
        "SELECT * FROM precios WHERE producto='wti' ORDER BY fecha DESC, id DESC LIMIT 1"
    ).fetchone()

    if not row:
        return []

    return [{
        "referencia": "wti",
        "fecha": row["fecha"],
        "usd_barril": row["precio"],
    }]


def ejecutar(cfg: dict = None) -> dict:
    """Orquesta el flujo completo de obtención de precios."""
    if cfg is None:
        import json
        config_path = _project_root / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    resultados = {
        "fuente": "",
        "precios_encontrados": 0,
        "insertados": 0,
        "errores": [],
        "datos": [],
    }

    # Verificar API key
    api_key = obtener_api_key(cfg)
    if not api_key:
        msg = f"API key no configurada (variable: {DEFAULT_API_KEY_ENV})"
        resultados["errores"].append(msg)
        print(f"[petroleo] {msg}")
        resultados["fuente"] = "sin_api_key"

        # Fallback: traer lo que haya en DB (vía conectar: ruta absoluta + Row)
        from collector.db import conectar
        conn = conectar()
        resultados["datos"] = obtener_petroleo_actual(conn)
        conn.close()
        return resultados

    # Obtener precios de la API (siempre, para tener el precio de hoy)
    precios = fetch_precios_petroleo(CODES, api_key)

    if not precios:
        resultados["fuente"] = "vacio"
        return resultados

    resultados["fuente"] = "oilpriceapi"
    resultados["precios_encontrados"] = len(precios)
    resultados["datos"] = precios

    # Guardar en DB (upsert idempotente)
    inserted = guardar_precios_petroleo(precios, cfg)

    # Autocorrección: la serie diaria del último mes (días de mercado ya
    # cerrados) pisa huecos o valores malos de días anteriores.
    serie = fetch_historial_wti(api_key)
    if serie:
        inserted += guardar_serie_petroleo(serie)
    resultados["historial_dias"] = len(serie)
    resultados["insertados"] = inserted

    return resultados


if __name__ == "__main__":
    print("=" * 60)
    print("Obtenedor de precios petróleo — OilPriceAPI")
    print("=" * 60)

    resultado = ejecutar()

    print(f"\nFuente: {resultado['fuente']}")
    print(f"Precios encontrados: {resultado.get('precios_encontrados', 0)}")
    print(f"Insertados en DB: {resultado.get('insertados', 0)}")
    if resultado.get("errores"):
        for e in resultado["errores"]:
            print(f"  ERROR: {e}")
