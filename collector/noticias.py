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
import re
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


# Proveedores LLM soportados (auto-detectado por base_url):
# - Gemini nativo (generativelanguage) con GEMINI_API_KEY ← proveedor activo
# - OpenAI-compatible (Groq, DeepSeek...) con GROK_API_KEY (respaldo)
def _obtener_api_key(llm_cfg: dict, base_url: str = "") -> str:
    """API key según proveedor (auto-detectado por base_url), con fallback a config.

    - Gemini nativo (generativelanguage) → GEMINI_API_KEY primero.
    - OpenAI-compatible (Groq/DeepSeek…) → GROK_API_KEY: las keys de Gemini
      NO funcionan en ese esquema (auth Bearer distinta); si ambas secrets
      existen, usar la equivocada da 401 solo en el POST.
    """
    if "generativelanguage" in (base_url or ""):
        candidates = ("GEMINI_API_KEY", "GROK_API_KEY")
    elif (base_url or "").strip():
        candidates = ("GROK_API_KEY",)
    else:
        candidates = ("GEMINI_API_KEY", "GROK_API_KEY")
    for var in candidates:
        val = os.environ.get(var, "").strip()
        if val:
            return val
    return (llm_cfg.get("api_key", "") or "").strip()


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
    global _last_llm_error
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
    api_key = _obtener_api_key(llm_cfg, base_url)

    if not base_url or not model:
        return False
    if not api_key:
        _last_llm_error = "sin API key (revisar secreto GROK_API_KEY/GEMINI_API_KEY)"
        print(f"[noticias] {_last_llm_error}")
        return False

    # Huella no sensible de TODAS las keys presentes (prefijo + longitud + sha).
    # Permite comparar contra el hash local y saber exactamente cuál secreto
    # difiere, sin exponer ningún valor.
    import hashlib as _hl
    global _llm_key_fp
    _llm_key_fp = {}
    for _var in ("GEMINI_API_KEY", "GROK_API_KEY"):
        _v = (os.environ.get(_var, "") or "").strip()
        if _v:
            _llm_key_fp[_var] = (f"{_v[:4]}*** len={len(_v)} "
                                 f"sha={_hl.sha256(_v.encode()).hexdigest()[:8]}")
    print(f"[noticias] LLM keys: {_llm_key_fp}")

    # Verificar que el endpoint responde (ping liviano según proveedor).
    # OJO: Groq/OpenAI exigen auth incluso en /v1/models → mandar Bearer.
    try:
        if "generativelanguage" in base_url:
            resp = requests.get(f"{base_url}/models/{model}?key={api_key}", timeout=10)
        else:
            resp = requests.get(
                f"{base_url}/v1/models", timeout=10,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if resp.status_code == 200:
            return True
        _last_llm_error = f"LLM ping={resp.status_code} (revisar key/modelo)"
        print(f"[noticias] {_last_llm_error}")
    except Exception as exc:
        _last_llm_error = _sanear_error(exc)
        print(f"[noticias] ping LLM falló: {exc}")

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
    api_key = _obtener_api_key(llm_cfg, base_url)

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
                    # Igual que el lote: sin fallback crudo; _validar_cls descarta si falta.
                    "resumen_es": (parsed.get("resumen_es") or "").strip(),
                    "titulo_es": titulo_es,
                }
    except Exception as exc:
        print(f"[noticias] Error LLM: {exc}")

    return None


# ──────────────────────────────────────────────
# LLM en lote (1 request para N noticias) + selección rotativa
# ──────────────────────────────────────────────

# Último error del LLM (diagnóstico exportado a resumen.json).
# NUNCA incluir la API key: se sanea antes de guardar.
_last_llm_error: str | None = None
# Huella no sensible de la key usada (prefijo + longitud)
_llm_key_fp: str | None = None


