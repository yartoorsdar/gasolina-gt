"""Extractor de precios HTML del MEM usando Playwright (bypass Cloudflare).

Este modulo usa un navegador headless Chromium para acceder a la pagina
del MEM, resolver el challenge de Cloudflare y extraer las tablas HTML
de precios directamente.

Flujo:
  1. Navegar a la pagina del MEM con Playwright (headless)
  2. Esperar a que se rendericen las tablas
  3. Extraer datos de Autoservicio y Servicio Completo
  4. Guardar en DB (tabla precios: fecha, producto, precio, fuente, fetched_at)

Requiere: pip install playwright && playwright install chromium
"""

import asyncio
import re
from datetime import date
from pathlib import Path

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None


# ──────────────────────────────────────────────
# Configuracion
# ──────────────────────────────────────────────

MEM_HTML_URL = (
    "https://mem.gob.gt/que-hacemos/hidrocarburos/"
    "comercializacion-downstream/precios-combustible-nacionales/"
)

# Productos mapeados a las keys que usa la DB
PRODUCTOS_MAP = {
    "gasolina superior": "superior",
    "gasolina regular": "regular",
    "combustible diesel": "diessel",
}


async def _scrape_page(url: str = MEM_HTML_URL) -> BeautifulSoup | None:
    """Navega a la pagina del MEM con Playwright y devuelve el HTML parseado.

    Returns:
        BeautifulSoup object o None si falla.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("[mem_html] Playwright no instalado, saltando scraper HTML")
        return None

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-extensions",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/128.0.0.0 Safari/537.36"
            ),
            locale="es-GT",
            viewport={"width": 1440, "height": 900},
        )
        
        # Inyectar script anti-detección de Cloudflare
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => false});
            window.navigator.plugins = [1,2,3,4,5];
        """)
        
        page = await context.new_page()

        try:
            # Navegar y esperar contenido completo
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            
            if response and response.status != 200:
                print(f"[mem_html] Status {response.status}, reintentando...")
                await page.reload(wait_until="domcontentloaded", timeout=15000)

            # Esperar para Cloudflare challenge (5 seg suficiente)
            await page.wait_for_timeout(5000)
            
            # Verificar que la página tiene datos reales
            body_text = await page.inner_text("body")
            if not any(kw in body_text for kw in ["Q4", "Gasolina", "Combustible"]):
                print("[mem_html] Sin datos visibles, esperando más...")
                await page.wait_for_timeout(5000)

            html = await page.content()
            await browser.close()

            if BeautifulSoup:
                return BeautifulSoup(html, "html.parser")
            else:
                from bs4 import BeautifulSoup as BS
                return BS(html, "html.parser")

        except Exception as exc:
            print(f"[mem_html] Error navegando pagina: {exc}")
            await browser.close()
            raise


