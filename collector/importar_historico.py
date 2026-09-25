"""Importador de precios históricos del MEM (XLSX diario nacional).

Fuente original: https://mem.gob.gt/historico-precios-nacionales/
Formato XLSX: tabla diaria con columnas FECHA, Gasolina Superior,
Gasolina Regular, Aceite Combustible Diésel, etc.

Plan B: si el sitio bloquea la descarga, el usuario coloca los XLSX
manuales en data/inbox/historico/.

El importador es idempotente (safe to run twice).
"""

import os
import re
from datetime import datetime
from pathlib import Path

import requests

# ──────────────────────────────────────────────
# Configuración — formato real del XLSX MEM
# ──────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "es-GT,es;q=0.9,en;q=0.8",
}

INBOX_DIR = Path(__file__).resolve().parent.parent / "data" / "inbox" / "historico"

# Mapeo de columnas del XLSX real (índices 0-based)
# Fila 6: FECHA | Tipo de Cambio | Gasolina Superior | Gasolina Regular | Aceite Combustible Diésel | Bunker | GLP | AvGas | JET A-1
PRODUCTOS_XLSX = {
    "gasolina superior": ("superior", 2),
    "gasolina regular": ("regular", 3),
    "aceite combustible diesel": ("diessel", 4),
    "bunker": ("bunker", 5),
}


# ──────────────────────────────────────────────
# Descarga de XLSX
# ──────────────────────────────────────────────

