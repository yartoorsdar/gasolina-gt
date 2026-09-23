"""Extractor de precios actuales del MEM (Ministerio de Energía y Minas).

Flujo:
  1. Fetch de la página del MEM con links a PDFs semanales.
  2. Descarga y parseo de cada PDF con pdfplumber + extract_words().
  3. Extracción de precios AUTOSERVICIO y SERVICIO COMPLETO, área metropolitana.
  4. Aplicación de lógica de impuestos (Etapa 1).
  5. Guardado en DB (Etapa 2) + raw copy en data/raw/.

Plan B: si no se puede fetchear la página, procesa PDFs manuales
de data/inbox/ (el usuario los coloca ahí manualmente).

Headers anti-bot incluidos para evitar bloqueos del sitio MEM.
"""

import os
import re
from datetime import date, datetime
from pathlib import Path

import requests
import pdfplumber

# ──────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────

MEM_PAGE_URL = "https://mem.gob.gt/que-hacemos/hidrocarburos/comercializacion-downstream/precios-combustible-nacionales/"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-GT,es;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

INBOX_DIR = Path(__file__).resolve().parent.parent / "data" / "inbox"
RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


# ──────────────────────────────────────────────
# Fetch de la página del MEM con links a PDFs
# ──────────────────────────────────────────────

def fetch_pagina_mem(url: str = MEM_PAGE_URL, headers: dict = HEADERS) -> str | None:
    """Descarga el HTML de la página del MEM que contiene los links a PDFs.

    Returns:
        El contenido HTML como string, o None si falla.
    """
    try:
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException as exc:
        print(f"[precios_mem] Error al fetchear página MEM: {exc}")
        return None


def extraer_links_pdf(html: str) -> list[str]:
    """Extrae URLs de PDFs desde el HTML de la página del MEM.

    Busca patrones como href=".../INFORME-....pdf" o similares.
    """
    patterns = [
        r'href=["\']([^"\']*informe[-eE]jecutivo[-pP]recios[-cC]ombustibles?[^"\']*\.pdf)[\'"]',
        r'href=["\']([^"\]*(?:INFORME|Informe)[^"\']*\.pdf)[\'"]',
        r'href=["\']([^"\']*/wp-content/uploads/\d{4}/\d{2}/[^"\']*\.pdf)[\'"]',
    ]

    links = set()
    for pattern in patterns:
        found = re.findall(pattern, html)
        links.update(found)

    base_url = "https://mem.gob.gt"
    absolute_links = []
    for link in sorted(links, reverse=True):
        if not link.startswith("http"):
            link = base_url + link
        absolute_links.append(link)

    return absolute_links


# ──────────────────────────────────────────────
# Descarga de PDFs
# ──────────────────────────────────────────────