def _sanear_error(exc: Exception) -> str:
    """Recorta el error y elimina secretos (key=...) antes de exponerlo."""
    import re as _re
    txt = str(exc)
    txt = _re.sub(r"key=[^&\s'\"]+", "key=***", txt)
    return txt[:160]


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
        # OpenAI compatible (Groq, DeepSeek, etc.). Con response_format
        # json_object en el primer intento (gpt-oss lo soporta); si falla,
        # reintento plano. Sin reintentos pegados extra (ver llamador).
        # OJO: Groq exige Authorization Bearer incluso para POST (sin header
        # da 401 aunque el ping GET /v1/models haya pasado con la misma key).
        for _json_mode in (True, False):
            try:
                body: dict = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": "Eres un analista de energia. Responde solo con JSON."},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                }
                if _json_mode:
                    body["response_format"] = {"type": "json_object"}
                resp = requests.post(
                    f"{base_url}/v1/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {api_key}"},
                    timeout=60,
                )
                resp.raise_for_status()
                data = resp.json()
                return data["choices"][0]["message"]["content"]
            except Exception as exc:
                if _json_mode:
                    print(f"[noticias] json_mode no soportado, reintentando plano: {exc}")
                    continue
                raise
    except Exception as exc:
        global _last_llm_error
        _last_llm_error = _sanear_error(exc)
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
    api_key = _obtener_api_key(llm_cfg, base_url)
    if not base_url or not model or not api_key:
        return [None] * len(items)

    lineas = "\n".join(
        f'[{i}] TITULO: {it.get("title", "")[:200]} | RESUMEN: {(it.get("summary") or "").strip()[:300] or "(sin cuerpo)"}'
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
        "qué pasó + por qué importa para el precio del combustible. Si el RESUMEN está vacío "
        "o dice '(sin cuerpo)', escríbelo basándote SOLO en el título. Nunca copies URLs, HTML ni marcas de código.\n"
        "- titulo_es: SIEMPRE en ESPAÑOL aunque el original esté en inglés. Titular "
        "periodístico, máx 90 caracteres, sin el nombre del medio ni sufijos tipo ' - ABC News'.\n\n"
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
            # SIN fallback al summary crudo: si el LLM no devolvió resumen_es,
            # la validación (_validar_cls) descarta el item y se pasa al siguiente.
            "resumen_es": (o.get("resumen_es") or "").strip(),
            "titulo_es": ((o.get("titulo_es") or "").strip()[:120]) or None,
        })
    return salida


# ──────────────────────────────────────────────
# Fallback determinístico sin LLM (traductor libre + keywords)
# Garantiza títulos/resúmenes en español aunque el LLM falle (401/429).
# ──────────────────────────────────────────────

_FB_SUBIDA = [
    'ataque', 'attack', 'drone', 'missile', 'misil', 'pipeline', 'oleoducto',
    'refinería', 'refinery', 'explos', 'incendio', 'fire', 'destroyed',
    'destruido', 'escasez', 'shortage', 'sanción', 'sanciones', 'sanction',
    'embargo', 'guerra', 'war', 'opec', 'opep', 'recorte', 'bloqueo',
    'alza', 'récord', 'record', 'arancel', 'huelga', 'strike', 'export',
    'sube', 'suben', 'rise', 'increase', 'jump', 'soar',
]
_FB_BAJADA = [
    'restart', 'reativa', 'restaura', 'exceso', 'surplus', 'decline',
    'caída', 'caen', 'bajan', 'drop', 'crash', 'reserva', 'acuerdo',
    'deal', 'tregua', 'ceasefire', 'reapertura', 'excedente',
]


# ──────────────────────────────────────────────
# Filtro temático (compuerta): solo entra lo que puede mover el precio del
# combustible en Guatemala. Lo demás se DESCARTA (antes solo se ordenaba y
# colaban columnas de opinión sin relación). Español + inglés; el título se
# traduce después, así que el idioma de origen no importa.
# ──────────────────────────────────────────────

DIAS_MAX_NOTICIA = 15   # antigüedad máxima (días) de una noticia publicable
NOTICIAS_MINIMAS = 5    # el dashboard debe tener al menos estas en español