def descargar_xlsx_historico(url: str) -> bytes | None:
    """Descarga el archivo XLSX de precios históricos del MEM.

    Args:
        url: URL del archivo XLSX.

    Returns:
        Los bytes del archivo, o None si falla.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        return resp.content
    except requests.RequestException as exc:
        print(f"[historico] Error al descargar XLSX '{url}': {exc}")
        return None


# ──────────────────────────────────────────────
# Parseo de XLSX con openpyxl — formato real MEM
# ──────────────────────────────────────────────

def parsear_xlsx_historico(xlsx_bytes: bytes) -> list[dict]:
    """Parsea el XLSX diario del MEM y extrae precios históricos.

    Formato real del archivo (descubierto 2026-09-22):
      - Una sola hoja llamada "PUBLICACIÓN WEB"
      - Filas 0-3: encabezados institucionales (DIRECCIÓN GENERAL, etc.)
      - Fila 4: título "PRECIOS PROMEDIO MONITOREADOS..."
      - Fila 6: encabezados de columnas (FECHA, Tipo de Cambio, Gasolina Superior...)
      - Fila 7: unidades (GTQ/GALON, etc.)
      - Filas 8+: datos diarios (ej: 2013-02-08, 7.87695, 34.08, 32.67, 31.71...)
      - Últimas filas: notas y fuentes

    Returns:
        Lista de dicts con keys: fecha, producto, precio, fuente
        Puede estar vacía si no se encuentran datos válidos.
    """
    try:
        from openpyxl import load_workbook
        from io import BytesIO
    except ImportError:
        print("[historico] openpyxl no disponible")
        return []

    resultados = []

    wb = load_workbook(filename=BytesIO(xlsx_bytes), read_only=True, data_only=True)
    if not wb.sheetnames:
        wb.close()
        return resultados

    # Procesar todas las hojas del libro (cada hoja puede ser un año diferente)
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows_list = list(ws.iter_rows(values_only=True))

        if len(rows_list) < 10:
            continue

        # La fila de encabezados está en índice 6 (fila 7 visual, 0-based)
        header_row_idx = 6

        # Validar que es la fila correcta buscando "FECHA" en col A
        if not rows_list[header_row_idx]:
            continue

        first_cell = str(rows_list[header_row_idx][0]).strip().lower() if rows_list[header_row_idx][0] else ""
        if "fecha" not in first_cell:
            # Buscar la fila que contiene "FECHA" en col A
            header_row_idx = None
            for idx, row in enumerate(rows_list):
                if row and isinstance(row[0], str) and "fecha" in row[0].strip().lower():
                    header_row_idx = idx
                    break
            if header_row_idx is None:
                continue

        # Filas de datos comienzan después del encabezado + fila de unidades (header_row_idx + 2)
        data_start = header_row_idx + 2

        # Las últimas filas suelen ser notas/fuentes — cortar antes de ellas
        # Detectar la última fila con datos numéricos y cortar ahí
        last_data_idx = len(rows_list) - 1
        for idx in range(len(rows_list) - 1, data_start - 1, -1):
            row = rows_list[idx]
            if not row:
                continue
            first_val = row[0]
            # Si la primera columna no es una fecha válida ni numérica → es metadata
            if isinstance(first_val, str) and "fuente" in first_val.lower():
                last_data_idx = idx - 1
                break
            elif isinstance(first_val, str) and ("nota" in first_val.lower() or "de 28/" in first_val):
                last_data_idx = idx - 1
                break

        # Procesar filas de datos
        for row in rows_list[data_start:last_data_idx + 1]:
            if not row or not row[0]:
                continue

            try:
                fecha_raw = row[0]
                fecha = _parse_xlsx_date(fecha_raw)
                if not fecha:
                    continue

                # Extraer precios para cada producto conocido
                for producto_key, col_idx in PRODUCTOS_XLSX.values():
                    precio_raw = row[col_idx] if col_idx < len(row) else None
                    precio = _parse_precio(precio_raw)

                    if precio is not None and 20 <= precio <= 100:
                        resultados.append({
                            "fecha": fecha,
                            "producto": producto_key,
                            "precio": round(precio, 2),
                            "fuente": "Ministerio de Energía y Minas",
                        })

            except Exception:
                # Saltar filas con formato inesperado
                continue

    wb.close()
    return resultados


def _parse_xlsx_date(value) -> str | None:
    """Convierte un valor de fecha de XLSX a YYYY-MM-DD."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, (int, float)):
        try:
            dt = datetime.fromordinal(int(value))
            if value % 1 > 0:
                fraction = int(round((value % 1) * 86400))
                from datetime import timedelta as _td
                dt += _td(seconds=fraction)
            return dt.strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            pass

    if isinstance(value, str):
        value = value.strip()
        meses = {
            "ene": "01", "feb": "02", "mar": "03", "abr": "04",
            "may": "05", "jun": "06", "jul": "07", "ago": "08",
            "sep": "09", "oct": "10", "nov": "11", "dic": "12",
            "enero": "01", "febrero": "02", "marzo": "03",
            "abril": "04", "junio": "06", "julio": "07",
            "agosto": "08", "septiembre": "09", "octubre": "10",
            "noviembre": "11", "diciembre": "12",
        }

        # Formato "Mes Año" (Marzo 2026, Jan 2025)
        match = re.match(r'(\w+)\s+(\d{4})', value)
        if match:
            mes_nombre, anio = match.groups()
            mes = meses.get(mes_nombre.lower(), "01")
            return f"{anio}-{mes}-01"

        # Formato DD/MM/YYYY o YYYY-MM-DD
        for fmt in ["%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%Y/%m/%d"]:
            try:
                dt = datetime.strptime(value, fmt)
                return dt.strftime("%Y-%m-%d")
            except ValueError:
                continue

    return None


def _parse_precio(value) -> float | None:
    """Extrae un valor numérico de precio."""
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return round(float(value), 2)

    if isinstance(value, str):
        cleaned = value.strip()
        # Quitar símbolo Q
        cleaned = cleaned.replace("Q", "").strip()

        # Detectar formato: si tiene coma con decimales → "42,50" (europ.)
        if "," in cleaned and "." not in cleaned:
            parts = cleaned.split(",")
            if len(parts) == 2 and len(parts[1]) <= 2:
                cleaned = f"{parts[0]}.{parts[1]}"
            else:
                cleaned = cleaned.replace(",", "")

        elif "." in cleaned:
            parts = cleaned.split(".")
            if len(parts) == 2 and len(parts[1]) <= 2:
                pass  # Ya está bien (decimal)
            elif len(parts) > 2:
                cleaned = cleaned.replace(".", "")

        try:
            result = round(float(cleaned), 2)
            return result if not (result < 0 or result > 9999) else None
        except ValueError:
            return None

    return None


# ──────────────────────────────────────────────
# Plan B: procesamiento de XLSX manuales
# ──────────────────────────────────────────────

