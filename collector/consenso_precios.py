"""Consejo de precios de combustible: varias fuentes → un precio con confianza.

Flujo (`ejecutar`):
  1. DESCUBRIR notas recientes sobre precios de combustible en Guatemala:
     feeds RSS/Atom y sitemaps de noticias DECLARADOS por cada medio (robots.txt
     o <link rel="alternate"> de su portada; `config.json → consejo.feeds_directos`)
     + Bing News RSS + GlobalPetrolPrices. Google News solo como diagnóstico de
     cobertura (sus enlaces no llevan al medio).
  2. DECIDIR qué notas leer: nuevas, o ya leídas que el medio ACTUALIZÓ (Publinews
     y Prensa Libre reescriben la misma URL cada día: "Así amanecieron los precios…"),
     o leídas sin precios con un extractor anterior. Sin cifra "Q00.00" en el texto
     no se gasta LLM.
  3. EXTRAER de cada nota los precios con su contexto: producto, modalidad
     (autoservicio / servicio completo / desconocida), tipo, fecha y cita textual.
     LLM (Groq) primero — distingue estimados, precios de otros países,
     comparaciones históricas —; si no hay LLM, regex conservador. Todo precio
     del LLM debe aparecer textual en la nota (si no, se descarta).
  4. GUARDAR cada precio como `observacion` (tabla propia; nunca pisa precios).
  5. DECIDIR por producto y modalidad (`consejo`): agrupa las fuentes que
     coinciden dentro de ±TOLERANCIA, pondera cada fuente por su precisión
     histórica contra el dato oficial del MEM y emite precio + confianza. Nunca
     mezcla precios con y sin IVA+IDP (Decreto 22-2026).

Solo combustibles (superior, regular, diésel). El WTI ya viene de una API.
"""

import json
import re
import time
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, quote_plus, urlparse

if __name__ == "__main__":
    import sys
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

import requests

_project_root = Path(__file__).resolve().parent.parent

# ──────────────────────────────────────────────
# Parámetros (config.json → "consejo" puede sobrescribirlos)
# ──────────────────────────────────────────────

CONSULTAS = [
    "precio gasolina superior regular diésel Guatemala",
    "precios combustibles Guatemala galón autoservicio",
    "precio combustibles Guatemala hoy MEM",
]
DIAS_ARTICULO = 10        # antigüedad máxima de una nota para leerla
MAX_ARTICULOS_LLM = 15    # llamadas al LLM por run (Groq: 8K tokens/min → pausa entre llamadas)
MAX_NOTAS_LECTURA = 60    # notas descargadas por run (las que no traen precios no gastan LLM)
MAX_CHARS_TEXTO = 4000    # texto de la nota que se envía al LLM (recortado a lo relevante)
PAUSA_LLM_SEG = 20        # entre requests (cuida el límite de tokens/minuto)
DIAS_RELECTURA = 1        # notas sin precios de hoy/ayer se vuelven a descargar (los medios las actualizan)
# Sube al cambiar el prompt o la extracción de texto: las notas que dieron 0 precios
# con una versión anterior se releen una vez (así se recuperan las del 30-sep/1-oct).
EXTRACTOR_LLM = "llm-v2"
SIN_PRECIOS = "sin_precios"  # extractor de una nota cuyo texto no trae ninguna cifra en quetzales
TOLERANCIA = 0.20         # Q/galón: dos fuentes "coinciden" si difieren menos
DIAS_VENTANA = 7          # observaciones consideradas para el veredicto
DIAS_BLOQUE = 3           # dentro de la ventana, solo lo más reciente (±3 días)
DECAIMIENTO_DIA = 0.85    # peso × 0.85 por cada día de antigüedad dentro del bloque
# Lo observado en gasolineras manda sobre lo anunciado: el 1-oct-2026 el MEM anunció
# diésel Q42.94 (cálculo con el precio del 28-sep) y en bomba amaneció en Q41.69.
PESO_TIPO = {"monitoreado": 1.0, "oficial": 1.0, "referencia": 0.7}
MEDIO_OFICIAL = "MEM (oficial)"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

_last_llm_error: str | None = None


def _cfg_consejo(cfg: dict | None) -> dict:
    base = {
        "consultas": CONSULTAS, "dias_articulo": DIAS_ARTICULO,
        "max_articulos_llm": MAX_ARTICULOS_LLM, "max_notas_lectura": MAX_NOTAS_LECTURA,
        "pausa_llm_seg": PAUSA_LLM_SEG, "feeds_directos": [], "consultas_google_news": [],
    }
    base.update((cfg or {}).get("consejo", {}))
    return base