_RE_PETROLEO = re.compile(
    r"\b(petr[oó]le\w*|crudo|barril\w*|wti|brent|opep|opec|refiner\w*|refino|refinaci\w*|"
    r"refinari\w*|oleoducto\w*|"
    r"gasoducto\w*|pipeline\w*|gasolin\w*|gasoline|di[eé]sel|combustible\w*|fuel\w*|"
    r"carburante\w*|combust[ií]ve\w*|glp|lpg|gas natural|natural gas|lng|gnl|b[uú]nker|jet fuel|"
    r"queroseno|kerosene|petrolero\w*|tanker\w*|aramco|pemex|pdvsa|rosneft|lukoil|"
    r"gazprom|oil|crude|refinery|refineries|barrel\w*)\b",
    re.IGNORECASE,
)
_RE_GUATEMALA = re.compile(r"\bguatemal\w*", re.IGNORECASE)
_RE_ENERGIA_GT = re.compile(
    r"\b(energ\w*|electricidad|el[eé]ctric\w*|subsidi\w*|idp|importaci\w*|"
    r"hidrocarbur\w*|decreto 22|precio\w* de (?:la |los )?combustible\w*)\b",
    re.IGNORECASE,
)
_RE_CONFLICTO = re.compile(
    r"\b(guerra\w*|war|wars|ataque\w*|attack\w*|atentado\w*|misil\w*|missile\w*|"
    r"dron\w*|drone\w*|bombarde\w*|airstrike\w*|sanci[oó]n\w*|sanction\w*|embargo\w*|"
    r"bloqueo\w*|blockade\w*|conflict\w*|invasi\w*|invasion|escalad\w*|escalat\w*)\b",
    re.IGNORECASE,
)
_RE_REGION = re.compile(
    r"\b(ormuz|hormuz|mar rojo|red sea|golfo p[eé]rsico|persian gulf|ir[aá]n\w*|"
    r"iraq\w*|irak\w*|arabia saud\w*|saudi\w*|rusia|russia\w*|ucrania|ukrain\w*|"
    r"venezuel\w*|libia|libya|nigeria|yemen|hut[ií]es|houthi\w*|israel\w*|kuwait|"
    r"qatar|emiratos|golfo de m[eé]xico|gulf of mexico|suez)\b",
    re.IGNORECASE,
)


def es_relevante(titulo: str, resumen: str = "") -> bool:
    """¿La noticia puede afectar el precio del combustible en Guatemala?

    Entra si trata de (a) petróleo o derivados; (b) Guatemala + energía /
    combustibles / subsidios / importación; o (c) guerra, ataque o sanción en
    una región productora o ruta clave del crudo.
    """
    texto = f"{titulo or ''} {resumen or ''}"
    if _RE_PETROLEO.search(texto):
        return True
    if _RE_GUATEMALA.search(texto) and _RE_ENERGIA_GT.search(texto):
        return True
    return bool(_RE_CONFLICTO.search(texto) and _RE_REGION.search(texto))


def _dentro_de_ventana(it: dict, dias: int = DIAS_MAX_NOTICIA) -> bool:
    """Publicada en los últimos `dias` (sin fecha verificable = fuera)."""
    from collector.db import hace_dias_gt
    publicado = _normalizar_fecha_publicacion(it.get("published", ""))
    return bool(publicado) and publicado[:10] >= hace_dias_gt(dias)


def _relevancia_keywords(titulo: str, resumen: str) -> int:
    """Relevancia 1-5 determinística (réplica del semáforo del dashboard)."""
    text = f"{titulo or ''} {resumen or ''}".lower()
    score = sum(1 for kw in _FB_SUBIDA if kw in text)
    score -= 0.5 * sum(1 for kw in _FB_BAJADA if kw in text)
    # Guatemala + combustible/energía = impacto directo en el dashboard: sin
    # este empuje quedaban detrás de cualquier nota de ataques (más keywords).
    if _RE_GUATEMALA.search(text) and (_RE_PETROLEO.search(text) or _RE_ENERGIA_GT.search(text)):
        score += 4
    import re as _re
    if _re.search(r"pipeline.*(attack|damage|shut)", text):
        score += 2
    if _re.search(r"refinería.*(attack|strike|drone)", text):
        score += 1.5
    if score >= 4:
        return 5
    if score >= 2:
        return 4
    if score >= 1:
        return 3
    if score > -1:
        return 2
    return 1


# ──────────────────────────────────────────────
# Validación de estructura y calidad (pipeline: verificar → ordenar → traducir)
# El objetivo es terminar con NOTICIAS_EXCELENTES fichas limpias: título ES +
# descripción sin defectos. Lo que falla se descarta y se pasa a la siguiente.
# ──────────────────────────────────────────────

NOTICIAS_EXCELENTES = 5


def _limpiar_texto(txt) -> str:
    """HTML/entidades → texto plano (tags fuera, &nbsp;→espacio, espacios colapsados).

    Los feeds de Google News traen summary como `<a href="URL">título</a>
    <font>Prensa Libre</font>`; sin limpiar, ese HTML se filtraba a resumen_es.
    """
    if not txt:
        return ""
    import html as _html
    import re as _re
    t = _re.sub(r"<[^>]+>", " ", str(txt))
    t = _html.unescape(t)
    return _re.sub(r"\s+", " ", t).strip()


