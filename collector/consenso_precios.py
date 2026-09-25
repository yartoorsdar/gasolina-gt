"""Consejo de precios de combustible: varias fuentes → un precio con confianza.

Flujo (`ejecutar`):
  1. DESCUBRIR notas recientes sobre precios de combustible en Guatemala
     (Bing News RSS: enlaces directos a Prensa Libre, Publinews, La Hora,
     Emisoras Unidas, AGN…) + GlobalPetrolPrices.
  2. EXTRAER de cada nota nueva los precios con su contexto: producto,
     modalidad (autoservicio / servicio completo), tipo, fecha y cita textual.
     LLM (Groq) primero — distingue estimados, precios de otros países,
     comparaciones históricas —; si no hay LLM, regex conservador.
  3. GUARDAR cada precio como `observacion` (tabla propia; nunca pisa precios).
  4. DECIDIR por producto y modalidad (`consejo`): agrupa las fuentes que
     coinciden dentro de ±TOLERANCIA, pondera cada fuente por su precisión
     histórica contra el dato oficial del MEM y emite precio + confianza.

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
MAX_ARTICULOS_LLM = 8     # notas nuevas por run con LLM (Groq: 8K tokens/min)
MAX_CHARS_TEXTO = 6000    # texto de la nota que se envía al LLM
PAUSA_LLM_SEG = 20        # entre requests (cuida el límite de tokens/minuto)
TOLERANCIA = 0.20         # Q/galón: dos fuentes "coinciden" si difieren menos
DIAS_VENTANA = 7          # observaciones consideradas para el veredicto
DIAS_BLOQUE = 3           # dentro de la ventana, solo lo más reciente (±3 días)
DECAIMIENTO_DIA = 0.85    # peso × 0.85 por cada día de antigüedad dentro del bloque
MEDIO_OFICIAL = "MEM (oficial)"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")

_last_llm_error: str | None = None


def _cfg_consejo(cfg: dict | None) -> dict:
    base = {
        "consultas": CONSULTAS, "dias_articulo": DIAS_ARTICULO,
        "max_articulos_llm": MAX_ARTICULOS_LLM, "pausa_llm_seg": PAUSA_LLM_SEG,
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
        rss = f"https://www.bing.com/news/search?q={quote_plus(q)}&format=rss&setlang=es"
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


def texto_articulo(url: str) -> str:
    """Texto legible de la nota (párrafos, listas y tablas)."""
    from bs4 import BeautifulSoup

    resp = requests.get(url, headers={"User-Agent": UA}, timeout=25)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "aside", "form"]):
        tag.decompose()
    partes = [el.get_text(" ", strip=True) for el in soup.find_all(["h1", "h2", "p", "li", "td"])]
    texto = "\n".join(p for p in partes if len(p) > 20)
    return re.sub(r"[ \t]+", " ", texto)


# ──────────────────────────────────────────────
# 2. Extraer precios de una nota
# ──────────────────────────────────────────────

_PROMPT = """Extrae los precios de combustible AL CONSUMIDOR EN GUATEMALA (quetzales por galón) \
del siguiente artículo, publicado el {publicado}.

Responde SOLO este JSON:
{{"observaciones": [{{"producto": "superior|regular|diesel", \
"modalidad": "autoservicio|servicio_completo|desconocida", "precio": 45.29, \
"fecha": "YYYY-MM-DD", "tipo": "monitoreado|referencia|estimado|historico|otro_pais|maximo_legal", \
"cita": "frase textual breve de donde sale el precio"}}]}}

Reglas:
- monitoreado: promedio observado en gasolineras (MEM o monitoreo del propio medio).
- referencia: "precio de referencia" publicado por el MEM.
- estimado: proyección o precio "si se aplica" una medida (exención, subsidio).
- historico: precio de una fecha pasada citado como comparación (usa SU fecha).
- otro_pais: precios de otros países. maximo_legal: topes o precios máximos.
- fecha: la fecha a la que corresponde el precio ("al 21 de septiembre" → esa); \
si no se indica, la de publicación.
- modalidad "desconocida" si el texto no dice autoservicio ni servicio completo.
- NO inventes ni calcules: si el texto no da el número, omítelo. Coma decimal → punto.
- Si no hay precios de Guatemala: {{"observaciones": []}}