def _medio(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


# ──────────────────────────────────────────────
# 1. Descubrir notas
# ──────────────────────────────────────────────

def descubrir_articulos(consultas: list[str], dias: int = DIAS_ARTICULO) -> list[dict]:
    """Notas recientes de Bing News RSS (enlace directo al medio).

    Returns:
        [{url, medio, titulo, publicado (YYYY-MM-DD)}] sin duplicados.
    """
    import xml.etree.ElementTree as ET
    from collector.db import hace_dias_gt

    limite = hace_dias_gt(dias)
    vistos, notas = set(), []
    for q in consultas:
        # cc=GT: sin el país, Bing devuelve casi solo Prensa Libre/Publinews y omite
        # Soy502, AGN, Emisoras Unidas, DCA (medido el 1-oct-2026).
        rss = f"https://www.bing.com/news/search?q={quote_plus(q)}&format=rss&setlang=es&cc=GT"
        try:
            resp = requests.get(rss, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            items = ET.fromstring(resp.content).findall(".//item")
        except Exception as exc:
            print(f"[consejo] Bing '{q}': {exc}")
            continue
        for it in items:
            link = it.findtext("link") or ""
            url = parse_qs(urlparse(link).query).get("url", [link])[0]
            try:
                publicado = parsedate_to_datetime(it.findtext("pubDate") or "").strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                continue
            if not url.startswith("http") or url in vistos or publicado < limite:
                continue
            vistos.add(url)
            notas.append({"url": url, "medio": _medio(url),
                          "titulo": (it.findtext("title") or "").strip(), "publicado": publicado})
    notas.sort(key=lambda n: n["publicado"], reverse=True)
    return notas


# Tema de la nota, en el título o en la URL (los sitemaps de noticias a veces no traen título).
_RE_TITULO_COMBUSTIBLE = re.compile(r"combustib|gasolin|di[eé]sel|gal[oó]n|\bidp\b", re.I)


def _fecha_gt(texto: str | None) -> str | None:
    """'YYYY-MM-DD' en hora de Guatemala desde RFC 822 (RSS) o ISO 8601 (Atom/sitemaps)."""
    from collector.db import _GT

    texto = (texto or "").strip()
    if not texto:
        return None
    try:
        return parsedate_to_datetime(texto).astimezone(_GT).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        pass
    try:
        dt = datetime.fromisoformat(texto.replace("Z", "+00:00"))
    except ValueError:
        return None
    # Sin zona (ej. "2026-10-01") = fecha local del medio guatemalteco
    return (dt.astimezone(_GT) if dt.tzinfo else dt).strftime("%Y-%m-%d")


def _items_xml(contenido: bytes) -> list[dict]:
    """Ítems de un RSS, Atom o sitemap de noticias: [{url, titulo, fecha (texto crudo)}].

    Tolerante: algunos feeds traen espacios antes de la declaración XML (La Hora) o
    caracteres inválidos (CRN); si el XML no parsea, se extraen los <item> por patrón.
    """
    import xml.etree.ElementTree as ET

    texto = contenido.lstrip(b"\xef\xbb\xbf \t\r\n")
    def local(tag):
        return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""

    try:
        raiz = ET.fromstring(texto)
    except ET.ParseError:
        items = []
        for bloque in re.findall(rb"<item\b.*?</item>", texto, re.S):
            campo = lambda n: re.search(rb"<" + n + rb"\b[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</" + n + rb">", bloque, re.S)
            m_url, m_tit, m_fec = campo(b"link"), campo(b"title"), campo(b"pubDate")
            items.append({"url": m_url.group(1).decode("utf-8", "replace").strip() if m_url else "",
                          "titulo": m_tit.group(1).decode("utf-8", "replace").strip() if m_tit else "",
                          "fecha": m_fec.group(1).decode("utf-8", "replace").strip() if m_fec else ""})
        return items

    items = []
    for el in raiz.iter():
        if local(el.tag) not in ("item", "entry", "url"):
            continue
        campos: dict[str, str] = {}
        for hijo in el.iter():
            nombre = local(hijo.tag)
            valor = (hijo.text or "").strip() or (hijo.get("href") or "").strip()
            if nombre and valor and nombre not in campos:  # el primero manda (no el de media:*)
                campos[nombre] = valor
        items.append({
            "url": campos.get("link") or campos.get("loc") or "",
            "titulo": campos.get("title", ""),
            "fecha": (campos.get("pubDate") or campos.get("publication_date") or campos.get("published")
                      or campos.get("updated") or campos.get("lastmod") or ""),
        })
    return items


def descubrir_feeds(feeds: list, dias: int = DIAS_ARTICULO, get=requests.get) -> list[dict]:
    """Notas recientes de feeds DIRECTOS de los medios (más frescos que Bing News).

    Cada entrada es la URL de un RSS/Atom o de un sitemap de noticias (formato Google
    News), o {"url": ..., "filtro_url": "/guatemala/"} para medios regionales. Solo
    entran notas cuyo título o URL habla de combustibles (no se gasta LLM en el resto).
    La fecha se toma en hora de Guatemala; si el medio actualiza una nota con la misma
    URL, su fecha nueva es la que cuenta (ver `_hay_que_leer`).

    Returns:
        [{url, medio, titulo, publicado (YYYY-MM-DD)}] sin duplicados, más recientes primero.
    """
    from collector.db import hace_dias_gt

    limite = hace_dias_gt(dias)
    vistos, notas = set(), []
    for entrada in feeds or []:
        feed = entrada.get("url", "") if isinstance(entrada, dict) else str(entrada)
        filtro = entrada.get("filtro_url") if isinstance(entrada, dict) else None
        try:
            resp = get(feed, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            items = _items_xml(resp.content)
        except Exception as exc:
            print(f"[consejo] feed {feed}: {type(exc).__name__}: {exc}")
            continue
        for it in items:
            url, titulo = it["url"], it["titulo"]
            publicado = _fecha_gt(it["fecha"])
            if (not url.startswith("http") or url in vistos or not publicado or publicado < limite
                    or (filtro and filtro not in url)
                    or not (_RE_TITULO_COMBUSTIBLE.search(titulo)
                            or _RE_TITULO_COMBUSTIBLE.search(urlparse(url).path))):
                continue
            vistos.add(url)
            notas.append({"url": url, "medio": _medio(url), "titulo": titulo, "publicado": publicado})
    notas.sort(key=lambda n: n["publicado"], reverse=True)
    return notas


def _palabras(texto: str) -> set[str]:
    """Palabras significativas (4 letras o más) de un título o de la ruta de una URL."""
    return {p for p in re.split(r"[\W_]+", texto.lower()) if len(p) >= 4}


def _nota_cubierta(medio: str, titulo: str, candidatas: list[dict]) -> bool:
    """¿Alguna candidata del mismo medio cuenta esa nota? El medio usa títulos distintos
    en su sitemap y en Google News, así que se comparan palabras (título + URL)."""
    buscadas = _palabras(titulo)
    if not buscadas:
        return True
    for n in candidatas:
        if n["medio"] == medio or n["medio"].endswith("." + medio) or medio.endswith("." + n["medio"]):
            propias = _palabras(n["titulo"]) | _palabras(urlparse(n["url"]).path)
            if len(buscadas & propias) / len(buscadas) >= 0.4:
                return True
    return False


def cobertura_google_news(consultas: list[str], candidatas: list[dict], hoy: str,
                          get=requests.get) -> list[tuple[str, str]]:
    """Diagnóstico: notas de HOY en Google News que el consejo no descubrió.

    Google News no da el enlace al medio (sus URLs son internas), así que no sirve
    para leer notas; sí para avisar en el log qué nota existe y no tenemos (y así
    sumar el feed de ese medio, leyendo su robots.txt).

    Returns:
        [(dominio del medio, título)] sin repetir.
    """
    faltan, vistos = [], set()
    for q in consultas or []:
        rss = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=es-419&gl=GT&ceid=GT:es-419"
        try:
            resp = get(rss, headers={"User-Agent": UA}, timeout=20)
            resp.raise_for_status()
            import xml.etree.ElementTree as ET
            items = ET.fromstring(resp.content).findall(".//item")
        except Exception as exc:
            print(f"[consejo] Google News '{q}': {type(exc).__name__}: {exc}")
            continue
        for it in items:
            fuente = it.find("source")
            medio = _medio(fuente.get("url", "")) if fuente is not None else ""
            titulo = (it.findtext("title") or "").strip()
            if fuente is not None and fuente.text and titulo.endswith(" - " + fuente.text.strip()):
                titulo = titulo[: -len(" - " + fuente.text.strip())]
            if (_fecha_gt(it.findtext("pubDate")) != hoy or not medio
                    or not _RE_TITULO_COMBUSTIBLE.search(titulo) or (medio, titulo) in vistos
                    or _nota_cubierta(medio, titulo, candidatas)):
                continue
            vistos.add((medio, titulo))
            faltan.append((medio, titulo))
    return faltan


# Cifra en quetzales: "Q36.19", "Q 36,24", "Q.36.24", "36.24 quetzales"
_RE_CIFRA_Q = re.compile(r"Q\.?\s?\d{2}[.,]\d{1,2}\b|\b\d{2}[.,]\d{2}\s*quetzales", re.I)
_RE_MODALIDAD = re.compile(r"autoservicio|servicio completo", re.I)
# Contenedor del cuerpo de la nota (el resto son menús y notas relacionadas)
_SELECTORES_CUERPO = ('[itemprop="articleBody"]', "article", "main", ".entry-content",
                      ".article-body", ".post-content", ".td-post-content")


def texto_de_html(html: str) -> str:
    """Texto legible de una nota: título + cuerpo (párrafos, subtítulos, listas y tablas).

    - Conserva líneas CORTAS si traen una cifra en quetzales o una modalidad: las
      notas diarias publican listas tipo "Autoservicio" / "Súper: Q36.19" (antes el
      corte de 20 caracteres las borraba y el LLM nunca veía el precio).
    - Cada fila de tabla sale en una línea ("Superior | Q45.29 | Q36.24"), no celda suelta.
    - Solo el contenedor principal de la nota, si se reconoce.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside", "form", "noscript", "iframe"]):
        tag.decompose()

    h1 = soup.find("h1")
    titulo = h1.get_text(" ", strip=True) if h1 else ""
    cuerpo, mejor = soup, 0
    for sel in _SELECTORES_CUERPO:
        for el in soup.select(sel):
            largo = sum(len(p.get_text(strip=True)) for p in el.find_all("p"))
            if largo > mejor:
                cuerpo, mejor = el, largo
    if mejor < 200:  # contenedor irreconocible: toda la página
        cuerpo = soup

    lineas = [titulo] if titulo else []
    for el in cuerpo.find_all(["h1", "h2", "h3", "h4", "p", "li", "tr", "blockquote", "figcaption"]):
        if el.name in ("p", "li") and el.find_parent("tr"):
            continue  # ya sale con su fila
        if el.name == "tr":
            texto = " | ".join(c.get_text(" ", strip=True) for c in el.find_all(["th", "td"]))
        else:
            texto = el.get_text(" ", strip=True)
        texto = re.sub(r"\s+", " ", texto).strip()
        if not texto or texto in lineas:
            continue
        if len(texto) > 20 or el.name in ("h2", "h3", "h4") or _RE_CIFRA_Q.search(texto) or _RE_MODALIDAD.search(texto):
            lineas.append(texto)
    return "\n".join(lineas)


def texto_articulo(url: str) -> str:
    """Descarga la nota y devuelve su texto legible (ver `texto_de_html`)."""
    resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    resp.raise_for_status()
    return texto_de_html(resp.text)


def _recortar_para_llm(texto: str, max_chars: int = MAX_CHARS_TEXTO) -> str:
    """Si la nota es larga, envía al LLM el título, el arranque y cada línea con una
    cifra o modalidad junto a su línea anterior y siguiente (ahí está el contexto:
    encabezado "Autoservicio", "hasta el martes…", "a partir del jueves…")."""
    if len(texto) <= max_chars:
        return texto
    lineas = texto.split("\n")
    keep = set(range(min(4, len(lineas))))
    for i, l in enumerate(lineas):
        if _RE_CIFRA_Q.search(l) or _RE_MODALIDAD.search(l):
            keep.update({i - 2, i - 1, i, i + 1})
    salida = "\n".join(lineas[i] for i in sorted(keep) if 0 <= i < len(lineas))
    return salida[:max_chars]


# ──────────────────────────────────────────────
# 2. Extraer precios de una nota
# ──────────────────────────────────────────────

_PROMPT = """Extrae los precios de combustible AL CONSUMIDOR EN GUATEMALA (quetzales por galón) \
del siguiente artículo, publicado el {publicado} ({dia_semana}).

Responde SOLO este JSON:
{{"observaciones": [{{"producto": "superior|regular|diesel", \
"modalidad": "autoservicio|servicio_completo|desconocida", "precio": 45.29, \
"fecha": "YYYY-MM-DD", "tipo": "monitoreado|referencia|estimado|historico|otro_pais|maximo_legal", \
"cita": "frase textual breve de donde sale el precio"}}]}}

Tipos:
- monitoreado: precio observado o reportado en gasolineras para un día ("así amanecieron \
los precios", "los precios reportados este jueves", monitoreo del MEM o del propio medio).
- referencia: "precio de referencia" del MEM, o precio que el MEM o el Gobierno ANUNCIA que \
rige desde una fecha concreta ya confirmada ("a partir de este jueves 1 de octubre el galón \
de súper costará Q36.24"). Su fecha es el día desde el que rige.
- estimado: cálculo hipotético o condicional sin vigencia confirmada ("quedaría", "podría", \
"si se aprueba") o un cálculo hecho por el propio medio.
- historico: precio de una fecha pasada citado como comparación ("hasta el martes estaba en \
Q45.29"; en "pasó de X a Y", X es historico). Usa SU fecha.
- otro_pais: precios de otros países. maximo_legal: topes o precios máximos legales.

Reglas:
- fecha: resuelve "hoy", "este jueves", "a partir del 1 de octubre" con la fecha de \
publicación; si el texto no indica fecha, la de publicación.
- modalidad: la que el texto indique para ESE precio, también si viene de un encabezado \
o del párrafo que introduce la lista (ej. "Autoservicio" seguido de "Súper: Q36.19"). \
Si el artículo no la indica, "desconocida". No la supongas.
- Súper = superior. Un ahorro o rebaja ("Q9.05 menos", "ahorro de Q8.33") NO es un precio.
- Si un mismo precio aparece en las dos modalidades, devuelve una observación por modalidad.
- NO inventes ni calcules: copia el número tal como aparece. Coma decimal → punto.
- Si no hay precios de Guatemala: {{"observaciones": []}}

ARTÍCULO:
{texto}"""

_DIAS_SEMANA = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


def extraer_llm(texto: str, publicado: str, cfg: dict) -> list[dict] | None:
    """Observaciones vía LLM (None si el LLM falla; [] si la nota no trae precios)."""
    global _last_llm_error
    from collector import noticias as _n

    llm = cfg.get("llm", {})
    base_url = llm.get("base_url", "").strip().rstrip("/")
    api_key = _n._obtener_api_key(llm, base_url)
    try:
        dia_semana = _DIAS_SEMANA[datetime.strptime(publicado, "%Y-%m-%d").weekday()]
    except ValueError:
        dia_semana = "fecha sin día"
    prompt = _PROMPT.format(publicado=publicado, dia_semana=dia_semana, texto=_recortar_para_llm(texto))
    raw = _n._llm_post(prompt, base_url, llm.get("model", ""), api_key, temperature=0)
    if raw is None:
        _last_llm_error = _n._last_llm_error
        return None
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        try:
            data = json.loads(m.group(0)) if m else {}
        except json.JSONDecodeError:
            _last_llm_error = "respuesta LLM no es JSON"
            return None
    obs = data.get("observaciones", []) if isinstance(data, dict) else []
    return [o for o in obs if isinstance(o, dict)]


# Frases que indican que el precio de esa oración NO es el vigente en Guatemala
_NO_VIGENTE = re.compile(
    r"estimad|nuevo precio|quedar[íi]a|pas[óo] de|pasaron de|subi[óo] de|baj[óo] de|"
    r"us\$|usd|d[óo]lares|el salvador|honduras|costa rica|nicaragua|panam[áa]|m[ée]xico|"
    r"estados unidos|ee\.? ?uu|tope|m[áa]ximo|anterior|semana pasada|hace un|en enero|en 202[0-5]|"
    # Fechas o rangos explícitos: sin LLM no se sabe a qué día corresponde
    # el precio ("entre el 31 de agosto y el 7…", "monitoreados al 21 de…").
    r"\bentre el\b|\b(?:al|del|el) \d{1,2} de\b",
    re.IGNORECASE,
)
_TOKEN = re.compile(
    r"(?P<prod>superior|s[uú]per|regular|di[eé]sel)|Q\s?(?P<precio>\d{2}[.,]\d{2})\b", re.IGNORECASE,
)


def _producto_de(palabra: str) -> str:
    p = palabra.lower()
    return "superior" if p.startswith(("sup", "súp")) else ("regular" if p == "regular" else "diésel")


# Lista bajo encabezado de modalidad (formato diario de Publinews):
#   "Autoservicio" / "Súper: Q36.19" / "Regular: Q34.89" / "Diésel: Q41.69"
_RE_ENCABEZADO_MOD = re.compile(r"^\W*(autoservicio|servicio completo)\W*$", re.I)
_RE_LINEA_PRODUCTO = re.compile(
    r"^\W*(?:gasolina\s+|combustible\s+)?(superior|s[uú]per|regular|di[eé]sel)\W*\s*:?\s*"
    r"Q\.?\s?(\d{2}[.,]\d{2})(?:\s*(?:por|el|/)\s*gal[oó]n)?\W*$", re.I)
# Contexto que hace dudar de que la lista sea el precio VIGENTE del día de publicación
_RE_NO_VIGENTE_LISTA = re.compile(
    r"a partir|ser[áa]n?\b|quedar[áa]n?|costar[áa]n?|regir[áa]n?|estimad|proyecc|anterior|"
    r"semana pasada|hasta el (?:lunes|martes|mi[ée]rcoles|jueves|viernes|s[áa]bado|domingo)", re.I)
# Oración sin modalidad: además, nada de condicionales, ahorros ni precios pasados
_RE_SIN_MODALIDAD_DUDOSA = re.compile(
    r"pasar[íi]a|ser[íi]a|podr[íi]a|ahorro|rebaja|\bmenos\b|descuento|\beran\b|estaba|registrad|"
    r"promedio|\bmes(?:es)?\b|\ba[ñn]os?\b|\bhasta\b|una vez|medida|\bqued", re.I)


def _dudosa_sin_modalidad(texto: str) -> bool:
    return bool(_NO_VIGENTE.search(texto) or _RE_NO_VIGENTE_LISTA.search(texto)
                or _RE_SIN_MODALIDAD_DUDOSA.search(texto))


def _listas_por_modalidad(texto: str, publicado: str) -> list[dict]:
    """Precios en listas "Encabezado de modalidad" + líneas "Producto: Q00.00".

    Solo las líneas de producto INMEDIATAMENTE después del encabezado; se descarta la
    lista si el título o las 3 líneas previas hablan de futuro, estimados o precios
    anteriores (sin LLM no se sabe a qué fecha corresponden).
    """
    lineas = [l.strip() for l in texto.split("\n") if l.strip()]
    titulo = lineas[0] if lineas else ""
    obs = []
    i = 0
    while i < len(lineas):
        m = _RE_ENCABEZADO_MOD.match(lineas[i])
        if not m:
            i += 1
            continue
        previas = [l for l in lineas[max(0, i - 3):i] if not _RE_ENCABEZADO_MOD.match(l)
                   and not _RE_LINEA_PRODUCTO.match(l)]
        dudosa = any(_RE_NO_VIGENTE_LISTA.search(l) or _NO_VIGENTE.search(l) for l in previas + [titulo])
        modalidad = "autoservicio" if m.group(1).lower() == "autoservicio" else "servicio_completo"
        j = i + 1
        while j < len(lineas) and (p := _RE_LINEA_PRODUCTO.match(lineas[j])):
            if not dudosa:
                obs.append({"producto": _producto_de(p.group(1)), "modalidad": modalidad,
                            "precio": float(p.group(2).replace(",", ".")), "fecha": publicado,
                            "tipo": "monitoreado", "cita": f"{m.group(1)}: {lineas[j]}"[:300]})
            j += 1
        i = j
    return obs


def extraer_regex(texto: str, publicado: str) -> list[dict]:
    """Respaldo sin LLM, a propósito conservador. Una oración aporta precios solo si:
    - nombra UNA modalidad (autoservicio o servicio completo);
    - no trae señales de estimado / histórico / otro país / fecha explícita;
    - productos y precios se ALTERNAN sin ambigüedad (producto→precio o
      precio→producto): "regular Q43.29 … súper Q45.29" o
      "Q45.69 para la superior, Q43.69 para la regular". Si no, se descarta
      (antes emparejaba corrido y asignaba el precio del diésel a la regular).
    Oración SIN modalidad ("Los precios amanecieron así: gasolina superior Q36.19,
    gasolina regular Q34.89 y diésel Q41.69"): solo si da 2+ productos y no habla de
    futuro, ahorros ni precios anteriores → modalidad `desconocida` (respaldo).
    Además, listas bajo un encabezado de modalidad (`_listas_por_modalidad`).
    """
    obs = _listas_por_modalidad(texto, publicado)
    # Título que habla de estimados/futuro ("así quedarían…"): sus cifras sin modalidad no entran
    titulo_dudoso = _dudosa_sin_modalidad(texto.split("\n", 1)[0])
    for oracion in re.split(r"(?<=[.!?;])\s+(?=[A-ZÁÉÍÓÚÑ¿])|\n", texto):
        low = oracion.lower()
        if _NO_VIGENTE.search(low):
            continue
        if "servicio completo" in low and "autoservicio" not in low:
            modalidad = "servicio_completo"
        elif "autoservicio" in low and "servicio completo" not in low:
            modalidad = "autoservicio"
        elif "autoservicio" in low or "servicio completo" in low:
            continue  # nombra las dos: ambigua
        elif titulo_dudoso or _dudosa_sin_modalidad(low):
            continue
        else:
            modalidad = "desconocida"
        tokens = [("prod", m.group("prod")) if m.group("prod") else ("precio", m.group("precio"))
                  for m in _TOKEN.finditer(oracion)]
        # Colapsar menciones repetidas del mismo producto seguidas ("gasolina súper … súper")
        seq = [t for i, t in enumerate(tokens) if not (i and t == tokens[i - 1])]
        if len(seq) < 2 or len(seq) % 2 or any(seq[i][0] == seq[i + 1][0] for i in range(len(seq) - 1)):
            continue
        if modalidad == "desconocida" and len(seq) < 4:
            continue  # sin modalidad, un solo producto no basta
        for a, b in zip(seq[0::2], seq[1::2]):
            prod, precio = (a[1], b[1]) if a[0] == "prod" else (b[1], a[1])
            obs.append({"producto": _producto_de(prod), "modalidad": modalidad,
                        "precio": float(precio.replace(",", ".")), "fecha": publicado,
                        "tipo": "monitoreado", "cita": oracion.strip()[:300]})
    return obs


# La cita habla en condicional o futuro: no puede ser un precio OBSERVADO en bomba.
# El LLM a veces lo etiqueta "monitoreado" (Infobae 1-oct: "la gasolina superior
# pasaría de Q45,29 a Q36,24"; en local salió referencia y en CI monitoreado).
_RE_CITA_NO_OBSERVADA = re.compile(
    r"pasar[íi]a|quedar[íi]a|ser[íi]a|costar[íi]a|estar[íi]a|podr[íi]a|"
    r"costar[áa]n?\b|quedar[áa]n?\b|ser[áa]n?\b|estar[áa]n?\b|regir[áa]n?\b|a partir|"
    r"estimad|proyecc|c[áa]lcul|"
    # "de Q43,26 a Q34,93": un cambio, no una observación (el LLM recorta el verbo de la cita)
    r"\bde\s+q\.?\s?\d{2}[.,]\d{1,2}\s+a\s+q", re.I)


def _tipo_efectivo(tipo: str, cita: str | None) -> str:
    """Tipo de la observación corregido por su cita (regla fija, no depende del LLM)."""
    if tipo == "monitoreado" and _RE_CITA_NO_OBSERVADA.search(cita or ""):
        return "referencia"
    return tipo


def _cifra_en_texto(precio, texto: str) -> bool:
    """¿El número aparece TEXTUAL en la nota? ("36.24", "36,24", "Q 36.2" para 36.20).

    Defensa contra precios inventados o calculados por el LLM: si no está escrito
    en la nota, no es una observación.
    """
    try:
        entero, dec = f"{float(precio):.2f}".split(".")
    except (TypeError, ValueError):
        return False
    dec_pat = dec if dec[1] != "0" else dec[0] + "0?"
    return re.search(rf"(?<![\d.,]){entero}[.,]{dec_pat}(?!\d)", texto) is not None


def normalizar_observaciones(crudas: list[dict], nota: dict, extractor: str,
                             texto: str | None = None) -> list[dict]:
    """Filtra a lo comparable: Guatemala, tipo vigente, fecha coherente con la
    publicación (hasta 10 días antes, máximo 1 después: precios anunciados para
    mañana) y, si se pasa `texto`, cifra presente textual en la nota.

    Modalidad: autoservicio, servicio_completo o `desconocida` (se guarda así; el
    consejo la usa solo como respaldo de un grupo con modalidad explícita).
    """
    from collector.db import MODALIDAD_DESCONOCIDA

    pub = datetime.strptime(nota["publicado"], "%Y-%m-%d")
    salida = []
    for o in crudas:
        tipo = _tipo_efectivo(str(o.get("tipo", "")).lower(), o.get("cita"))
        modalidad = str(o.get("modalidad", "")).lower().replace(" ", "_")
        if (tipo not in ("monitoreado", "referencia")
                or modalidad not in ("autoservicio", "servicio_completo", MODALIDAD_DESCONOCIDA)):
            continue
        if texto is not None and not _cifra_en_texto(o.get("precio"), texto):
            print(f"[consejo]     descartado (no aparece en la nota): {o.get('producto')} {o.get('precio')}")
            continue
        fecha = str(o.get("fecha") or nota["publicado"])[:10]
        try:
            f = datetime.strptime(fecha, "%Y-%m-%d")
        except ValueError:
            fecha, f = nota["publicado"], pub
        if not (pub - timedelta(days=10) <= f <= pub + timedelta(days=1)):
            continue
        salida.append({
            "fecha": fecha, "producto": o.get("producto"), "modalidad": modalidad,
            "precio": o.get("precio"), "tipo": tipo, "medio": nota["medio"], "url": nota["url"],
            "cita": o.get("cita"), "extractor": extractor,
        })
    return salida


def observaciones_gpp() -> list[dict]:
    """GlobalPetrolPrices: su 'gasoline' coincide con la Superior de SERVICIO
    COMPLETO del MEM (45.74 = SC 21-sep-2026), igual el diésel."""
    from collector.fuentes_alternas import GPP_URL, _fetch, extraer_precios_gpp

    html = _fetch(GPP_URL, timeout=30) or _fetch(GPP_URL, timeout=30)  # 1 reintento (timeouts esporádicos)
    datos = extraer_precios_gpp(html) if html else None
    if not datos:
        return []
    galon = 3.78541
    base = {"fecha": datos["fecha"], "modalidad": "servicio_completo", "tipo": "monitoreado",
            "medio": "globalpetrolprices.com", "url": GPP_URL, "extractor": "api"}
    return [
        {**base, "producto": "superior", "precio": round(datos["gasolina_gtq_liter"] * galon, 2),
         "cita": f"Gasoline {datos['gasolina_gtq_liter']} GTQ/L"},
        {**base, "producto": "diésel", "precio": round(datos["diesel_gtq_liter"] * galon, 2),
         "cita": f"Diesel {datos['diesel_gtq_liter']} GTQ/L"},
    ]


# ──────────────────────────────────────────────
# 3-4. Precisión por fuente y veredicto del consejo
# ──────────────────────────────────────────────

def precision_fuentes(conn) -> dict[str, dict]:
    """Error de cada medio contra el precio OFICIAL del MEM del mismo día,
    producto y modalidad. peso = 1 / (1 + 2·error medio), entre 0.2 y 1.

    Medio sin días comparables → peso neutro 0.5.
    """
    rows = conn.execute("""
        SELECT o.medio, ABS(o.precio - p.precio) AS err
        FROM observaciones o
        JOIN precios p ON p.fecha = o.fecha AND p.producto = o.producto
                      AND p.modalidad = o.modalidad AND p.fuente = 'MEM'
    """).fetchall()
    errores: dict[str, list[float]] = {}
    for medio, err in rows:
        errores.setdefault(medio, []).append(err)
    res = {}
    for medio, errs in errores.items():
        mae = sum(errs) / len(errs)
        res[medio] = {"error_medio": round(mae, 3), "n": len(errs),
                      "peso": round(min(1.0, max(0.2, 1 / (1 + 2 * mae))), 3)}
    res[MEDIO_OFICIAL] = {"error_medio": 0.0, "n": None, "peso": 1.0}
    return res


def _peso(medio: str, precision: dict) -> float:
    return precision.get(medio, {}).get("peso", 0.5)


def _peso_efectivo(voto: dict, precision: dict, ultima: str) -> float:
    """Precisión histórica del medio × recencia (el dato más nuevo pesa más) × tipo
    (lo monitoreado en bomba pesa más que un precio anunciado)."""
    dias = (datetime.strptime(ultima, "%Y-%m-%d") - datetime.strptime(voto["fecha"], "%Y-%m-%d")).days
    return (_peso(voto["medio"], precision) * DECAIMIENTO_DIA ** max(0, dias)
            * PESO_TIPO.get(voto.get("tipo"), 1.0))


def _regimen_fiscal(cfg: dict | None):
    """f(fecha) → True si el precio de esa fecha lleva IVA+IDP (Decreto 22-2026).

    Sin config del decreto, una sola serie (todo igual)."""
    from collector.impuestos import precio_incluye_impuestos

    if not (cfg or {}).get("decreto_22_2026"):
        return lambda fecha: True
    return lambda fecha: precio_incluye_impuestos(fecha, cfg)


def _mas_reciente_por_medio(votos: list[dict], etiqueta: str = "") -> dict[str, dict]:
    """De cada medio, su dato más reciente; el mismo día, monitoreado gana a referencia.

    Si el medio se CONTRADICE ese día (dos notas con valores que difieren más que
    TOLERANCIA, ej. Publinews 1-oct: diésel servicio completo Q42.79 en una nota y
    Q43.79 en otra), gana el valor que más notas repiten; si empatan, el medio no
    vota ese día (no se elige uno a ciegas).
    """
    rango = {"referencia": 0}
    clave = lambda c: (c["fecha"], rango.get(c["tipo"], 1))
    por_medio: dict[str, list[dict]] = {}
    for c in votos:
        por_medio.setdefault(c["medio"], []).append(c)
    salida = {}
    for medio, lista in por_medio.items():
        tope = max(clave(c) for c in lista)
        ultimos = sorted((c for c in lista if clave(c) == tope), key=lambda c: (c["precio"], c["url"]))
        if ultimos[-1]["precio"] - ultimos[0]["precio"] <= TOLERANCIA + 1e-9:
            salida[medio] = ultimos[len(ultimos) // 2]
            continue
        cuenta: dict[float, int] = {}
        for c in ultimos:
            cuenta[c["precio"]] = cuenta.get(c["precio"], 0) + 1
        orden = sorted(cuenta.items(), key=lambda kv: -kv[1])
        if len(orden) == 1 or orden[0][1] > orden[1][1]:
            salida[medio] = next(c for c in ultimos if c["precio"] == orden[0][0])
        else:
            print(f"[consejo]   {medio} se contradice el {tope[0]} ({etiqueta}): "
                  + ", ".join(f"Q{p:.2f}" for p in sorted(cuenta)) + " -> no vota ese dia")
    return salida


def _mediana_ponderada(pares: list[tuple[float, float]]) -> float:
    pares = sorted(pares)
    total = sum(w for _, w in pares)
    acum = 0.0
    for valor, w in pares:
        acum += w
        if acum >= total / 2:
            return round(valor, 2)
    return round(pares[-1][0], 2)


def consejo(conn, producto: str, modalidad: str, hoy: str, precision: dict,
            cfg: dict | None = None) -> dict | None:
    """Veredicto para un producto/modalidad con lo observado en los últimos días.

    - Candidatos: observaciones monitoreado/referencia + el dato oficial MEM
      (tabla precios) de la ventana; de cada medio, su dato más reciente.
    - Régimen fiscal (con `cfg` del Decreto 22-2026): solo precios del mismo régimen
      que el dato más reciente. El 1-oct-2026 el galón bajó ~Q9 al quitar IVA+IDP;
      mezclar el Q45.29 del 28-sep con el Q36.19 del 1-oct haría ganar al viejo.
    - Solo el bloque más reciente (DIAS_BLOQUE) para no mezclar semanas.
    - Peso de cada voto = precisión histórica del medio × 0.85^días × PESO_TIPO.
    - Grupo ganador: el de mayor peso total dentro de ±TOLERANCIA alrededor de un
      voto con modalidad EXPLÍCITA; precio = mediana ponderada del grupo.
    - Notas sin modalidad (`desconocida`): solo RESPALDAN el grupo ganador si su
      precio cae dentro de ±TOLERANCIA y NO cerca de un precio explícito de la otra
      modalidad del mismo producto (autoservicio y servicio completo se separan
      ~Q1). Nunca crean un grupo por sí solas. Van marcadas `modalidad_inferida`.
    - Confianza: alta (≥3 medios coinciden y son ≥60 %, o el MEM + otro),
      media (2 coinciden, o solo el MEM), baja (una sola fuente no oficial o
      fuentes en desacuerdo).
    """
    from collector.db import MODALIDAD_DESCONOCIDA

    desde = (datetime.strptime(hoy, "%Y-%m-%d") - timedelta(days=DIAS_VENTANA)).strftime("%Y-%m-%d")
    filas = [dict(r) for r in conn.execute(
        "SELECT fecha, precio, medio, url, tipo, modalidad, cita FROM observaciones "
        "WHERE producto = ? AND fecha >= ? AND fecha <= ?",
        (producto, desde, hoy),
    )]
    for f in filas:  # también corrige filas ya guardadas antes de la regla
        f["tipo"] = _tipo_efectivo(f["tipo"], f.pop("cita"))
    cand = [f for f in filas if f["modalidad"] == modalidad]
    cand += [{"fecha": r[0], "precio": r[1], "medio": MEDIO_OFICIAL, "url": "https://mem.gob.gt/",
              "tipo": "oficial", "modalidad": modalidad}
             for r in conn.execute(
                 "SELECT fecha, precio FROM precios WHERE producto = ? AND modalidad = ? "
                 "AND fuente = 'MEM' AND fecha >= ? AND fecha <= ?",
                 (producto, modalidad, desde, hoy))]
    if not cand:
        return None

    ultima = max(c["fecha"] for c in cand)
    con_impuestos = _regimen_fiscal(cfg)
    regimen = con_impuestos(ultima)
    corte = (datetime.strptime(ultima, "%Y-%m-%d") - timedelta(days=DIAS_BLOQUE)).strftime("%Y-%m-%d")

    def vigente(c):
        return corte <= c["fecha"] <= ultima and con_impuestos(c["fecha"]) == regimen

    etiqueta = f"{producto} {modalidad}"
    explicitos = _mas_reciente_por_medio([c for c in cand if vigente(c)], etiqueta)
    otra_modalidad = [f["precio"] for f in filas
                      if f["modalidad"] not in (modalidad, MODALIDAD_DESCONOCIDA) and vigente(f)]
    # Un solo dato por medio ANTES de compararlo (si no, de un medio con "monitoreado
    # Q41.69" y "anunciado Q42.94" se colaría el que mejor encaje en otra modalidad).
    sin_modalidad = _mas_reciente_por_medio(
        [f for f in filas if f["modalidad"] == MODALIDAD_DESCONOCIDA and vigente(f)],
        f"{producto} sin modalidad")
    votos = list(explicitos.values())
    if not votos:  # el único medio con dato reciente se contradijo
        return None

    mejor = None
    for centro in votos:
        grupo = [v for v in votos if abs(v["precio"] - centro["precio"]) <= TOLERANCIA + 1e-9]
        clave = (sum(_peso_efectivo(v, precision, ultima) for v in grupo), len(grupo),
                 max(v["fecha"] for v in grupo))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, grupo, centro)
    grupo, centro = mejor[1], mejor[2]

    # Respaldo de notas sin modalidad: solo medios que no votaron con modalidad explícita
    # en el bloque, del MISMO tipo que el grupo (un precio anunciado no respalda uno
    # monitoreado: el 1-oct el diésel anunciado Q42.94, de autoservicio, quedaba a
    # Q0.15 del servicio completo monitoreado Q42.79), junto al centro del grupo y
    # lejos de todo precio explícito de la otra modalidad.
    tipos_grupo = {v["tipo"] for v in grupo}
    if "oficial" in tipos_grupo:
        tipos_grupo |= {"monitoreado", "referencia"}
    respaldo = [
        {**v, "inferida": True} for v in sin_modalidad.values()
        if v["medio"] not in explicitos and v["tipo"] in tipos_grupo
        and abs(v["precio"] - centro["precio"]) <= TOLERANCIA + 1e-9
        and all(abs(v["precio"] - p) > TOLERANCIA + 1e-9 for p in otra_modalidad)
    ]
    grupo = grupo + respaldo
    votos = votos + respaldo
    medios_grupo = {v["medio"] for v in grupo}

    n_c, n_t = len(grupo), len(votos)
    oficial = MEDIO_OFICIAL in medios_grupo
    if (n_c >= 3 and n_c / n_t >= 0.6) or (oficial and n_c >= 2):
        confianza = "alta"
    elif n_c >= 2 or oficial:
        confianza = "media"
    else:
        confianza = "baja"

    return {
        "fecha": max(v["fecha"] for v in grupo),
        "producto": producto,
        "modalidad": modalidad,
        "precio": _mediana_ponderada([(v["precio"], _peso_efectivo(v, precision, ultima)) for v in grupo]),
        "confianza": confianza,
        "n_coinciden": n_c,
        "n_fuentes": n_t,
        "fuentes": sorted(
            ({"medio": v["medio"], "precio": v["precio"], "fecha": v["fecha"], "url": v["url"],
              "tipo": v["tipo"], "modalidad_inferida": bool(v.get("inferida")),
              "coincide": v["medio"] in medios_grupo, "peso": _peso(v["medio"], precision)}
             for v in votos),
            key=lambda f: (not f["coincide"], -f["peso"], f["medio"]),
        ),
    }


# ──────────────────────────────────────────────
# Orquestación
# ──────────────────────────────────────────────

def _hay_que_leer(previo, nota: dict, extractor_actual: str, hoy: str) -> str | None:
    """Motivo para (re)leer una nota, o None si ya está al día.

    - nueva: nunca leída.
    - actualizada: el medio la volvió a publicar con fecha más nueva (Publinews reescribe
      "Así amanecieron los precios…" del 28-sep cada mañana con la MISMA URL; antes,
      por estar "ya leída", el consejo no vio los precios del 1-oct).
    - reintento: la lectura anterior falló (red, HTTP) y la nota es de hoy o ayer.
    - revisar: no traía cifras y es de hoy o ayer (el medio puede completarla después).
    - extractor nuevo: dio 0 precios con un prompt/extractor anterior.
    """
    if previo is None:
        return "nueva"
    if (previo["publicado"] or "") < nota["publicado"]:
        return "actualizada"
    reciente = nota["publicado"] >= (datetime.strptime(hoy, "%Y-%m-%d")
                                     - timedelta(days=DIAS_RELECTURA)).strftime("%Y-%m-%d")
    if previo["error"]:
        return "reintento" if reciente else None
    if not previo["n_obs"]:
        if previo["extractor"] == SIN_PRECIOS:
            return "revisar" if reciente else None
        if previo["extractor"] != extractor_actual:
            return "extractor nuevo"
    return None


# Títulos de las notas diarias de precios: se leen primero si el cupo de LLM no alcanza
_RE_TITULO_PRECIOS = re.compile(r"precio|amanec|cuesta|cuánto|costo|bajan|suben|gal[oó]n|Q\d", re.I)


def ejecutar(cfg: dict = None) -> dict:
    """Descubre → extrae → guarda observaciones → emite veredictos."""
    global _last_llm_error
    _last_llm_error = None
    if cfg is None:
        with open(_project_root / "config.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    params = _cfg_consejo(cfg)

    from collector import noticias as _n
    from collector.db import (
        COMBUSTIBLES, PRODUCTOS, conectar, guardar_consenso, guardar_observaciones,
        guardar_precios, hoy_gt, leer_articulo, registrar_articulo,
    )

    conn = conectar()
    hoy = hoy_gt()
    res = {"fuente": "consejo", "notas_descubiertas": 0, "notas_leidas": 0,
           "observaciones_nuevas": 0, "extractor": None, "veredictos": {}, "llm_error": None}

    # 1. Descubrir: feeds directos primero (los más frescos), luego Bing; sin duplicados por URL.
    de_feeds = descubrir_feeds(params.get("feeds_directos", []), params["dias_articulo"])
    de_bing = descubrir_articulos(params["consultas"], params["dias_articulo"])
    candidatas, vistas = [], set()
    for n in de_feeds + de_bing:
        if n["url"] not in vistas:
            vistas.add(n["url"])
            candidatas.append(n)

    # 2. Decidir qué leer
    usar_llm = _n._llm_available(cfg)
    res["extractor"] = "llm" if usar_llm else "regex"
    extractor_actual = EXTRACTOR_LLM if usar_llm else "regex"
    notas = []
    for n in candidatas:
        motivo = _hay_que_leer(leer_articulo(conn, n["url"]), n, extractor_actual, hoy)
        if motivo:
            notas.append({**n, "motivo": motivo})
    notas.sort(key=lambda n: (n["publicado"], bool(_RE_TITULO_PRECIOS.search(n["titulo"]))), reverse=True)
    notas = notas[: params["max_notas_lectura"]]
    res["notas_descubiertas"] = len(notas)
    motivos = {}
    for n in notas:
        motivos[n["motivo"]] = motivos.get(n["motivo"], 0) + 1
    print(f"[consejo] candidatas: {len(candidatas)} (feeds {len(de_feeds)}, Bing {len(de_bing)}) | "
          f"a leer: {len(notas)} {motivos} | extractor: {extractor_actual}")

    # 3-4. Leer → extraer → guardar observaciones
    llamadas_llm = 0
    for nota in notas:
        extractor, error, obs = extractor_actual, None, []
        try:
            texto = texto_articulo(nota["url"])
            if not _RE_CIFRA_Q.search(texto):
                extractor, crudas = SIN_PRECIOS, []        # sin cifras: no se gasta LLM
            elif usar_llm:
                if llamadas_llm >= params["max_articulos_llm"]:
                    continue  # cupo de LLM agotado: queda pendiente para el próximo run
                if llamadas_llm:
                    time.sleep(params["pausa_llm_seg"])
                llamadas_llm += 1
                crudas = extraer_llm(texto, nota["publicado"], cfg)
                if crudas is None and "429" in (_last_llm_error or ""):
                    time.sleep(60)
                    crudas = extraer_llm(texto, nota["publicado"], cfg)
                if crudas is None:  # LLM falló en esta nota → respaldo regex (se relee luego)
                    extractor, crudas = "regex", extraer_regex(texto, nota["publicado"])
            else:
                crudas = extraer_regex(texto, nota["publicado"])
            obs = normalizar_observaciones(crudas, nota, extractor, texto)
            res["observaciones_nuevas"] += guardar_observaciones(conn, obs)["insertadas"]
        except Exception as exc:
            error = str(exc)[:200]
        registrar_articulo(conn, nota["url"], nota["medio"], nota["titulo"], nota["publicado"],
                           extractor, len(obs), error)
        res["notas_leidas"] += 1
        print(f"[consejo]   {nota['medio']} ({nota['publicado']}, {nota['motivo']}): {len(obs)} precios"
              + (" | sin cifras" if extractor == SIN_PRECIOS else "")
              + (f" | error: {error}" if error else ""))
    pendientes = len(notas) - res["notas_leidas"]
    if pendientes:
        print(f"[consejo] {pendientes} nota(s) con cifras quedan para el próximo run (cupo LLM {params['max_articulos_llm']})")

    # Diagnóstico de cobertura: ¿qué medio publicó hoy y no lo descubrimos?
    faltan = cobertura_google_news(params.get("consultas_google_news", []), candidatas, hoy)
    for medio, titulo in faltan[:12]:
        print(f"[consejo] Google News, nota de hoy no descubierta: {medio} | "
              + titulo[:100].encode("cp1252", "replace").decode("cp1252"))

    try:
        res["observaciones_nuevas"] += guardar_observaciones(conn, observaciones_gpp())["insertadas"]
    except Exception as exc:
        print(f"[consejo] GPP: {exc}")

    # 5. Veredictos
    precision = precision_fuentes(conn)
    for producto in COMBUSTIBLES:
        for modalidad in PRODUCTOS[producto]["modalidades"]:
            v = consejo(conn, producto, modalidad, hoy, precision, cfg)
            if not v:
                continue
            guardar_consenso(conn, v)
            if v["confianza"] != "baja":  # una sola fuente no oficial no entra al historial
                guardar_precios(conn, [{"producto": producto, "modalidad": modalidad,
                                        "fecha": v["fecha"], "precio": v["precio"]}], fuente="Consejo")
            res["veredictos"][f"{producto}/{modalidad}"] = (
                f"Q{v['precio']:.2f} {v['confianza']} ({v['n_coinciden']}/{v['n_fuentes']})")
            print(f"[consejo] {producto:9s} {modalidad:17s} Q{v['precio']:.2f} "
                  f"confianza {v['confianza']} ({v['n_coinciden']}/{v['n_fuentes']} fuentes)")

    res["llm_error"] = _last_llm_error
    conn.close()
    return res


if __name__ == "__main__":
    print(json.dumps(ejecutar(), ensure_ascii=False, indent=2))