def _inteligible(txt: str) -> bool:
    """Heurística de caracteres inentendibles: relleno U+FFFD, control chars o
    mayoría de letras fuera del alfabeto latino (noticia 'mojada' en la codificación)."""
    if not txt:
        return False
    import re as _re
    if "\ufffd" in txt or _re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", txt):
        return True
    letras = [c for c in txt if c.isalpha()]
    if not letras:
        return False
    latin = sum(1 for c in letras if "a" <= c.lower() <= "z")
    return (len(letras) - latin) / len(letras) > 0.35


def _validar_cls(cls: dict | None, it: dict | None = None) -> bool:
    """Valida una clasificación/salida traducida IN PLACE y la devuelve lista.

    True solo si titulo_es Y resumen_es están presentes, legibles y sin URLs/HTML/
    caracteres raros. Si falla → el item NO es 'excelente': se pasa al siguiente.
    (resumen_es opcional SOLO si la noticia no trae cuerpo ni el LLM pudo escribirlo.)
    """
    if not cls:
        return False
    t = (cls.get("titulo_es") or "").strip()
    r = (cls.get("resumen_es") or "").strip()
    sin_cuerpo = it is None or not (it.get("summary") or "").strip()

    ok_titulo = bool(t) and len(t) >= 10 \
        and "http" not in t.lower() and "<a " not in t.lower() and not _inteligible(t)
    # Descripción: exigida salvo noticia sin cuerpo (ahí el título ES ya la cubre).
    ok_resumen = bool(r) or (sin_cuerpo and ok_titulo)
    if r:
        ok_resumen = ok_resumen and len(r) >= 25 \
            and "http" not in r.lower() and "&#" not in r and not _inteligible(r)

    if not (ok_titulo and ok_resumen):
        return False

    # Normalizar antes de persistir: nada de HTML residual ni sufijos de medio.
    # Sin cuerpo y sin resumen del LLM → "" a propósito: el dashboard oculta la
    # descripción vacía (mejor que fabricar una repitiendo el título).
    cls["titulo_es"] = t[:120]
    cls["resumen_es"] = r
    return True


def _item_valido(it: dict) -> bool:
    """Estructura mínima para competir por ficha 'excelente' (paso 1 del pipeline).

    Descarta: sin link, título corto/vacío, título con URL en vez de texto, o
    caracteres inentendibles. El summary ya llega limpio (_limpiar_texto al recibir).
    """
    titulo = it.get("title") or ""
    if not it.get("link") or len(titulo) < 15:
        return False
    tl = titulo.lower()
    if "http" in tl or "<a " in tl or _inteligible(titulo):
        return False
    s = (it.get("summary") or "").lower()
    if "http" in s and "google.com/rss/articles" in s:
        return False  # resumen que sigue siendo URL → descripción en blanco real
    return True


def _ordenar_candidatos(items: list[dict]) -> list[dict]:
    """Paso 2 del pipeline: ordena por relevancia estimada (determinística, sin LLM).

    Las NOTICIAS_EXCELENTES mejores reciben la traducción; el resto queda de
    respaldo si alguna falla en validación. Empates: primero Guatemala (el
    puntaje satura en 5 y muchas notas de ataques empatan), luego lo más nuevo.
    """
    def clave(x):
        texto = f"{x.get('title', '')} {x.get('summary', '')}"
        return (
            _relevancia_keywords(x.get("title", ""), x.get("summary", "")),
            bool(_RE_GUATEMALA.search(texto)),
            _normalizar_fecha_publicacion(x.get("published", "")) or "",
        )
    return sorted(items, key=clave, reverse=True)