ARTÍCULO:
{texto}"""


def extraer_llm(texto: str, publicado: str, cfg: dict) -> list[dict] | None:
    """Observaciones vía LLM (None si el LLM falla; [] si la nota no trae precios)."""
    global _last_llm_error
    from collector import noticias as _n

    llm = cfg.get("llm", {})
    base_url = llm.get("base_url", "").strip().rstrip("/")
    api_key = _n._obtener_api_key(llm, base_url)
    prompt = _PROMPT.format(publicado=publicado, texto=texto[:MAX_CHARS_TEXTO])
    raw = _n._llm_post(prompt, base_url, llm.get("model", ""), api_key)
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


def extraer_regex(texto: str, publicado: str) -> list[dict]:
    """Respaldo sin LLM, a propósito conservador. Una oración aporta precios solo si:
    - nombra UNA modalidad (autoservicio o servicio completo);
    - no trae señales de estimado / histórico / otro país / fecha explícita;
    - productos y precios se ALTERNAN sin ambigüedad (producto→precio o
      precio→producto): "regular Q43.29 … súper Q45.29" o
      "Q45.69 para la superior, Q43.69 para la regular". Si no, se descarta
      (antes emparejaba corrido y asignaba el precio del diésel a la regular).
    """
    obs = []
    for oracion in re.split(r"(?<=[.!?;])\s+(?=[A-ZÁÉÍÓÚÑ¿])|\n", texto):
        low = oracion.lower()
        if _NO_VIGENTE.search(low):
            continue
        if "servicio completo" in low and "autoservicio" not in low:
            modalidad = "servicio_completo"
        elif "autoservicio" in low and "servicio completo" not in low:
            modalidad = "autoservicio"
        else:
            continue
        tokens = [("prod", m.group("prod")) if m.group("prod") else ("precio", m.group("precio"))
                  for m in _TOKEN.finditer(oracion)]
        # Colapsar menciones repetidas del mismo producto seguidas ("gasolina súper … súper")
        seq = [t for i, t in enumerate(tokens) if not (i and t == tokens[i - 1])]
        if len(seq) < 2 or len(seq) % 2 or any(seq[i][0] == seq[i + 1][0] for i in range(len(seq) - 1)):
            continue
        for a, b in zip(seq[0::2], seq[1::2]):
            prod, precio = (a[1], b[1]) if a[0] == "prod" else (b[1], a[1])
            obs.append({"producto": _producto_de(prod), "modalidad": modalidad,
                        "precio": float(precio.replace(",", ".")), "fecha": publicado,
                        "tipo": "monitoreado", "cita": oracion.strip()[:300]})
    return obs


def normalizar_observaciones(crudas: list[dict], nota: dict, extractor: str) -> list[dict]:
    """Filtra a lo comparable: Guatemala, modalidad conocida, tipo vigente y fecha
    coherente con la publicación (hasta 10 días antes, nunca después)."""
    pub = datetime.strptime(nota["publicado"], "%Y-%m-%d")
    salida = []
    for o in crudas:
        tipo = str(o.get("tipo", "")).lower()
        modalidad = str(o.get("modalidad", "")).lower()
        if tipo not in ("monitoreado", "referencia") or modalidad not in ("autoservicio", "servicio_completo"):
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
    """Precisión histórica del medio × recencia (el dato más nuevo pesa más)."""
    dias = (datetime.strptime(ultima, "%Y-%m-%d") - datetime.strptime(voto["fecha"], "%Y-%m-%d")).days
    return _peso(voto["medio"], precision) * DECAIMIENTO_DIA ** max(0, dias)


def _mediana_ponderada(pares: list[tuple[float, float]]) -> float:
    pares = sorted(pares)
    total = sum(w for _, w in pares)
    acum = 0.0
    for valor, w in pares:
        acum += w
        if acum >= total / 2:
            return round(valor, 2)
    return round(pares[-1][0], 2)


def consejo(conn, producto: str, modalidad: str, hoy: str, precision: dict) -> dict | None:
    """Veredicto para un producto/modalidad con lo observado en los últimos días.

    - Candidatos: observaciones monitoreado/referencia + el dato oficial MEM
      (tabla precios) de la ventana; de cada medio, su dato más reciente.
    - Solo el bloque más reciente (DIAS_BLOQUE) para no mezclar semanas.
    - Peso de cada voto = precisión histórica del medio × 0.85^días de antigüedad.
    - Grupo ganador: el de mayor peso total dentro de ±TOLERANCIA; precio =
      mediana ponderada del grupo.
    - Confianza: alta (≥3 medios coinciden y son ≥60 %, o el MEM + otro),
      media (2 coinciden, o solo el MEM), baja (una sola fuente no oficial o
      fuentes en desacuerdo).
    """
    desde = (datetime.strptime(hoy, "%Y-%m-%d") - timedelta(days=DIAS_VENTANA)).strftime("%Y-%m-%d")
    cand = [dict(r) for r in conn.execute(
        "SELECT fecha, precio, medio, url, tipo FROM observaciones "
        "WHERE producto = ? AND modalidad = ? AND fecha >= ? AND fecha <= ?",
        (producto, modalidad, desde, hoy),
    )]
    cand += [{"fecha": r[0], "precio": r[1], "medio": MEDIO_OFICIAL, "url": "https://mem.gob.gt/", "tipo": "oficial"}
             for r in conn.execute(
                 "SELECT fecha, precio FROM precios WHERE producto = ? AND modalidad = ? "
                 "AND fuente = 'MEM' AND fecha >= ? AND fecha <= ?",
                 (producto, modalidad, desde, hoy))]
    if not cand:
        return None

    ultima = max(c["fecha"] for c in cand)
    corte = (datetime.strptime(ultima, "%Y-%m-%d") - timedelta(days=DIAS_BLOQUE)).strftime("%Y-%m-%d")
    por_medio: dict[str, dict] = {}
    for c in sorted((c for c in cand if c["fecha"] >= corte), key=lambda c: c["fecha"]):
        por_medio[c["medio"]] = c  # queda el más reciente de cada medio
    votos = list(por_medio.values())

    mejor = None
    for centro in votos:
        grupo = [v for v in votos if abs(v["precio"] - centro["precio"]) <= TOLERANCIA + 1e-9]
        clave = (sum(_peso_efectivo(v, precision, ultima) for v in grupo), len(grupo),
                 max(v["fecha"] for v in grupo))
        if mejor is None or clave > mejor[0]:
            mejor = (clave, grupo)
    grupo = mejor[1]
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
              "coincide": v["medio"] in medios_grupo, "peso": _peso(v["medio"], precision)}
             for v in votos),
            key=lambda f: (not f["coincide"], -f["peso"], f["medio"]),
        ),
    }


# ──────────────────────────────────────────────
# Orquestación
# ──────────────────────────────────────────────

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
        COMBUSTIBLES, PRODUCTOS, articulo_procesado, conectar, guardar_consenso,
        guardar_observaciones, guardar_precios, hoy_gt, registrar_articulo,
    )

    conn = conectar()
    res = {"fuente": "consejo", "notas_descubiertas": 0, "notas_leidas": 0,
           "observaciones_nuevas": 0, "extractor": None, "veredictos": {}, "llm_error": None}

    # 1-3. Notas nuevas → observaciones
    notas = [n for n in descubrir_articulos(params["consultas"], params["dias_articulo"])
             if not articulo_procesado(conn, n["url"])]
    res["notas_descubiertas"] = len(notas)
    usar_llm = _n._llm_available(cfg)
    res["extractor"] = "llm" if usar_llm else "regex"
    print(f"[consejo] {len(notas)} notas nuevas | extractor: {res['extractor']}")

    for i, nota in enumerate(notas[: params["max_articulos_llm"] if usar_llm else None]):
        extractor, error, obs = res["extractor"], None, []
        try:
            texto = texto_articulo(nota["url"])
            if usar_llm:
                if i:
                    time.sleep(params["pausa_llm_seg"])
                crudas = extraer_llm(texto, nota["publicado"], cfg)
                if crudas is None and "429" in (_last_llm_error or ""):
                    time.sleep(60)
                    crudas = extraer_llm(texto, nota["publicado"], cfg)
                if crudas is None:  # LLM falló en esta nota → respaldo regex
                    extractor, crudas = "regex", extraer_regex(texto, nota["publicado"])
            else:
                crudas = extraer_regex(texto, nota["publicado"])
            obs = normalizar_observaciones(crudas, nota, extractor)
            res["observaciones_nuevas"] += guardar_observaciones(conn, obs)["insertadas"]
        except Exception as exc:
            error = str(exc)[:200]
        registrar_articulo(conn, nota["url"], nota["medio"], nota["titulo"], nota["publicado"],
                           extractor, len(obs), error)
        res["notas_leidas"] += 1
        print(f"[consejo]   {nota['medio']} ({nota['publicado']}): {len(obs)} precios"
              + (f" | error: {error}" if error else ""))

    try:
        res["observaciones_nuevas"] += guardar_observaciones(conn, observaciones_gpp())["insertadas"]
    except Exception as exc:
        print(f"[consejo] GPP: {exc}")

    # 4. Veredictos
    precision = precision_fuentes(conn)
    hoy = hoy_gt()
    for producto in COMBUSTIBLES:
        for modalidad in PRODUCTOS[producto]["modalidades"]:
            v = consejo(conn, producto, modalidad, hoy, precision)
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
