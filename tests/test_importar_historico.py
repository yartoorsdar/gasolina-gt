"""Tests unitarios para collector/importar_historico.py.

Pruebas con datos simulados (no requieren archivos reales).
"""

import json
from pathlib import Path

import pytest
from openpyxl import Workbook


@pytest.fixture()
def cfg():
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# 1. _parse_xlsx_date
# ──────────────────────────────────────────────

class TestParseXlsxDate:
    def test_datetime_object(self):
        from datetime import datetime
        from collector.importar_historico import _parse_xlsx_date
        dt = datetime(2026, 9, 21)
        assert _parse_xlsx_date(dt) == "2026-09-21"

    def test_string_yyyy_mm_dd(self):
        from collector.importar_historico import _parse_xlsx_date
        assert _parse_xlsx_date("2026-03-15") == "2026-03-15"

    def test_string_dmy_slash(self):
        from collector.importar_historico import _parse_xlsx_date
        assert _parse_xlsx_date("15/03/2026") == "2026-03-15"

    def test_none_value(self):
        from collector.importar_historico import _parse_xlsx_date
        assert _parse_xlsx_date(None) is None


# ──────────────────────────────────────────────
# 2. _parse_precio
# ──────────────────────────────────────────────

class TestParsePrecio:
    @pytest.mark.parametrize(
        "entrada, esperado", [
            (42.50, 42.5),
            ("Q42.50", 42.5),
            ("42.50", 42.5),
            ("42,50", 42.5),
            ("Q42,50", 42.5),
        ]
    )
    def test_parse_precios(self, entrada, esperado):
        from collector.importar_historico import _parse_precio
        assert _parse_precio(entrada) == pytest.approx(esperado, abs=0.01)

    def test_none_valor(self):
        from collector.importar_historico import _parse_precio
        assert _parse_precio(None) is None


# ──────────────────────────────────────────────
# 3. parsear_xlsx_historico — integración con openpyxl
# ──────────────────────────────────────────────

class TestParsearXlsxHistorico:
    def _crear_xlsx_mem(self):
        """Crea un XLSX simulado con el formato real del MEM."""
        wb = Workbook()
        ws = wb.active
        ws.title = "PUBLICACION WEB"

        # Filas de encabezado institucional (filas 0-3)
        ws.append(["DIRECCION GENERAL DE HIDROCARBUROS"])
        ws.append(["DEPARTAMENTO DE ANALISIS ECONOMICO"])
        ws.append(["SECCION DE COMERCIALIZACION"])
        ws.append([])
        ws.append(["PRECIOS PROMEDIO MONITOREADOS"])

        # Fila 6: encabezados de columnas
        ws.append([
            "FECHA", "Tipo de Cambio",
            "Gasolina Superior", "Gasolina Regular",
            "Aceite Combustible Diesel", "Bunker", "GLP"
        ])
        # Fila 7: unidades
        ws.append(["Unidades:", "GTQ/USD", "GTQ/GALON", "GTQ/GALON", "GTQ/GALON", "GTQ/GALON", "GTQ/CIL"])

        # Filas de datos (simuladas)
        from datetime import datetime as dt
        ws.append([dt(2026, 9, 15), 7.63, 43.06, 40.93, 46.37, 24.45, 115])
        ws.append([dt(2026, 9, 16), 7.62, 44.66, 42.58, 49.36, 24.45, 115])
        ws.append([dt(2026, 9, 17), 7.62, 44.66, 42.58, 49.36, 24.45, 115])

        # Filas de metadata al final
        ws.append(["Fuente precios nacionales: Seccion Comercializacion"])
        ws.append(["Nota: DE 28/04/2026 se aplica apoyo social"])

        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return buf.getvalue()

    def test_parsea_datos_correctamente(self):
        from collector.importar_historico import parsear_xlsx_historico
        xlsx_bytes = self._crear_xlsx_mem()
        resultados = parsear_xlsx_historico(xlsx_bytes)

        # Debería encontrar precios para los 3 productos principales en las 3 fechas
        assert len(resultados) >= 6  # al menos 2 días x 3 productos

    def test_duplicados_por_fecha_producto(self):
        from collector.importar_historico import parsear_xlsx_historico
        xlsx_bytes = self._crear_xlsx_mem()
        resultados = parsear_xlsx_historico(xlsx_bytes)

        # Verificar que hay datos de los productos esperados
        productos_encontrados = set(r["producto"] for r in resultados)
        assert "superior" in productos_encontrados
        assert "regular" in productos_encontrados
        assert "diessel" in productos_encontrados

    def test_fechas_correctas(self):
        from collector.importar_historico import parsear_xlsx_historico
        xlsx_bytes = self._crear_xlsx_mem()
        resultados = parsear_xlsx_historico(xlsx_bytes)

        fechas = set(r["fecha"] for r in resultados)
        assert "2026-09-15" in fechas or "2026-09-16" in fechas or "2026-09-17" in fechas

    def test_precios_en_rango(self):
        from collector.importar_historico import parsear_xlsx_historico
        xlsx_bytes = self._crear_xlsx_mem()
        resultados = parsear_xlsx_historico(xlsx_bytes)

        for r in resultados:
            assert 20 <= r["precio"] <= 100, f"Precio fuera de rango: {r}"

    def test_vacio_sin_datos(self):
        from collector.importar_historico import parsear_xlsx_historico
        # XLSX con solo encabezados, sin datos
        wb = Workbook()
        ws = wb.active
        ws.append(["FECHA", "Tipo de Cambio", "Gasolina Superior"])
        import io
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        resultados = parsear_xlsx_historico(buf.getvalue())
        assert len(resultados) == 0