def _traducir_fallback(items: list[dict], objetivo: int = 0) -> int:
    """Traduce al español lo que el LLM no alcanzó (MyMemory, sin API key).

    Dos pasadas sobre el mismo lote:
      1. Titulares YA en español (feeds ES): etiquetado GRATIS (titulo_es +
         categoria + relevancia por keywords), sin gastar cuota de traducción.
         Antes esta rama competía con las EN y, al agotarse la cuota, un
         `break` dejaba a las restantes SIN categoría ni relevancia.
      2. Titulares EN vía MyMemory: presupuesto anónimo ~5000 caracteres/día —
         primero títulos (baratos), luego resúmenes solo mientras quede cuota.
    """
    import time as _time

    def aceptado(it) -> bool:
        """El item solo cuenta hacia objetivo si su traducción pasa calidad."""
        probe = {"titulo_es": it.get("titulo_es", ""), "resumen_es": it.get("resumen_es", "")}
        if not _validar_cls(probe, it):
            return False
        # Escribir los valores normalizados (sin HTML ni sufijos de medio).
        it["titulo_es"] = probe["titulo_es"]
        it["resumen_es"] = probe["resumen_es"]
        return True

    ok = 0

    # Pasada 1: ya-español → etiquetado gratuito (nunca consume cuota).
    for it in items:
        if objetivo and ok >= objetivo:
            break
        if it.get("titulo_es"):
            continue
        titulo = (it.get("title") or "").strip()
        if not titulo:
            continue
        if _ya_es(titulo, it.get("source_url", "")):
            it["titulo_es"] = titulo[:120]
            # Categoria + relevancia SOLO si pasa calidad: un item defectuoso no
            # debe aparecer en el top-10 (con nulls) ni faltarle el semáforo.
            if aceptado(it):
                it.setdefault("categoria", "otro")
                it["relevancia"] = _relevancia_keywords(titulo, it.get("summary", ""))
                ok += 1
            else:
                it["titulo_es"] = None

    # Pasada 2: EN vía MyMemory; cuota agotada o red caída → detenerse sin
    # afectar lo ya etiquetado en la pasada 1. Con objetivo, parar al lograrlo.
    gasto = 0
    for it in items:
        if objetivo and ok >= objetivo:
            break
        if it.get("titulo_es"):
            continue
        titulo = (it.get("title") or "").strip()
        if not titulo:
            continue
        try:
            es = _mymemory(titulo[:450])
            if not es:
                break  # cuota agotada o red caída: no insistir
            gasto += len(titulo)
            it["titulo_es"] = es[:120]
            summ = (it.get("summary") or "")[:400].strip()
            if summ and not it.get("resumen_es") and gasto < 3000:
                _time.sleep(1)
                es_s = _mymemory(summ)
                if es_s:
                    gasto += len(summ)
                    it["resumen_es"] = es_s[:500]
            # Traducción con defectos (mojada, URL, vacía) → no contar y seguir.
            # Categoria + relevancia SOLO si pasa calidad.
            if aceptado(it):
                it.setdefault("categoria", "otro")
                it["relevancia"] = _relevancia_keywords(it.get("title", ""), it.get("summary", ""))
                ok += 1
            else:
                it["titulo_es"] = None
            _time.sleep(1)
        except Exception as exc:
            print(f"[noticias] Trad fallback falló, continúo sin él: {exc}")
            break
    return ok


def _ya_es(titulo: str, source_url: str) -> bool:
    """Heurística: titular ya en español (feed ES)."""
    if "hl=es" in (source_url or ""):
        return True
    import re as _re
    return bool(_re.search(r"[áéíóúñ¿¡]", titulo))


