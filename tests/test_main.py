"""Tests unitarios para collector/main.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def cfg():
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture()
def export_dir(tmp_path):
    """Directorio temporal para exportación."""
    d = tmp_path / "export"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ──────────────────────────────────────────────
# 1. Helpers de conversión y escritura JSON
# ──────────────────────────────────────────────

class TestHelpersMain:
    def test_write_json(self, export_dir):
        from collector.main import _write_json

        path = export_dir / "test.json"
        data = {"clave": "valor", "numero": 42}
        _write_json(path, data)

        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert loaded == data


# ──────────────────────────────────────────────
# 2. Ejecución de módulos individuales (mock)
# ──────────────────────────────────────────────

class TestEjecutarModulo:
    def test_modulo_exitoso(self, cfg):
        from collector.main import _ejecutar_modulo

        def mock_func():
            return {"fuente": "test", "insertados": 5}

        resultado = _ejecutar_modulo("test_modulo", mock_func)
        assert resultado["fuente"] == "test"
        assert resultado["insertados"] == 5

    def test_modulo_con_error(self, cfg):
        from collector.main import _ejecutar_modulo

        def mock_func():
            raise ValueError("Test error")

        resultado = _ejecutar_modulo("error_modulo", mock_func)
        assert resultado["fuente"] == "error"
        assert "Test error" in resultado.get("error", "")


# ──────────────────────────────────────────────
# 3. Orquestador principal (ejecutar_todo)
# ──────────────────────────────────────────────

class TestEjecutarTodo:
    def test_ejecutar_todo_con_modulos_mock(self, cfg):
        from collector.main import ejecutar_todo

        with patch("collector.main.ejecutar_mem_html") as mock_mh, \
             patch("collector.main.ejecutar_consenso") as mock_cs, \
             patch("collector.main.ejecutar_historico") as mock_hi, \
             patch("collector.main.ejecutar_petroleo") as mock_pt, \
             patch("collector.main.ejecutar_noticias") as mock_n:

            mock_mh.return_value = {"fuente": "html", "insertados": 0}
            mock_cs.return_value = {"fuente": "consenso_validador", "con_senso_alcanzado": 3}
            mock_hi.return_value = {"fuente": "xlsx", "insertados": 100}
            mock_pt.return_value = {"fuente": "eia_api", "insertados": 2}
            mock_n.return_value = {"fuente": "rss_feeds", "insertados": 50}

            resultados = ejecutar_todo(cfg=cfg, exportar=False)

        assert len(resultados) == 5
        modulos = [r["modulo"] for r in resultados]
        assert "mem_html" in modulos
        assert "consenso_precios" in modulos
        assert "historico" in modulos
        assert "petroleo" in modulos
        assert "noticias" in modulos

    def test_ejecutar_todo_con_error(self, cfg):
        from collector.main import ejecutar_todo

        with patch("collector.main.ejecutar_mem_html") as mock_mh:
            mock_mh.side_effect = ValueError("Fallo HTML")

            resultados = ejecutar_todo(cfg=cfg, exportar=False)

        # Debería continuar con los demás módulos (ahora 5 modulos)
        assert len(resultados) == 5


# ──────────────────────────────────────────────
# 4. Exportación JSON
# ──────────────────────────────────────────────

class TestExportJson:
    def test_exportar_consolidado(self, cfg, tmp_path):
        """Un solo archivo, misma forma para cada producto."""
        from collector.db import conectar_temporal, guardar_precios, hace_dias_gt, hoy_gt
        from collector.main import exportar_json

        conn = conectar_temporal()
        ayer, hoy = hace_dias_gt(1), hoy_gt()
        guardar_precios(conn, [
            {"producto": p, "fecha": f, "precio": 35.0 + i}
            for i, p in enumerate(["superior", "regular", "diessel"]) for f in (ayer, hoy)
        ], "MEM")
        guardar_precios(conn, [{"producto": "wti", "fecha": hoy, "precio": 75.0}], "OilPriceAPI")
        conn.execute(
            "INSERT INTO noticias (url, titulo, medio, publicado_at, relevancia, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("https://test.com/1", "Petroleo sube 5%", "Test Media", hoy + "T10:00:00-06:00", 4, hoy + "T10:00:00-06:00"),
        )
        conn.commit()

        export_dir = tmp_path / "export"
        resultado = exportar_json(cfg=cfg, output_dir=export_dir, conn=conn)

        assert sorted(p.name for p in export_dir.iterdir()) == ["consolidado.json"]
        assert resultado["total_registros"] == 7 + 1  # 7 filas de historial + 1 noticia

        c = json.loads((export_dir / "consolidado.json").read_text(encoding="utf-8"))
        assert c["precios_actualizados"] is True
        assert list(c["productos"]) == ["regular", "superior", "diésel", "wti"]
        for codigo, prod in c["productos"].items():
            assert set(prod) == {"nombre", "categoria", "unidad", "orden", "actual", "historial", "mensual", "anual"}
            assert prod["actual"]["fecha"] == hoy
        assert c["productos"]["diésel"]["historial"] == [
            {"fecha": ayer, "precio": 37.0}, {"fecha": hoy, "precio": 37.0}
        ]
        assert c["productos"]["wti"]["actual"]["fuente"] == "OilPriceAPI"
        assert c["noticias"]["total"] == 1
        assert c["noticias"]["top"][0]["relevancia"] == 4

    def test_promedios_anuales_prioridad(self):
        """diario completo > mensual oficial > semilla > diario parcial."""
        from datetime import date, timedelta

        from collector.db import conectar_temporal, guardar_precios
        from collector.main import _promedios_anuales

        conn = conectar_temporal()
        # 2023: año diario completo (365 días a 30.0)
        d0 = date(2023, 1, 1)
        guardar_precios(conn, [
            {"producto": "regular", "fecha": (d0 + timedelta(days=i)).isoformat(), "precio": 30.0}
            for i in range(365)
        ], "MEM")
        # 2024 y 2019: solo 2 días (parcial)
        guardar_precios(conn, [
            {"producto": "regular", "fecha": "2024-01-01", "precio": 30.0},
            {"producto": "regular", "fecha": "2024-06-01", "precio": 31.0},
            {"producto": "regular", "fecha": "2019-06-01", "precio": 25.0},
        ], "MEM")
        semilla = [
            {"anio": 2020, "producto": "regular", "promedio": 21.24, "fuente": "consolidado histórico"},
            {"anio": 2023, "producto": "regular", "promedio": 99.0, "fuente": "consolidado histórico"},
            {"anio": 2020, "producto": "superior", "promedio": 22.64, "fuente": "consolidado histórico"},
        ]
        mensual = [
            {"anio": 2023, "mes": 1, "producto": "regular", "promedio": 50.0, "fuente": "MEM mensual"},
            {"anio": 2024, "mes": 1, "producto": "regular", "promedio": 28.0, "fuente": "MEM mensual"},
            {"anio": 2024, "mes": 2, "producto": "regular", "promedio": 29.0, "fuente": "MEM mensual"},
        ]
        assert _promedios_anuales(conn, "regular", semilla, mensual) == [
            {"anio": 2019, "promedio": 25.0, "dias": 1, "meses": None, "fuente": "diario"},  # parcial, sin alternativa
            {"anio": 2020, "promedio": 21.24, "dias": None, "meses": None, "fuente": "consolidado histórico"},
            {"anio": 2023, "promedio": 30.0, "dias": 365, "meses": None, "fuente": "diario"},  # diario completo gana
            {"anio": 2024, "promedio": 28.5, "dias": None, "meses": 2, "fuente": "MEM mensual"},  # mensual > diario parcial
        ]

    def test_frescura_ignora_wti(self, cfg, tmp_path):
        """Un WTI de hoy no vuelve "frescos" a combustibles viejos."""
        from collector.db import conectar_temporal, guardar_precios, hoy_gt
        from collector.main import exportar_json

        conn = conectar_temporal()
        guardar_precios(conn, [{"producto": "regular", "fecha": "2024-10-27", "precio": 28.62}], "MEM")
        guardar_precios(conn, [{"producto": "wti", "fecha": hoy_gt(), "precio": 75.0}], "OilPriceAPI")
        exportar_json(cfg=cfg, output_dir=tmp_path, conn=conn)

        c = json.loads((tmp_path / "consolidado.json").read_text(encoding="utf-8"))
        assert c["precios_actualizados"] is False
        assert c["max_fecha_precios"] == "2024-10-27"

    def test_exportar_json_sin_datos(self, cfg, tmp_path):
        """La exportación no falla con DB vacía."""
        from collector.db import conectar_temporal
        from collector.main import exportar_json

        resultado = exportar_json(cfg=cfg, output_dir=tmp_path, conn=conectar_temporal())

        assert resultado["total_registros"] == 0
        c = json.loads((tmp_path / "consolidado.json").read_text(encoding="utf-8"))
        assert all(p["actual"] is None for p in c["productos"].values())


# ──────────────────────────────────────────────
# 5. Carga de configuración
# ──────────────────────────────────────────────

class TestConfig:
    def test_cargar_config(self, cfg):
        from collector.main import cargar_config
        loaded = cargar_config()
        assert "impuestos" in loaded
        assert "petroleo" in loaded
        assert "noticias" in loaded
