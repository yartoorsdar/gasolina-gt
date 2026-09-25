"""Coleccionador de noticias relacionadas con petróleo y energía.

Fuentes RSS configuradas en config.json → noticias.feeds:
  - Google News ES (petróleo, refinería, oleoducto, ataque)
  - Google News EN (oil, refinery, pipeline, OPEC)
  - oilprice.com (principal)
  - EIA todayinenergy.xml

El módulo parsea cada feed RSS y guarda las noticias en la DB.
Opcionalmente usa un LLM local para clasificar y resumir en español
(si config.json → llm.base_url y model están configurados).
"""

import os
import json
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

# Cargar .env del root del proyecto (si existe)
_project_root = Path(__file__).resolve().parent.parent
_dotenv_path = _project_root / ".env"
if _dotenv_path.exists():
    load_dotenv(_dotenv_path)

# Asegurar imports relativos cuando se ejecuta como __main__
if __name__ == "__main__":
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in os.sys.path:
        os.sys.path.insert(0, str(_root))


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
}


# ──────────────────────────────────────────────
# Fetch de feeds RSS
# ──────────────────────────────────────────────

def fetch_feed_rss(url: str, timeout: int = 30) -> dict | None:
    """Descarga y parsea un feed RSS.

    Args:
        url: URL del feed RSS.
        timeout: Timeout en segundos.

    Returns:
        Dict con keys: title, link, published, summary (o None si falla).
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[noticias] Error al fetchear feed '{url}': {exc}")
        return None

    # Intentar parsear con feedparser primero (más robusto para RSS)
    try:
        import feedparser
    except ImportError:
        # Fallback a parsing manual si feedparser no está disponible
        from xml.etree import ElementTree as ET
        return _parse_feed_fallback(resp.text, url)

    try:
        feed = feedparser.parse(resp.text)
        if not feed.get("entries"):
            print(f"[noticias] Feed '{url}' sin entries")
            return None

        resultados = []
        for entry in feed.entries[:20]:  # máximo 20 por feed
            item = {
                "title": entry.get("title", "").strip(),
                "link": entry.get("link", "").strip(),
                "published": entry.get("published", entry.get("updated", "")),
                "summary": entry.get("summary", "").strip()[:500],
                "source_url": url,
            }
            if item["title"] and item["link"]:
                resultados.append(item)

        return {"feed_url": url, "entries": resultados}

    except Exception as exc:
        print(f"[noticias] Error parseando feed '{url}': {exc}")
        return None


def _parse_feed_fallback(xml_text: str, source_url: str) -> dict | None:
    """Parseo manual de RSS como fallback (sin feedparser)."""
    try:
        root = ET.fromstring(xml_text)

        # Buscar items en diferentes formatos de feed
        items = []
        for item_elem in root.iter("item"):
            title = _get_text(item_elem, "title")
            link = _get_text(item_elem, "link")
            published = (
                _get_text(item_elem, "pubDate")
                or _get_text(item_elem, "published", ns="http://purl.org/dc/elements/1.1/")
            )
            summary = _get_text(item_elem, "description")

            if title and link:
                items.append({
                    "title": title.strip(),
                    "link": link.strip(),
                    "published": published or "",
                    "summary": (summary or "")[:500].strip(),
                    "source_url": source_url,
                })

        return {"feed_url": source_url, "entries": items} if items else None

    except Exception:
        return None


def _get_text(elem, tag, ns=""):
    """Extrae texto de un elemento XML."""
    if ns:
        tag = f"{{{ns}}}{tag}"
    child = elem.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


# ──────────────────────────────────────────────
# Clasificación con LLM local (opcional)
# ──────────────────────────────────────────────

def _llm_available(cfg: dict = None) -> bool:
    """Verifica si el LLM está configurado y responde.

    Soporta Gemini nativo (generativelanguage) y endpoints OpenAI-compatibles.
    """
    if cfg is None:
        config_path = _project_root / "config.json"
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return False

    llm_cfg = cfg.get("llm", {})
    base_url = llm_cfg.get("base_url", "").strip().rstrip("/")
    model = llm_cfg.get("model", "").strip()
    api_key = os.environ.get("GEMINI_API_KEY", llm_cfg.get("api_key", "")).strip()

    if not base_url or not model or not api_key:
        return False

    # Verificar que el endpoint responde (ping liviano según proveedor)
    try:
        if "generativelanguage" in base_url:
            resp = requests.get(f"{base_url}/models/{model}?key={api_key}", timeout=10)
        else:
            resp = requests.get(f"{base_url}/v1/models", timeout=10)
        if resp.status_code == 200:
            return True
    except Exception:
        pass

    return False


def clasificar_noticia_llm(titulo: str, resumen: str, cfg: dict = None) -> dict | None:
    """Usa el LLM local para clasificar y resumir una noticia.

    Args:
        titulo: Título de la noticia.
        resumen: Resumen/contenido de la noticia.
        cfg: Configuración del LLM (opcional).

    Returns:
        Dict con categoria, relevancia (1-5), resumen_es, o None si falla.
    """
    if cfg is None:
        config_path = _project_root / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    llm_cfg = cfg.get("llm", {})
    base_url = llm_cfg.get("base_url", "").strip()
    model = llm_cfg.get("model", "").strip()
    api_key = os.environ.get("GEMINI_API_KEY", llm_cfg.get("api_key", "")).strip()

    if not base_url or not model:
        return None

    prompt = (
        "Eres un analista de energía para Guatemala (país que IMPORTA todos sus "
        "combustibles: gasolina superior/regular y diésel). Analiza la noticia y "
        "responde en formato JSON con estas keys:\n"
        "- categoria: 'oferta', 'demanda', 'geopolitica', 'precios', 'infraestructura', 'finanzas', 'diplomacia', 'otro'\n"
        "- relevancia: entero 1-5. 5 = afecta directo el precio o abastecimiento de "
        "combustibles en Guatemala (ataques a refinerías/oleoductos, sanciones, OPEP, "
        "guerras en zonas petroleras, crisis del diésel). 1 = sin relación.\n"
        "- resumen_es: resumen en ESPAÑOL, máximo 2 líneas, enfocado en qué pasó y "
        "por qué importa para el precio del combustible\n"
        "- titulo_es: titular en ESPAÑOL, máximo 90 caracteres, directo y periodístico\n\n"
        f"TITULO: {titulo}\nRESUMEN: {resumen}"
    )

    try:
        content = _llm_post(prompt, base_url, model, api_key)
        if not content:
            return None

        # Buscar JSON dentro del texto de respuesta (del primer { al último })
        import re as _re
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(content[start:end + 1])
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, dict) and "categoria" in parsed:
                titulo_es = (parsed.get("titulo_es") or "").strip()[:120] or None
                return {
                    "categoria": parsed.get("categoria", "otro"),
                    "relevancia": min(5, max(1, int(parsed.get("relevancia", 3)))),
                    "resumen_es": parsed.get("resumen_es", resumen[:200]),
                    "titulo_es": titulo_es,
                }
    except Exception as exc:
        print(f"[noticias] Error LLM: {exc}")

    return None


# ──────────────────────────────────────────────
# LLM en lote (1 request para N noticias) + selección rotativa
# ──────────────────────────────────────────────

def _llm_post(prompt: str, base_url: str, model: str, api_key: str) -> str | None:
    """Un request al LLM (una sola tentativa, modo plano).

    A propósito SIN reintento inmediato ni JSON-mode: los reintentos pegados
    disparan 429 (rate-limit) y dejan todo el lote en cero. El modo plano ya
    funcionó en producción; la limpieza de cercas la hace el parser.
    """
    try:
        if "generativelanguage" in base_url:
            resp = requests.post(
                f"{base_url}/models/{model}:generateContent?key={api_key}",
                json={
                    "contents": [{
                        "parts": [
                            {"text": "Eres un analista de energia. Responde SOLO con JSON.\n\n" + prompt}
                        ]
                    }],
                    "generationConfig": {"temperature": 0.3},
                },
                timeout=60,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["candidates"][0]["content"]["parts"][0]["text"]
        # OpenAI compatible (DeepSeek, etc.)
        resp = requests.post(
            f"{base_url}/v1/chat/completions",
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": "Eres un analista de energia. Responde solo con JSON."},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]
    except Exception as exc:
        print(f"[noticias] Error LLM: {exc}")
        return None


def _seleccion_rotativa(items: list[dict], por_feed: int = 3, max_total: int = 15) -> list[dict]:
    """Round-robin por feed: hasta `por_feed` items de cada fuente.

    Evita que el primer feed (ES) acapare todo el lote LLM y deje fuera
    a OilPrice/EN. Preserva el orden original dentro de cada feed.
    """
    por_fuente: dict[str, list[dict]] = {}
    for it in items:
        por_fuente.setdefault(it.get("source_url", ""), []).append(it)
    salida: list[dict] = []
    for i in range(por_feed):
        for fuente in por_fuente:
            if len(salida) >= max_total:
                return salida
            if i < len(por_fuente[fuente]):
                salida.append(por_fuente[fuente][i])
    return salida[:max_total]


def clasificar_lote_llm(items: list[dict], cfg: dict = None) -> list[dict | None]:
    """Clasifica N noticias en UN request. Retorna lista paralela (dict o None)."""
    if not items:
        return []
    if cfg is None:
        config_path = _project_root / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    llm_cfg = cfg.get("llm", {})
    base_url = llm_cfg.get("base_url", "").strip()
    model = llm_cfg.get("model", "").strip()
    api_key = os.environ.get("GEMINI_API_KEY", llm_cfg.get("api_key", "")).strip()
    if not base_url or not model or not api_key:
        return [None] * len(items)

    lineas = "\n".join(
        f'[{i}] TITULO: {it.get("title", "")[:200]} | RESUMEN: {(it.get("summary", "") or "")[:300]}'
        for i, it in enumerate(items)
    )
    prompt = (
        "Eres un analista de energía para Guatemala (país que IMPORTA todos sus "
        "combustibles). Clasifica CADA noticia y responde SOLO con un array JSON, "
        "sin texto antes ni después, sin markdown. Un objeto por noticia con su índice:\n"
        '[{"i":0,"categoria":"geopolitica","relevancia":5,'
        '"resumen_es":"...","titulo_es":"..."}, ...]\n'
        "Reglas por campo:\n"
        "- categoria: UNA de 'oferta','demanda','geopolitica','precios','infraestructura','finanzas','diplomacia','otro'\n"
        "- relevancia: entero 1-5. 5 = afecta directo precio/abastecimiento en Guatemala "
        "(refinerías, oleoductos, sanciones, OPEP, guerras petroleras, diésel). "
        "1 = sin relación con combustibles.\n"
        "- resumen_es: SIEMPRE en ESPAÑOL aunque la noticia esté en inglés. Máx 2 líneas: "
        "qué pasó + por qué importa para el precio del combustible.\n"
        "- titulo_es: SIEMPRE en ESPAÑOL aunque el original esté en inglés. Titular "
        "periodístico, máx 90 caracteres.\n\n"
        f"NOTICIAS:\n{lineas}"
    )

    content = _llm_post(prompt, base_url, model, api_key)
    if not content:
        return [None] * len(items)

    # Limpiar cercas markdown (```json ... ```) que Gemini suele agregar
    import re as _re
    content = _re.sub(r"^```(?:json)?\s*", "", content.strip())
    content = _re.sub(r"\s*```$", "", content.strip())

    try:
        start = content.find("[")
        end = content.rfind("]")
        arr = json.loads(content[start:end + 1]) if start != -1 and end > start else []
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[noticias] Lote: parse falló ({exc}). Respuesta: {content[:300]!r}")
        return [None] * len(items)

    if not isinstance(arr, list) or not arr:
        print(f"[noticias] Lote: respuesta sin array. Respuesta: {content[:300]!r}")
        return [None] * len(items)

    por_i: dict[int, dict] = {}
    for o in arr:
        if not isinstance(o, dict):
            continue
        try:
            por_i[int(o.get("i"))] = o
        except (TypeError, ValueError):
            continue
    salida: list[dict | None] = []
    for i, it in enumerate(items):
        o = por_i.get(i)
        if not o or "categoria" not in o:
            salida.append(None)
            continue
        salida.append({
            "categoria": o.get("categoria", "otro"),
            "relevancia": min(5, max(1, int(o.get("relevancia", 3)))),
            "resumen_es": o.get("resumen_es") or it.get("summary", "")[:200],
            "titulo_es": ((o.get("titulo_es") or "").strip()[:120]) or None,
        })
    return salida


# ──────────────────────────────────────────────
# Integración con DB y orquestador
# ──────────────────────────────────────────────

def guardar_noticias(items: list[dict], cfg: dict = None) -> int:
    """Guarda noticias en la base de datos.

    Args:
        items: Lista de dicts con keys: title, link, published, summary, source_url.
        cfg: Configuración (opcional).

    Returns:
        Número de registros insertados exitosamente.
    """
    if cfg is None:
        config_path = _project_root / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    from collector.db import conectar, insertar_noticia
    conn = conectar()

    inserted = 0
    for item in items:
        try:
            published_at = _normalizar_fecha_publicacion(item.get("published", ""))

            # Preferir el resumen/categoría del LLM cuando exista
            resumen_es = (item.get("resumen_es") or item.get("summary", ""))[:500]
            row_id = insertar_noticia(
                conn=conn,
                url=item["link"],
                titulo=item["title"],
                titulo_es=item.get("titulo_es"),
                medio=_extraer_medio(item.get("source_url", "")),
                publicado_at=published_at,
                categoria=item.get("categoria"),
                relevancia=item.get("relevancia"),
                resumen_es=resumen_es,
            )
            if row_id is not None:
                inserted += 1
        except Exception as exc:
            print(f"[noticias] Error guardando '{item.get('title', '?')}': {exc}")

    conn.close()
    return inserted


def _normalizar_fecha_publicacion(pub_str: str) -> str | None:
    """Normaliza una fecha de feed RSS a ISO 8601."""
    if not pub_str:
        return None

    # feedparser ya normaliza a algo como 'Mon, 22 Sep 2026 14:30:00 GMT'
    try:
        from datetime import timezone, timedelta
        from email.utils import parsedate_to_datetime
        _GT = timezone(timedelta(hours=-6))
        dt = parsedate_to_datetime(pub_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(_GT).strftime("%Y-%m-%dT%H:%M:%S-06:00")
    except (ValueError, TypeError):
        pass

    # Intentar formatos comunes (naive se asume UTC → convertir a GT)
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %Z",
        "%a, %d %b %Y %H:%M:%S %z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]:
        try:
            from datetime import timezone, timedelta
            _GT = timezone(timedelta(hours=-6))
            dt = datetime.strptime(pub_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(_GT).strftime("%Y-%m-%dT%H:%M:%S-06:00")
        except ValueError:
            continue

    return None


def _extraer_medio(url: str) -> str:
    """Extrae el nombre del medio desde la URL del feed."""
    if "google.com" in url:
        if "hl=es" in url or "hl=es-419" in url:
            return "Google News ES"
        return "Google News EN"
    elif "oilprice.com" in url:
        return "OilPrice.com"
    elif "eia.gov" in url:
        return "EIA Today in Energy"
    else:
        # Intentar extraer dominio
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            return domain.replace("www.", "")
        except Exception:
            return url


def ejecutar(cfg: dict = None) -> dict:
    """Orquesta el flujo completo de obtención de noticias.

    Args:
        cfg: Configuración cargada desde config.json (opcional).

    Returns:
        Dict con resumen de la ejecución.
    """
    if cfg is None:
        import json as _json
        config_path = _project_root / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = _json.load(f)

    feeds = cfg.get("noticias", {}).get("feeds", [])

    resultados = {
        "fuente": "",
        "feeds_procesados": 0,
        "total_encontrados": 0,
        "insertados": 0,
        "errores": [],
    }

    all_items = []

    for feed_url in feeds:
        print(f"[noticias] Fetching feed: {feed_url[:60]}...")
        data = fetch_feed_rss(feed_url)
        if data and data.get("entries"):
            resultados["feeds_procesados"] += 1
            all_items.extend(data["entries"])
            print(f"  -> {len(data['entries'])} noticias encontradas")

    if not all_items:
        resultados["fuente"] = "vacio"
        return resultados

    resultados["total_encontrados"] = len(all_items)

    # Clasificación con LLM si está disponible. Selección rotativa entre feeds
    # (3 por feed) para que todos los idiomas/fuentes entren al lote; el resto
    # se guarda sin clasificar. Un solo request en lote (no 15 sueltos).
    if _llm_available(cfg):
        candidatos = _seleccion_rotativa(all_items, por_feed=3, max_total=15)
        print(f"[noticias] Clasificando lote de {len(candidatos)} con LLM...")
        import time as _time
        _time.sleep(2)  # respirar antes del lote (anti rate-limit)
        lote = clasificar_lote_llm(candidatos, cfg)
        n_ok = sum(1 for c in (lote or []) if c)
        if lote and n_ok:
            print(f"[noticias] Lote OK: {n_ok}/{len(candidatos)} clasificadas")
            for item, cls in zip(candidatos, lote):
                if cls:
                    item.update(cls)
        else:
            # Fallback: uno por uno (máximo 5, con pausa anti rate-limit).
            # Sin reintentos pegados: a 5s por llamada quedamos en ~12 RPM.
            import time as _time
            print("[noticias] Lote falló, reintentando uno por uno...")
            ok = 0
            for item in candidatos[:5]:
                _time.sleep(5)
                classification = clasificar_noticia_llm(
                    item["title"], item.get("summary", ""), cfg
                )
                if classification:
                    item.update(classification)
                    ok += 1
            print(f"[noticias] Fallback OK: {ok} clasificadas")

    # Guardar en DB
    inserted = guardar_noticias(all_items, cfg)
    resultados["insertados"] = inserted
    resultados["fuente"] = "rss_feeds"

    return resultados


if __name__ == "__main__":
    print("=" * 60)
    print("Coleccionador de noticias — gasolina-gt")
    print("=" * 60)

    resultado = ejecutar()

    print(f"\nFuente: {resultado['fuente']}")
    print(f"Feeds procesados: {resultado.get('feeds_procesados', 0)}")
    print(f"Noticias encontradas: {resultado.get('total_encontrados', 0)}")
    print(f"Insertadas en DB: {resultado.get('insertados', 0)}")
    if resultado.get("errores"):
        for e in resultado["errores"]:
            print(f"  ERROR: {e}")