def _mymemory(texto: str) -> str:
    """Una traducción EN→ES vía MyMemory (sin key). Vacío si falla/cuota."""
    try:
        resp = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": texto, "langpair": "en|es"},
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("quotaFinished"):
            print("[noticias] MyMemory: cuota diaria agotada")
            return ""
        return ((data.get("responseData") or {}).get("translatedText") or "").strip()
    except Exception as exc:
        print(f"[noticias] MyMemory falló: {exc}")
        return ""


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

    global _last_llm_error
    _last_llm_error = None

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

    # ── Pipeline IA (orden fijo): 1 verificar → 2 ordenar → 3 traducir al final ──
    # Meta: terminar con NOTICIAS_EXCELENTES fichas SIN FALLAS en título y
    # descripción. Lo defectuoso se descarta y se pasa a la siguiente noticia.

    # 1. VERIFICAR estructura: limpia HTML/entidades del summary (raíz del bug de
    # Google News que filtraba <a href="..."> hasta resumen_es) y detecta títulos
    # rotos. Los items inválidos siguen guardándose a la DB, pero no compiten por
    # ficha excelente.
    for it in all_items:
        it["summary"] = _limpiar_texto(it.get("summary", ""))

    # 2. ORDENAR por relevancia estimada (determinística, sin costo de LLM). Las
    # mejores reciben la traducción; el resto queda de respaldo si alguna falla
    # la validación final.
    validos = [it for it in all_items if _item_valido(it)]
    recientes = [it for it in validos if _dentro_de_ventana(it)]
    candidatos = _ordenar_candidatos(
        [it for it in recientes if es_relevante(it.get("title", ""), it.get("summary", ""))]
    )
    print(f"[noticias] {len(all_items)} recibidas -> {len(validos)} válidas -> "
          f"{len(recientes)} de los últimos {DIAS_MAX_NOTICIA} días -> "
          f"{len(candidatos)} relevantes (petróleo / Guatemala-energía / conflicto en zona petrolera)")
    if len(candidatos) < NOTICIAS_MINIMAS:
        print(f"[noticias] AVISO: solo {len(candidatos)} relevantes (< {NOTICIAS_MINIMAS})")

    excelentes: list[dict] = []  # items que superan TODA la validación de calidad

    LOTE_MAX = 15
    if _llm_available(cfg):
        # 3. TRADUCIR (último paso): lote único del top rankeado, un solo request.
        candidatos_llm = candidatos[:LOTE_MAX]
        print(f"[noticias] Clasificando lote de {len(candidatos_llm)} con LLM...")
        import time as _time
        _time.sleep(2)  # respirar antes del lote (anti rate-limit)

        def fusionar_lote(lote):
            """Aplica solo lo que pasa calidad; devuelve cuántas fichas quedaron OK."""
            ok = 0
            for item, cls in zip(candidatos_llm, lote or []):
                if cls and _validar_cls(cls, item):
                    item.update(cls)
                    excelentes.append(item)
                    ok += 1
            return ok

        n_ok = fusionar_lote(clasificar_lote_llm(candidatos_llm, cfg))
        if not n_ok and "429" in (_last_llm_error or ""):
            # Rate-limit: esperar 65s y reintentar el lote UNA vez
            print("[noticias] 429: esperando 65s y reintentando lote...")
            _time.sleep(65)
            n_ok = fusionar_lote(clasificar_lote_llm(candidatos_llm, cfg))
        if n_ok:
            fallados = len(candidatos_llm) - n_ok
            print(f"[noticias] Lote OK: {n_ok}/{len(candidatos_llm)} válidas"
                  + (f", {fallados} descartadas (se pasa a la siguiente)" if fallados else ""))
        else:
            # Fallback LLM uno por uno (máximo 3, con pausa anti rate-limit).
            print("[noticias] Lote falló, reintentando uno por uno...")
            for item in candidatos_llm[:3]:
                _time.sleep(10)
                classification = clasificar_noticia_llm(
                    item["title"], item.get("summary", ""), cfg
                )
                if classification and _validar_cls(classification, item):
                    item.update(classification)
                    excelentes.append(item)
            print(f"[noticias] Fallback LLM OK: {len(excelentes)} clasificadas")

    # 3b. Si el LLM no cubrió las fichas exigidas: fallback determinístico (sin key)
    # SOLO sobre lo necesario — y de nuevo, lo defectuoso se descarta y sigue.
    faltantes = max(0, NOTICIAS_EXCELENTES - len(excelentes))
    if faltantes:
        hechos = {id(e) for e in excelentes}
        bloque = [it for it in candidatos if id(it) not in hechos][:faltantes * 3]
        n_tr = _traducir_fallback(bloque, objetivo=faltantes)
        nuevas = [it for it in bloque if it.get("relevancia") is not None and it.get("titulo_es")]
        excelentes.extend(nuevas)
        print(f"[noticias] Traductor fallback: {n_tr} completadas "
              f"(fichas totales: {len(excelentes)}/{NOTICIAS_EXCELENTES})")

    resultados["excelentes"] = len(excelentes)

    # Guardar en DB SOLO lo relevante y reciente: lo descartado no debe poder
    # llegar al dashboard por ningún camino.
    inserted = guardar_noticias(candidatos, cfg)
    resultados["relevantes"] = len(candidatos)
    resultados["insertados"] = inserted
    resultados["fuente"] = "rss_feeds"
    resultados["llm_error"] = _last_llm_error

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
    if "excelentes" in resultado:
        print(f"Fichas excelentes (título+descripción sin fallas): {resultado['excelentes']}/{NOTICIAS_EXCELENTES}")
    if resultado.get("errores"):
        for e in resultado["errores"]:
            print(f"  ERROR: {e}")
