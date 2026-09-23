"""Colector de precios de combustible desde fuentes alternas sin Cloudflare.

Fuentes:
  1. GlobalPetrolPrices.com - precios nacionales por litro (GTQ/USD)
     → https://globalpetrolprices.com/Guatemala
  
  2. Chapin TV - artículos con precios de referencia del MEM
     → Busca el artículo más reciente con precios actualizados

Ambas fuentes son accesibles con HTTP simple (requests), sin Cloudflare.

Version Tracking:
  v1.0.0 — 2026-09-23 — Creación del colector de fuentes alternas
         — GlobalPetrolPrices + Chapin TV como fuentes adicionales
"""

import re
import sys
from datetime import date, datetime
from pathlib import Path

# Agregar al path si se ejecuta como __main__
if __name__ == "__main__":
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

try:
    import requests
except ImportError:
    print("[alternas] requests no instalado")
    requests = None


# ──────────────────────────────────────────────
# Configuración
# ┎──────────────────────────────────────────────
GPP_URL = "https://globalpetrolprices.com/Guatemala/"
CHAPINTV_SEARCH_URL = (
    "https://www.chapintv.com/buscar/"  # Buscador interno de Chapin TV
)

# URLs conocidas de artículos con precios
ARTICULOS_PRECIOS = [
    {
        "url": "https://www.chapintv.com/noticia/gasolina-aumenta-un-quetzal-por-galon-este-5-de-septiembre-asi-estan-los-precios",
        "label": "Gasolina sube Q1 - 5 sept 2026",
    },
    {
        "url": (
            "https://www.chapintv.com/noticia/gasolina-superior-regular-y-diesel"
            "-estos-son-los-precios-actualizados-en-guatemala"
        ),
        "label": "Precios actualizados - marzo 2026",
    },
]

# Regex para extraer precios tipo Q44.61 del texto
PRECIO_RE = re.compile(r"Q(\d+\.\d{2})")


def _fetch(url: str, timeout: int = 15) -> str | None:
    """Hace GET simple a una URL y devuelve el texto."""
    if not requests:
        return None
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
        ),
    }
    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception as exc:
        print(f"[alternas] Error fetching {url}: {exc}")
        return None