def procesar_inbox_historico() -> list[dict]:
    """Procesa todos los XLSX en data/inbox/historico/ (plan B).

    Returns:
        Lista de dicts con precios extraídos.
    """
    if not INBOX_DIR.exists():
        return []

    resultados = []
    xlsx_files = sorted(INBOX_DIR.glob("*.xlsx"))

    for xlsx_path in xlsx_files:
        print(f"[historico] Procesando inbox: {xlsx_path.name}")
        try:
            xlsx_bytes = xlsx_path.read_bytes()
            precios = parsear_xlsx_historico(xlsx_bytes)
            resultados.extend(precios)
        except Exception as exc:
            print(f"[historico] Error procesando {xlsx_path.name}: {exc}")

    return resultados


# ──────────────────────────────────────────────
# Integración con DB y resumen
# ──────────────────────────────────────────────

def importar_historico(precios: list[dict], cfg: dict = None) -> dict:
    """Importa precios históricos en la base de datos.

    Args:
        precios: Lista de dicts con keys: fecha, producto, precio, fuente.
        cfg: Configuración (opcional).

    Returns:
        Dict con resumen: rangos importados, conteo por producto, duplicados.
    """
    if cfg is None:
        from pathlib import Path as _Path
        import json
        config_path = _Path(__file__).resolve().parent.parent / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)

    from collector.db import canon_producto, conectar, guardar_precios
    conn = conectar()

    conteo = guardar_precios(conn, precios, fuente="MEM")
    conn.close()

    inserted = conteo["insertados"] + conteo["actualizados"]
    skipped = conteo["sin_cambio"] + conteo["descartados"] + conteo["invalidos"]
    dates_seen = {p["fecha"] for p in precios}
    products_counted = {}
    for p in precios:
        prod = canon_producto(p["producto"])
        products_counted[prod] = products_counted.get(prod, 0) + 1

    min_date = min(dates_seen) if dates_seen else "N/A"
    max_date = max(dates_seen) if dates_seen else "N/A"

    return {
        "rango_fechas": f"{min_date} a {max_date}",
        "total_encontrados": len(precios),
        "insertados": inserted,
        "duplicados_ignorados": skipped,
        "por_producto": products_counted,
    }


def _determinar_regimen_historico(fecha_obs: str, cfg: dict) -> str:
    """Determina el régimen fiscal para una fecha histórica."""
    return "normal"


# ──────────────────────────────────────────────
# Orquestador principal
# ──────────────────────────────────────────────

def ejecutar(url_xlsx: str = "") -> dict:
    """Orquesta todo el flujo de importación histórica.

    Args:
        url_xlsx: URL del XLSX (opcional). Si se deja vacío, usa plan B.

    Returns:
        Dict con resumen de la ejecución.
    """
    resultados = {
        "fuente": "",
        "rango_fechas": "",
        "total_encontrados": 0,
        "insertados": 0,
        "duplicados_ignorados": 0,
        "por_producto": {},
    }

    # Paso 1: Intentar descargar desde URL
    if url_xlsx:
        print(f"[historico] Descargando XLSX de: {url_xlsx}")
        xlsx_bytes = descargar_xlsx_historico(url_xlsx)
        if xlsx_bytes:
            resultados["fuente"] = "mem_download"
            precios = parsear_xlsx_historico(xlsx_bytes)
            print(f"[historico] Precios encontrados en XLSX: {len(precios)}")

            resumen = importar_historico(precios)
            resultados.update(resumen)
            return resultados

    # Paso 2: Plan B — procesar inbox
    print("[historico] Intentando plan B (inbox/historico/)...")
    precios_inbox = procesar_inbox_historico()
    if precios_inbox:
        resultados["fuente"] = "inbox"
        resumen = importar_historico(precios_inbox)
        resultados.update(resumen)
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
    print("Importador de precios históricos MEM — gasolina-gt")
    print("=" * 60)

    resultado = ejecutar()

    print(f"\nFuente: {resultado['fuente']}")
    print(f"Rango de fechas: {resultado.get('rango_fechas', 'N/A')}")
    print(f"Precios encontrados: {resultado.get('total_encontrados', 0)}")
    print(f"Insertados en DB: {resultado.get('insertados', 0)}")
    print(f"Duplicados ignorados: {resultado.get('duplicados_ignorados', 0)}")
    print(f"Por producto: {resultado.get('por_producto', {})}")