def _extraer_precios_de_tabla(soup: BeautifulSoup) -> list[dict]:
    """Extrae precios de las tablas HTML de la pagina del MEM.

    La pagina tiene esta estructura:
        <h2>Comparacion semanal de precios promedio</h2>
        <p>Modalidad: autoservicio</p>
        <table>...</table>
        <p>Modalidad: servicio completo</p>
        <table>...</table>

    Returns:
        Lista de dicts con keys: fecha, producto, precio, modalidad, fuente.
    """
    resultados = []
    today = date.today().isoformat()

    # Buscar todos los encabezados y tablas en orden
    elements = soup.find_all(True)  # todos los elementos

    modalidad_actual = None
    tabla_actual = None
    tablas_encontradas = []

    for el in elements:
        text = el.get_text(strip=True).lower()

        # Detectar seccion de modalidad
        if "autoservicio" in text and "modalidad" not in text:
            modalidad_actual = "autoservicio"
            continue
        elif "servicio completo" in text and "modalidad" not in text:
            modalidad_actual = "servicio completo"
            continue

        # Detectar tabla de precios (tiene "Producto" en header o contiene Q4)
        if el.name == "table":
            table_text = el.get_text()
            if any(kw in table_text for kw in ["Q4", "Gasolina", "Combustible"]):
                tablas_encontradas.append({
                    "modalidad": modalidad_actual or "autoservicio",
                    "html": str(el),
                })

    # Parsear cada tabla encontrada
    for table_info in tablas_encontradas:
        table_soup = BeautifulSoup(table_info["html"], "html.parser")
        rows = table_soup.find_all("tr")

        if len(rows) < 2:
            continue

        modalidad = table_info["modalidad"]

        # Parsear filas de la tabla
        for row in rows[1:]:  # Saltar header
            cells = row.find_all(["td", "th"])
            if len(cells) < 3:
                continue

            product_text = cells[0].get_text(strip=True).lower()

            # Mapear producto
            producto_key = None
            for search_term, key in PRODUCTOS_MAP.items():
                if search_term in product_text:
                    producto_key = key
                    break

            if not producto_key:
                continue

            # Extraer precios de las celdas (formato Q44.61)
            precios = []
            for cell in cells[1:]:
                text = cell.get_text(strip=True)
                match = re.search(r"Q\s*([\d]+\.\d{2})", text.replace(",", "."))
                if match:
                    try:
                        precios.append(float(match.group(1)))
                    except ValueError:
                        pass

            # Tomar el segundo precio (mas reciente, columna de fecha 2)
            if len(precios) >= 2:
                precio = round(precios[-1], 2)  # ultimo precio = mas reciente
            elif len(precios) == 1:
                precio = round(precios[0], 2)
            else:
                continue

            # Validar rango razonable (GTQ/galon)
            if 20 <= precio <= 80:
                resultados.append({
                    "fecha": today,
                    "producto": producto_key,
                    "precio": precio,
                    "modalidad": modalidad,
                    "fuente": "Ministerio de Energia y Minas (HTML)",
                    "url": MEM_HTML_URL,
                })

    return resultados


def ejecutar() -> dict:
    """Orquesta el scraping HTML del MEM.

    Returns:
        Dict con resumen de la ejecucion.
    """
    resultados = {
        "fuente": "mem_html",
        "precios_encontrados": 0,
        "insertados": 0,
        "error": None,
    }

    try:
        soup = asyncio.run(_scrape_page())
        if not soup:
            resultados["error"] = "No se pudo obtener la pagina"
            print(f"[mem_html] {resultados['error']}")
            return resultados

        precios = _extraer_precios_de_tabla(soup)
        resultados["precios_encontrados"] = len(precios)

        if not precios:
            resultados["error"] = "No se encontraron precios en la tabla"
            print("[mem_html] No se encontraron precios")
            return resultados

        # Guardar en DB
        from collector.db import conectar
        conn = conectar()

        from collector.impuestos import precio_incluye_impuestos
        import json
        from pathlib import Path as _Path
        config_path = _Path(__file__).resolve().parent.parent / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

        # Re-ejecutar el mismo día = ACTUALIZAR el precio, no duplicar ni
        # congelar el valor del primer run (mismo patrón que fuentes_alternas).
        from collector.db import borrar_precios_hoy
        _FUENTE_MEM_HTML = "Ministerio de Energia y Minas (HTML)"
        borrados = borrar_precios_hoy(conn, _FUENTE_MEM_HTML)
        if borrados:
            print(f"[mem_html] Actualización: {borrados} precio(s) de hoy reemplazado(s)")

        inserted = 0
        for p in precios:
            from collector.db import insertar_precio
            row_id = insertar_precio(
                conn=conn,
                fecha=p["fecha"],
                producto=p["producto"],
                precio=p["precio"],
                fuente=p.get("fuente", "MEM HTML"),
            )
            if row_id is not None:
                inserted += 1

        conn.close()
        resultados["insertados"] = inserted
        print(f"[mem_html] OK: {inserted} precios insertados")

    except Exception as exc:
        resultados["error"] = str(exc)
        print(f"[mem_html] Error: {exc}")

    return resultados


if __name__ == "__main__":
    import os as _os
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parent.parent
    if str(_root) not in _os.sys.path:
        _os.sys.path.insert(0, str(_root))

    print("=" * 60)
    print("Extractor MEM HTML (Playwright) — gasolina-gt")
    print("=" * 60)

    resultado = ejecutar()
    print(f"\nFuente: {resultado['fuente']}")
    print(f"Precios encontrados: {resultado['precios_encontrados']}")
    print(f"Insertados en DB: {resultado['insertados']}")
    if resultado.get("error"):
        print(f"Error: {resultado['error']}")
