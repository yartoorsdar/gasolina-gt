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
    def test_fila_a_dict_none(self):
        from collector.main import _fila_a_dict
        assert _fila_a_dict(None) == {}

    def test_fila_a_dict_sqlite_row_like(self):
        from collector.main import _fila_a_dict

        class FakeRow(dict):
            def keys(self):
                return ["id", "fecha", "precio"]

        row = FakeRow({"id": 1, "fecha": "2026-09-22", "precio": 35.5})
        result = _fila_a_dict(row)
        assert result["id"] == 1
        assert result["fecha"] == "2026-09-22"
        assert result["precio"] == 35.5

    def test_fila_a_dict_tuple(self):
        from collector.main import _fila_a_dict
        columns = ["a", "b", "c"]
        fila = (1, "texto", None)
        result = _fila_a_dict(fila, columns=columns)
        assert result == {"a": 1, "b": "texto", "c": None}

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

        with patch("collector.main.ejecutar_precios_mem") as mock_pm, \
             patch("collector.main.ejecutar_mem_html") as mock_mh, \
             patch("collector.main.ejecutar_consenso") as mock_cs, \
             patch("collector.main.ejecutar_historico") as mock_hi, \
             patch("collector.main.ejecutar_petroleo") as mock_pt, \
             patch("collector.main.ejecutar_noticias") as mock_n:

            mock_pm.return_value = {"fuente": "mem", "insertados": 6}
            mock_mh.return_value = {"fuente": "html", "insertados": 0}
            mock_cs.return_value = {"fuente": "consenso_validador", "con_senso_alcanzado": 3}
            mock_hi.return_value = {"fuente": "xlsx", "insertados": 100}
            mock_pt.return_value = {"fuente": "eia_api", "insertados": 2}
            mock_n.return_value = {"fuente": "rss_feeds", "insertados": 50}

            resultados = ejecutar_todo(cfg=cfg, exportar=False)

        assert len(resultados) == 6
        modulos = [r["modulo"] for r in resultados]
        assert "precios_mem" in modulos
        assert "mem_html" in modulos
        assert "consenso_precios" in modulos
        assert "historico" in modulos
        assert "petroleo" in modulos
        assert "noticias" in modulos

    def test_ejecutar_todo_con_error(self, cfg):
        from collector.main import ejecutar_todo

        with patch("collector.main.ejecutar_precios_mem") as mock_pm:
            mock_pm.side_effect = ValueError("Fallo MEM")

            resultados = ejecutar_todo(cfg=cfg, exportar=False)

        # Debería continuar con los demás módulos (ahora 6 modulos)
        assert len(resultados) == 6


# ──────────────────────────────────────────────
# 4. Exportación JSON
# ──────────────────────────────────────────────

class TestExportJson:
    def test_exportar_json_crea_archivos(self, cfg, tmp_path):
        from collector.db import conectar_temporal, crear_tablas
        from collector.main import exportar_json
        from datetime import datetime

        # Crear DB temporal con datos de prueba
        conn = conectar_temporal()
        crear_tablas(conn)

        ahora = datetime.now().strftime("%Y-%m-%dT%H:%M:%S-06:00")

        # Insertar precios de combustible actuales
        for i, producto in enumerate(["superior", "regular", "diessel"]):
            conn.execute(
                """INSERT INTO precios_combustible
                   (fecha_observacion, producto, precio, incluye_impuestos, regimen, fuente, fetched_at)
                   VALUES (?, ?, ?, 1, 'normal', 'test_db', ?)""",
                ("2026-09-22", producto, 35.0 + i, ahora),
            )

        # Insertar precios de petróleo actuales
        for ref in ["brent", "wti"]:
            conn.execute(
                """INSERT INTO precios_petroleo
                   (fecha, referencia, usd_barril, fuente, fetched_at)
                   VALUES (?, ?, ?, 'test_db', ?)""",
                ("2026-09-21", ref, 75.0, ahora),
            )

        # Insertar noticias recientes
        conn.execute(
            """INSERT INTO noticias (url, titulo, medio, publicado_at, fetched_at)
               VALUES (?, ?, ?, ?, ?)""",
            ("https://test.com/1", "Petroleo sube 5%", "Test Media",
             "2026-09-22T10:00:00-06:00", ahora),
        )

        conn.commit()

        export_dir = tmp_path / "export"

        resultado = exportar_json(cfg=cfg, output_dir=export_dir, conn=conn)

        # Verificar que se crearon los archivos esperados
        assert "archivos" in resultado
        expected_files = [
            "precios_combustible.json",
            "historial_precios.json",
            "petroleo.json",
            "historial_petroleo.json",
            "noticias.json",
            "resumen.json",
        ]
        for fname in expected_files:
            path = export_dir / fname
            assert path.exists(), f"Archivo {fname} no creado en {export_dir}"

        # Verificar contenido de resumen.json
        resumen_path = export_dir / "resumen.json"
        resumen = json.loads(resumen_path.read_text(encoding="utf-8"))
        assert "actualizado_at" in resumen
        assert "precios_combustible" in resumen
        assert "petroleo" in resumen

    def test_exportar_json_sin_datos(self, cfg, tmp_path):
        """Verifica que la exportación no falla con DB vacía."""
        from collector.db import conectar_temporal, crear_tablas
        from collector.main import exportar_json

        conn = conectar_temporal()
        crear_tablas(conn)

        export_dir = tmp_path / "export"

        resultado = exportar_json(cfg=cfg, output_dir=export_dir, conn=conn)

        assert "archivos" in resultado
        # No hay datos insertados → 0 registros
        assert resultado["total_registros"] == 0


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