def extraer_precios_gpp(html: str) -> dict | None:
    """Extrae precios de GlobalPetrolPrices (por litro, GTQ y USD).

    Returns:
        Dict con keys: fecha, gasolina_gtq_liter, diesel_gtq_liter,
                       gasolina_usd_liter, diesel_usd_liter
        o None si no se encuentran.
    """
    if not html:
        return None

    # Buscar patrón de fecha y precios en la tabla
    # Formato en HTML: "21.09.2026" seguido de valores numéricos
    fecha_match = re.search(r"(\d{2}\.\d{2}\.\d{4})", html)
    if not fecha_match:
        return None

    fecha_str = fecha_match.group(1)  # "21.09.2026"
    try:
        fecha = datetime.strptime(fecha_str, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return None

    # Buscar precios de gasolina y diesel
    # El HTML tiene tablas con "Gasoline prices", valores en GTQ y USD
    lines = html.split("\n")
    gasolina_gtq = None
    diesel_gtq = None
    gasolina_usd = None
    diesel_usd = None

    in_gasolina_section = False
    in_diesel_section = False
    
    # Debug: contar secciones encontradas
    section_count = 0

    for line in lines:
        stripped = line.strip()

        if "gasoline prices" in stripped.lower():
            in_gasolina_section = True
            in_diesel_section = False
            section_count += 1
            continue
        elif "diesel prices" in stripped.lower():
            in_diesel_section = True
            in_gasolina_section = False
            section_count += 1
            continue
        elif "kerosene prices" in stripped.lower():
            in_gasolina_section = False
            in_diesel_section = False
            continue

        if in_gasolina_section and re.search(r'\d+\.\d{3,4}', stripped):
            # Buscar el primer número válido (excluyendo fechas)
            nums = [float(x) for x in re.findall(r'(\d+\.?\d*)', stripped)]
            valid_nums = [n for n in nums if 5 < n < 100]  # Precios razonables por litro
            if len(valid_nums) >= 1:
                gasolina_gtq = valid_nums[0]
            if len(valid_nums) >= 2:
                gasolina_usd = valid_nums[1]

        if in_diesel_section and re.search(r'\d+\.\d{3,4}', stripped):
            nums = [float(x) for x in re.findall(r'(\d+\.?\d*)', stripped)]
            valid_nums = [n for n in nums if 5 < n < 100]
            if len(valid_nums) >= 1:
                diesel_gtq = valid_nums[0]
            if len(valid_nums) >= 2:
                diesel_usd = valid_nums[1]

    # Validar que encontramos al menos datos básicos
    if gasolina_gtq is None or diesel_gtq is None:
        return None

    return {
        "fecha": fecha,
        "gasolina_gtq_liter": round(gasolina_gtq, 3),
        "diesel_gtq_liter": round(diesel_gtq, 3),
        "gasolina_usd_liter": round(gasolina_usd, 3) if gasolina_usd else None,
        "diesel_usd_liter": round(diesel_usd, 3) if diesel_usd else None,
    }


def extraer_precios_chapintv(html: str, url_label: str = "") -> dict | None:
    """Extrae precios de un artículo de Chapin TV.

    Busca patrones como "Gasolina superior en Q43.09" o similar.

    Returns:
        Dict con keys: fecha (del article), superior, regular, diesel
        o None si no se encuentran.
    """
    if not html:
        return None

    # Extraer fecha del JSON-LD
    json_match = re.search(
        r'"datePublished"\s*:\s*"([^"]+)"', html
    )
    fecha_str = None
    if json_match:
        try:
            dt = datetime.fromisoformat(json_match.group(1))
            fecha_str = dt.strftime("%Y-%m-%d")
        except ValueError:
            pass

    # Usar BeautifulSoup para extraer solo el texto del artículo (no scripts/nav)
    article_text = html  # fallback
    
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        
        # Estrategia: buscar párrafos que contengan precios de combustible
        paragraphs_with_prices = []
        for p in soup.find_all("p"):
            txt = p.get_text()
            if "Q" in txt and any(
                kw in txt.lower() 
                for kw in ["superior", "regular", "diésel", "diesel"]
            ):
                paragraphs_with_prices.append(txt)
        
        if paragraphs_with_prices:
            # Unir los párrafas relevantes (normalmente 1-3 párrafos)
            article_text = "\n".join(paragraphs_with_prices[:5])
        else:
            # Fallback: extraer body text menos scripts/nav/footer/header
            for tag in ["script", "style", "nav", "footer", "header"]:
                for el in soup.find_all(tag):
                    el.decompose()
            article_text = soup.get_text(separator=" ", strip=True)[:10000]
    except ImportError:
        pass

    # Debug: mostrar texto extraído si es corto (para verificar)
    if len(article_text) < 2000:
        import logging
        logging.debug(f"[alternas] Articulo text ({len(article_text)} chars): {article_text[:500]}")

    # Buscar precios en el texto del artículo (ahora solo body, no HTML completo)
    superior = None
    regular = None
    diesel = None

    # Patrón 1: "gasolina superior en QXX.XX" / "Gasolina superior: QXX.XX"
    pat_superior = re.search(
        r'gasolina\s+superior[^Q]*?Q(\d+\.\d{2})', article_text, re.IGNORECASE
    )
    if pat_superior:
        superior = float(pat_superior.group(1))

    # Patrón 2: "la regular alcanza QXX.XX" / "gasolina regular QXX.XX"
    pat_regular = re.search(
        r'(?:\bla\s+)?regular[^Q]*?Q(\d+\.\d{2})', article_text, re.IGNORECASE
    )
    if pat_regular:
        regular = float(pat_regular.group(1))

    # Patrón 3: "diésel... se mantiene en QXX.XX" o "valor de diésel QXX.XX"
    pat_diesel = re.search(
        r'diés?sel[^Q]*?se\s+mantiene\s+en\s+Q(\d+\.\d{2})', article_text, re.IGNORECASE
    )
    if not pat_diesel:
        # Fallback: buscar "diésel" seguido de cualquier precio cercano
        pat_diesel = re.search(
            r'diés?sel[^Q]{5,}Q(\d+\.\d{2})', article_text, re.IGNORECASE
        )
    if not pat_diesel:
        # Último fallback: buscar "mantener en Q" (diésel suele decir "se mantiene")
        pat_diesel = re.search(
            r'mantiene\s+(?:su|el)\s+precio[^Q]*?Q(\d+\.\d{2})', article_text, re.IGNORECASE
        )
    if pat_diesel:
        diesel = float(pat_diesel.group(1))

    # Si no encontramos con patrones específicos, buscar todos los precios Q##.##
    if superior is None and regular is None and diesel is None:
        all_precios = PRECIO_RE.findall(article_text)
        nums = [float(x) for x in all_precios if float(x) > 10]

        # Los artículos de Chapin TV suelen tener 3-6 precios
        # Tomamos los primeros 3 como superior, regular, diesel
        if len(nums) >= 3:
            # Ordenar y tomar los más altos (autoservicio), luego los siguientes
            sorted_nums = sorted(set(nums), reverse=True)
            if len(sorted_nums) >= 1:
                superior = sorted_nums[0]
            if len(sorted_nums) >= 2:
                regular = sorted_nums[1]
            if len(sorted_nums) >= 3:
                diesel = sorted_nums[2]

    if superior is None and regular is None and diesel is None:
        return None

    # Si solo encontramos algunos, usar fecha de hoy como fallback
    if not fecha_str:
        fecha_str = date.today().strftime("%Y-%m-%d")

    return {
        "fecha": fecha_str,
        "superior": round(superior, 2) if superior else None,
        "regular": round(regular, 2) if regular else None,
        "diesel": round(diesel, 2) if diesel else None,
    }


def ejecutar() -> dict:
    """Ejecuta el colector de fuentes alternas.

    Returns:
        Dict con resumen de resultados.
    """
    print("[alternas] Iniciando colector de fuentes alternas...")

    result = {
        "fuente": "fuentes_alternas",
        "gpp_data": None,
        "chapintv_articles": [],
        "total_registros": 0,
    }

    # ── Fuente 1: GlobalPetrolPrices ─────────────────────
    print("[alternas] 1. Consultando GlobalPetrolPrices...")
    gpp_html = _fetch(GPP_URL)
    if gpp_html:
        gpp_data = extraer_precios_gpp(gpp_html)
        if gpp_data:
            result["gpp_data"] = gpp_data
            print(
                f"[alternas]   ✅ GPP: {gpp_data['fecha']} | "
                f"Gasolina: Q{gpp_data['gasolina_gtq_liter']}/L, "
                f"Diesel: Q{gpp_data['diesel_gtq_liter']}/L"
            )

            # Convertir a precios por galón (1 galón = 3.78541 litros)
            GALON_LITROS = 3.78541
            gpp_galon = {
                **gpp_data,
                "gasolina_gtq_galon": round(
                    gpp_data["gasolina_gtq_liter"] * GALON_LITROS, 2
                ),
                "diesel_gtq_galon": round(
                    gpp_data["diesel_gtq_liter"] * GALON_LITROS, 2
                ),
            }
            print(
                f"[alternas]   → Por galón: Gasolina Q{gpp_galon['gasolina_gtq_galon']}, "
                f"Diesel Q{gpp_galon['diesel_gtq_galon']}"
            )
        else:
            print("[alternas]   ⚠️ GPP: HTML obtenido pero no se pudieron extraer precios")
    else:
        print("[alternas]   ❌ GPP: No se pudo fetchear")

    # ── Fuente 2: Chapin TV (artículos conocidos) ────────
    print("[alternas] 2. Consultando artículos de Chapin TV...")
    for art in ARTICULOS_PRECIOS:
        html = _fetch(art["url"])
        if html:
            datos = extraer_precios_chapintv(html, art.get("label", ""))
            if datos:
                result["chapintv_articles"].append({
                    "url": art["url"],
                    "fecha": datos["fecha"],
                    "superior": datos.get("superior"),
                    "regular": datos.get("regular"),
                    "diesel": datos.get("diesel"),
                })
                result["total_registros"] += 1

                sup_str = f"Q{datos['superior']}" if datos.get("superior") else "N/A"
                reg_str = f"Q{datos['regular']}" if datos.get("regular") else "N/A"
                die_str = f"Q{datos['diesel']}" if datos.get("diesel") else "N/A"
                print(
                    f"[alternas]   ✅ {art['label']}: Sup={sup_str}, "
                    f"Reg={reg_str}, Die={die_str}"
                )
            else:
                print(f"[alternas]   ⚠️ {art['label']}: No se extrajeron precios")
        else:
            print(f"[alternas]   ❌ {art['label']}: No se pudo fetchear")

    # ── Guardar en DB ────────────────────────────────────
    print("[alternas] 3. Guardando precios en base de datos...")
    
    # Convertir GPP a formato DB (por galón)
    if gpp_data:
        GALON_LITROS = 3.78541
        gpp_precios_db = [
            {
                "fecha": gpp_data["fecha"],
                "producto": "superior",
                "precio": round(gpp_data["gasolina_gtq_liter"] * GALON_LITROS, 2),
                "fuente": "GlobalPetrolPrices",
                "tipo": "nacional_promedio",
            },
            {
                "fecha": gpp_data["fecha"],
                "producto": "diésel",  # Corregido: con acento como espera el dashboard
                "precio": round(gpp_data["diesel_gtq_liter"] * GALON_LITROS, 2),
                "fuente": "GlobalPetrolPrices",
                "tipo": "nacional_promedio",
            },
        ]
        
        insertados_gpp = guardar_precios_en_db(gpp_precios_db)
        print(f"[alternas]   GPP: {insertados_gpp} registros guardados")
    
    # Convertir Chapin TV a formato DB
    chapintv_precios_db = []
    for art in result["chapintv_articles"]:
        if art.get("superior"):
            chapintv_precios_db.append({
                "fecha": art["fecha"],
                "producto": "superior",
                "precio": art["superior"],
                "fuente": "Chapin TV",
                "tipo": "metro_sondeo",
            })
        if art.get("regular"):
            chapintv_precios_db.append({
                "fecha": art["fecha"],
                "producto": "regular",
                "precio": art["regular"],
                "fuente": "Chapin TV",
                "tipo": "metro_sondeo",
            })
        if art.get("diesel"):
            chapintv_precios_db.append({
                "fecha": art["fecha"],
                "producto": "diésel",  # Corregido: con acento como espera el dashboard
                "precio": art["diesel"],
                "fuente": "Chapin TV",
                "tipo": "metro_sondeo",
            })
    
    if chapintv_precios_db:
        insertados_chapin = guardar_precios_en_db(chapintv_precios_db)
        print(f"[alternas]   ChapinTV: {insertados_chapin} registros guardados")

    # ── Resumen ──────────────────────────────────────────
    print(
        f"[alternas] Completado. Fuentes alternas: "
        f"GPP={'OK' if result['gpp_data'] else 'FAIL'}, "
        f"ChapinTV={len(result['chapintv_articles'])} artículos"
    )

    return result


def guardar_precios_en_db(precios: list[dict]) -> int:
    """Guarda precios de fuentes alternas en la base de datos.

    Args:
        precios: Lista de dicts con keys: fecha, producto, precio, fuente, tipo.

    Returns:
        Número de registros insertados exitosamente.
    """
    from collector.db import conectar, insertar_precio_combustible
    
    conn = conectar()
    inserted = 0
    
    for p in precios:
        try:
            row_id = insertar_precio_combustible(
                conn=conn,
                fecha_obs=p["fecha"],
                producto=p["producto"],
                precio=p["precio"],
                incluye_impuestos=1,
                regimen="normal",
                fuente=p.get("fuente", "fuentes_alternas"),
            )
            if row_id is not None:
                inserted += 1
        except Exception as exc:
            print(f"[alternas-db] Error guardando {p['producto']} Q{p['precio']}: {exc}")
    
    conn.close()
    return inserted


# ──────────────────────────────────────────────
# Entry point para ejecución directa
# ──────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Colector de Fuentes Alternas — Gasolina GT")
    print("GlobalPetrolPrices + Chapin TV (sin Cloudflare)")
    print("=" * 60)

    resultado = ejecutar()

    print(f"\nFuente: {resultado['fuente']}")
    if resultado["gpp_data"]:
        g = resultado["gpp_data"]
        print(
            f"GPP ({g['fecha']}): Gasolina Q{g['gasolina_gtq_liter']}/L, "
            f"Diesel Q{g['diesel_gtq_liter']}/L"
        )
    if resultado["chapintv_articles"]:
        for art in resultado["chapintv_articles"]:
            print(
                f"Chapin TV ({art['fecha']}): Sup={art.get('superior')}, "
                f"Reg={art.get('regular')}, Die={art.get('diesel')}"
            )