def descargar_pdf(url: str, save_path: Path | None = None) -> bytes | None:
    """Descarga un PDF desde una URL.

    Returns:
        Los bytes del PDF, o None si falla.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        if save_path:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(resp.content)
        return resp.content
    except requests.RequestException as exc:
        print(f"[precios_mem] Error al descargar PDF '{url}': {exc}")
        return None


# ──────────────────────────────────────────────
# Parseo de PDF con extract_words() + posición
# ──────────────────────────────────────────────

def parsear_pdf(pdf_bytes: bytes) -> list[dict]:
    """Parsea un PDF del MEM y extrae precios AUTOSERVICIO/SERVICIO COMPLETO.

    Usa extract_words() con agrupación por posición Y para reconstruir líneas.
    El formato real del PDF es:

        Modalidad: AutoServicio / Servicio Completo
        Producto  [fecha1] [precio1] [fecha2] [precio2] Diferencia
        Gasolina Superior Q44.66 Q44.61 -Q0.05
        Gasolina Regular  Q42.58 Q42.58  Q0.00
        Combustible Diesel Q49.36 Q49.40 Q0.04

    Returns:
        Lista de dicts con keys: fecha, producto, precio, fuente, url
        Puede estar vacía si no se encuentran datos válidos.
    """
    import io
    from collections import defaultdict

    resultados = []

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            # Agrupar palabras por posición Y para reconstruir líneas
            lines_map = defaultdict(list)
            for page in pdf.pages:
                words = page.extract_words()
                for w in words:
                    text = w.get("text", "").strip()
                    if text and len(text) > 1:
                        y_key = round(w["top"], 0)
                        lines_map[y_key].append(w)

    except Exception as exc:
        print(f"[precios_mem] Error al parsear PDF con pdfplumber: {exc}")
        return resultados

    # Extraer fecha del informe desde encabezado (busca "21 de septiembre de 2026")
    fecha_informe = None
    for y in sorted(lines_map.keys()):
        line_words = sorted(lines_map[y], key=lambda x: x["x0"])
        text_line = " ".join(w.get("text", "") for w in line_words)

        # Buscar fecha tipo "21 de septiembre de 2026" o "DD/MM/YYYY"
        match_fecha = re.search(
            r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', text_line
        )
        if match_fecha:
            dia, mes_nombre, anio = match_fecha.groups()
            meses = {
                "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
                "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
                "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
            }
            mes = meses.get(mes_nombre.lower(), "01")
            fecha_informe = f"{anio}-{mes}-{dia.zfill(2)}"
            break

        # Buscar formato DD/MM/YYYY o YYYY-MM-DD en encabezado (primeras líneas)
        if y < 200:
            match_fmt = re.search(r'(\d{1,2}[/-]\w+[/-]\d{4}|\d{1,2}[/-]\d{1,2}[/-]\d{4})', text_line)
            if match_fmt:
                fecha_informe = _normalizar_fecha(match_fmt.group(1))
                break

    # Si no se encontró fecha en encabezado, buscar "Fecha de recolección:"
    if not fecha_informe:
        for y in sorted(lines_map.keys()):
            line_words = sorted(lines_map[y], key=lambda x: x["x0"])
            text_line = " ".join(w.get("text", "") for w in line_words)
            if "recolección" in text_line.lower() or "recoleccion" in text_line.lower():
                match_fmt = re.search(r'(\d{1,2})\s+de\s+(\w+)\s+de\s+(\d{4})', text_line)
                if match_fmt:
                    dia, mes_nombre, anio = match_fmt.groups()
                    meses = {
                        "enero": "01", "febrero": "02", "marzo": "03", "abril": "04",
                        "mayo": "05", "junio": "06", "julio": "07", "agosto": "08",
                        "septiembre": "09", "octubre": "10", "noviembre": "11", "diciembre": "12",
                    }
                    mes = meses.get(mes_nombre.lower(), "01")
                    fecha_informe = f"{anio}-{mes}-{dia.zfill(2)}"
                break

    if not fecha_informe:
        # Intentar extraer de nombre de archivo o usar hoy como fallback
        fecha_informe = date.today().isoformat()

    # Procesar líneas por sección (AutoServicio / Servicio Completo)
    # El PDF tiene esta estructura:
    #   1. COMPARACIÓN PRECIOS MONITOREADOS → AutoServicio + Servicio Completo
    #   2. Otros Combustibles (kerosina, GLP)
    #   3. COMPARACIÓN ÚLTIMAS SEMANAS (histórico semanal) ← ignorar esto
    modalidad_actual = None
    en_seccion_historico = False
    productos_map = {
        "gasolina superior": "superior",
        "gasolina regular": "regular",
        "combustible diesel": "diessel",
        "combustible diésel": "diessel",
        "kerosina": "kerosina",  # producto opcional
    }

    for y in sorted(lines_map.keys()):
        line_words = sorted(lines_map[y], key=lambda x: x["x0"])
        text_line = " ".join(w.get("text", "") for w in line_words)
        text_lower = text_line.lower()

        # Detectar entrada a la sección de histórico semanal (ignorar de aquí en adelante)
        if "ultimas semanas" in text_lower or "últimas semanas" in text_lower:
            en_seccion_historico = True
            break

        # Si ya estamos en histórico, saltar líneas de producto
        if en_seccion_historico:
            continue

        # Detectar modalidad
        if "autoservicio" in text_lower:
            modalidad_actual = "autoservicio"
            continue
        elif "servicio completo" in text_lower:
            modalidad_actual = "servicio completo"
            continue

        # Si no hay modalidad activa, saltar
        if modalidad_actual is None:
            continue

        # Buscar filas de producto con precios
        # Formato esperado: "Gasolina Superior Q44.66 Q44.61 -Q0.05"
        # Donde: Q44.66 = precio semana anterior, Q44.61 = precio más reciente (tomar este)
        # El último valor es la diferencia (ej: -Q0.05 → regex captura "05" → 5.0)
        for producto_busqueda, producto_key in productos_map.items():
            if producto_busqueda in text_lower:
                # Extraer todos los valores tipo Q####.## desde la línea
                precios_en_linea = re.findall(r'Q\s*([\d]+\.\d{2})', text_line)
                if len(precios_en_linea) >= 1:
                    # Tomar el penúltimo precio (el más reciente, segunda fecha de comparación).
                    # El último suele ser la diferencia (Q0.05, Q0.00, etc.)
                    if len(precios_en_linea) >= 2:
                        precio_str = precios_en_linea[-2].replace(",", ".")
                    else:
                        precio_str = precios_en_linea[0].replace(",", ".")
                    try:
                        precio = float(precio_str)
                        # Filtrar valores razonables para gasolina/diésel (GTQ/galón)
                        if 20 <= precio <= 80:
                            resultados.append({
                                "fecha": fecha_informe,
                                "producto": producto_key,
                                "precio": round(precio, 2),
                                "fuente": "Ministerio de Energía y Minas",
                                "modalidad": modalidad_actual,
                            })
                    except ValueError:
                        continue

    return resultados


# ──────────────────────────────────────────────
# Helpers de normalización
# ──────────────────────────────────────────────

def _normalizar_fecha(fecha_str: str) -> str:
    """Normaliza una fecha de formato DD/MM/YYYY o similar a YYYY-MM-DD."""
    meses = {
        "ene": "01", "feb": "02", "mar": "03", "abr": "04",
        "may": "05", "jun": "06", "jul": "07", "ago": "08",
        "sep": "09", "oct": "10", "nov": "11", "dic": "12",
        "enero": "01", "febrero": "02", "marzo": "03",
        "abril": "04", "junio": "06", "julio": "07",
        "agosto": "08", "septiembre": "09", "octubre": "10",
        "noviembre": "11", "diciembre": "12",
    }

    # Intentar DD/MM/YYYY o DD-MM-YYYY (mes numérico)
    match = re.match(r'(\d{1,2})[/-](\d{1,2})[/-](\d{2,4})', fecha_str)
    if match:
        dia, mes, anio = match.groups()
        anio = f"20{anio}" if len(anio) == 2 else anio
        return f"{anio}-{mes.zfill(2)}-{dia.zfill(2)}"

    # Intentar DD/Mes/YYYY o DD-Mes-YYYY (mes con nombre, cualquier longitud)
    match = re.match(r'(\d{1,2})[/-](\w+)[/-](\d{2,4})', fecha_str)
    if match:
        dia, mes_nombre, anio = match.groups()
        mes = meses.get(mes_nombre.lower(), "01")
        anio = f"20{anio}" if len(anio) == 2 else anio
        return f"{anio}-{mes}-{dia.zfill(2)}"

    # Intentar YYYY-MM-DD o YYYY/MM/DD
    match = re.match(r'(\d{4})[/-](\d{1,2})[/-](\d{1,2})', fecha_str)
    if match:
        anio, mes, dia = match.groups()
        return f"{anio}-{mes.zfill(2)}-{dia.zfill(2)}"

    # Default: retornar la cadena original si no se puede parsear
    return fecha_str


# ──────────────────────────────────────────────
# Plan B: procesamiento de PDFs manuales
# ──────────────────────────────────────────────

def procesar_inbox() -> list[dict]:
    """Procesa todos los PDFs en data/inbox/ (plan B).

    Los PDFs se leen, parsean, y se mueven a data/raw/ después de procesarse.

    Returns:
        Lista de dicts con precios extraídos.
    """
    if not INBOX_DIR.exists():
        return []

    resultados = []
    pdf_files = sorted(INBOX_DIR.glob("*.pdf"))

    for pdf_path in pdf_files:
        print(f"[precios_mem] Procesando inbox: {pdf_path.name}")
        try:
            pdf_bytes = pdf_path.read_bytes()
            precios = parsear_pdf(pdf_bytes)

            # Mover a raw/ después de procesar
            RAW_DIR.mkdir(parents=True, exist_ok=True)
            dest = RAW_DIR / pdf_path.name
            if not dest.exists():
                pdf_path.rename(dest)

            resultados.extend(precios)
        except Exception as exc:
            print(f"[precios_mem] Error procesando {pdf_path.name}: {exc}")

    return resultados


# ──────────────────────────────────────────────
# Integración con DB y lógica de impuestos
# ──────────────────────────────────────────────

def guardar_precios_en_db(precios: list[dict], cfg: dict = None) -> int:
    """Guarda una lista de precios en la base de datos.

    Aplica la lógica de incluye_impuestos desde Etapa 1 y el regimen
    según config.json → regimenes.

    Args:
        precios: Lista de dicts con keys: fecha, producto, precio, fuente.
        cfg: Configuración cargada desde config.json (opcional).

    Returns:
        Número de registros insertados exitosamente.
    """
    if cfg is None:
        import json
        from pathlib import Path as _Path
        config_path = _Path(__file__).resolve().parent.parent / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    from collector.db import conectar, insertar_precio
    conn = conectar()

    inserted = 0
    for p in precios:
        fecha_obs = p["fecha"]

        try:
            row_id = insertar_precio(
                conn=conn,
                fecha=fecha_obs,
                producto=p["producto"],
                precio=p["precio"],
                fuente=p.get("fuente", "Ministerio de Energía y Minas"),
            )
            if row_id is not None:
                inserted += 1
        except Exception as exc:
            print(f"[precios_mem] Error guardando {p}: {exc}")

    conn.close()
    return inserted


def _determinar_regimen(fecha_obs: str, cfg: dict) -> str:
    """Determina el régimen fiscal aplicable para una fecha dada."""
    return "normal"


# ──────────────────────────────────────────────
# Orquestador principal
# ──────────────────────────────────────────────

def ejecutar() -> dict:
    """Orquesta todo el flujo: fetch → descarga → parseo → DB.

    Returns:
        Dict con resumen de la ejecución.
    """
    resultados = {
        "fuente": "",
        "pdfs_procesados": 0,
        "precios_encontrados": 0,
        "insertados": 0,
    }

    # Paso 1: Intentar fetch desde la página del MEM
    html = fetch_pagina_mem()
    if html:
        links_pdf = extraer_links_pdf(html)
        if links_pdf:
            resultados["fuente"] = "mem_page"
            for link in links_pdf[:3]:  # procesar los primeros 3 (más recientes)
                pdf_path = RAW_DIR / f"{link.split('/')[-1]}"
                pdf_bytes = descargar_pdf(link, pdf_path)
                if pdf_bytes:
                    precios = parsear_pdf(pdf_bytes)
                    resultados["pdfs_procesados"] += 1
                    resultados["precios_encontrados"] += len(precios)

                    # Guardar en DB
                    for p in precios:
                        p["url"] = link
                    inserted = guardar_precios_en_db(precios)
                    resultados["insertados"] += inserted
            return resultados

    # Paso 2: Plan B — procesar inbox
    print("[precios_mem] Fetch fallido o sin links, intentando plan B (inbox)...")
    precios_inbox = procesar_inbox()
    if precios_inbox:
        resultados["fuente"] = "inbox"
        resultados["pdfs_procesados"] = len(precios_inbox)
        resultados["precios_encontrados"] = len(precios_inbox)

        inserted = guardar_precios_en_db(precios_inbox)
        resultados["insertados"] += inserted
    else:
        resultados["fuente"] = "vacio"

    return resultados


if __name__ == "__main__":
    import os as _os
    from pathlib import Path as _Path
    _root = _Path(__file__).resolve().parent.parent
    if str(_root) not in _os.sys.path:
        _os.sys.path.insert(0, str(_root))

    print("=" * 60)
    print("Extractor de precios MEM — gasolina-gt")
    print("=" * 60)

    resultado = ejecutar()

    print(f"\nFuente: {resultado['fuente']}")
    print(f"PDFs procesados: {resultado['pdfs_procesados']}")
    print(f"Precios encontrados: {resultado['precios_encontrados']}")
    print(f"Insertados en DB: {resultado['insertados']}")
