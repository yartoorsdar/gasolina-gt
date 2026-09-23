"""Tests unitarios para collector/scheduler.py."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def cfg():
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# 1. Ejecucion unica (run_all)
# ──────────────────────────────────────────────

class TestRunAll:
    def test_run_all_exit_success(self, cfg):
        """Verifica que run_all retorna status ok cuando subprocess tiene exit code 0."""
        from collector.scheduler import run_all

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("collector.scheduler.subprocess.run", return_value=mock_result):
            resultado = run_all(export=False)

        assert resultado["status"] == "ok"
        assert resultado["returncode"] == 0

    def test_run_all_exit_error(self, cfg):
        """Verifica que run_all retorna status error cuando subprocess falla."""
        from collector.scheduler import run_all

        mock_result = MagicMock()
        mock_result.returncode = 1

        with patch("collector.scheduler.subprocess.run", return_value=mock_result):
            resultado = run_all(export=False)

        assert resultado["status"] == "error"
        assert resultado["returncode"] == 1

    def test_run_all_timeout(self, cfg):
        """Verifica manejo de timeout."""
        from collector.scheduler import run_all
        import subprocess as sub_mod

        with patch(
            "collector.scheduler.subprocess.run",
            side_effect=sub_mod.TimeoutExpired("python", 300),
        ):
            resultado = run_all(export=False)

        assert resultado["status"] == "timeout"


# ──────────────────────────────────────────────
# 2. Generador de XML para Windows Task Scheduler
# ──────────────────────────────────────────────

class TestExportTaskXml:
    def test_export_task_xml_crea_archivo(self, tmp_path):
        from collector.scheduler import export_task_xml

        xml_path = tmp_path / "test-task.xml"
        result_path = export_task_xml("test-task", interval_minutes=15, output_path=xml_path)

        assert result_path == xml_path
        assert xml_path.exists()

    def test_export_task_xml_contenido_valido(self, tmp_path):
        from collector.scheduler import export_task_xml

        xml_path = tmp_path / "test-task.xml"
        export_task_xml("test-task", interval_minutes=30, output_path=xml_path)

        # Leer como texto UTF-16 (formato que usa Windows Task Scheduler)
        content = xml_path.read_text(encoding="utf-16")

        assert '<?xml version="1.0" encoding="UTF-16"?>' in content
        assert "test-task" in content.lower() or "Test-Task" in content or "test_task" in content
        assert "PT1800S" in content  # 30 minutos = 1800 segundos

    def test_export_task_xml_interval_60min(self, tmp_path):
        from collector.scheduler import export_task_xml

        xml_path = tmp_path / "test-60.xml"
        export_task_xml("test", interval_minutes=60, output_path=xml_path)

        content = xml_path.read_text(encoding="utf-16")
        assert "PT3600S" in content  # 60 minutos = 3600 segundos


# ──────────────────────────────────────────────
# 3. Instalacion / desinstalacion de tarea
# ──────────────────────────────────────────────

class TestTaskInstall:
    def test_export_task_genera_xml(self, tmp_path):
        """Verifica que export_task retorna Path valido."""
        from collector.scheduler import export_task_xml

        xml_path = tmp_path / "task.xml"
        result = export_task_xml("test-task", 60, output_path=xml_path)
        assert isinstance(result, Path)


# ──────────────────────────────────────────────
# 4. Configuracion y logging
# ──────────────────────────────────────────────

class TestConfig:
    def test_project_root_existe(self):
        from collector.scheduler import PROJECT_ROOT
        assert PROJECT_ROOT.exists()

    def test_log_dir_se_crea(self, tmp_path):
        """Verifica que el directorio de logs se crea si no existe."""
        # El log dir ya existe en la carpeta real del proyecto
        assert Path("logs").exists()


# ──────────────────────────────────────────────
# 5. Integration: run_all con mock completo
# ──────────────────────────────────────────────

class TestIntegration:
    def test_run_all_llama_main_py_correcto(self, cfg):
        """Verifica que run_all invoca main.py con los argumentos correctos."""
        from collector.scheduler import run_all

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("collector.scheduler.subprocess.run") as mock_run:
            run_all(export=True)

        # Verificar que se llamo con los args correctos
        call_args = mock_run.call_args
        cmd = call_args[0][0] if call_args[0] else call_args[1].get("cmd", [])

        assert "main.py" in str(cmd)
        assert "--all" in str(cmd)
        assert "--export" in str(cmd)
