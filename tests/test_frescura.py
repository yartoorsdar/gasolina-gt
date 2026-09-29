"""scripts/frescura.py: la red de seguridad de GitHub solo corre si no hay datos frescos."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import frescura  # noqa: E402

AHORA = datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc)      # 12:00 en Guatemala


def _consolidado(tmp_path, hace_horas, zona="-06:00"):
    gt = (AHORA - timedelta(hours=hace_horas)).astimezone(timezone(timedelta(hours=-6)))
    ts = gt.strftime("%Y-%m-%dT%H:%M:%S") + zona
    ruta = tmp_path / "consolidado.json"
    ruta.write_text(json.dumps({"actualizado_at": ts}), encoding="utf-8")
    return ruta


def test_edad_en_horas(tmp_path):
    assert frescura.edad_horas(_consolidado(tmp_path, 2.5), AHORA) == 2.5


def test_el_cron_de_github_se_salta_si_los_datos_son_frescos(tmp_path):
    assert frescura.decidir("schedule", _consolidado(tmp_path, 1), AHORA) is True
    assert frescura.decidir("schedule", _consolidado(tmp_path, 4.6), AHORA) is True    # cron tardio tras el run de las 11:52


def test_el_cron_de_github_corre_si_los_datos_son_viejos(tmp_path):
    assert frescura.decidir("schedule", _consolidado(tmp_path, 8), AHORA) is False
    assert frescura.decidir("schedule", _consolidado(tmp_path, 6), AHORA) is False     # justo en el limite: corre


def test_disparos_manuales_y_de_cloudflare_nunca_se_saltan(tmp_path):
    ruta = _consolidado(tmp_path, 0.1)
    assert frescura.decidir("workflow_dispatch", ruta, AHORA) is False
    assert frescura.decidir("push", ruta, AHORA) is False
    assert frescura.decidir("", ruta, AHORA) is False


def test_ante_cualquier_duda_corre(tmp_path):
    assert frescura.decidir("schedule", tmp_path / "no_existe.json", AHORA) is False
    malo = tmp_path / "malo.json"
    malo.write_text("no es json", encoding="utf-8")
    assert frescura.decidir("schedule", malo, AHORA) is False
    sin_campo = tmp_path / "sin.json"
    sin_campo.write_text("{}", encoding="utf-8")
    assert frescura.decidir("schedule", sin_campo, AHORA) is False
    sin_zona = tmp_path / "sinzona.json"
    sin_zona.write_text(json.dumps({"actualizado_at": "2026-09-29T11:00:00"}), encoding="utf-8")
    assert frescura.decidir("schedule", sin_zona, AHORA) is False


def test_un_dato_del_futuro_no_cuenta_como_fresco(tmp_path):
    assert frescura.decidir("schedule", _consolidado(tmp_path, -3), AHORA) is False


def test_la_salida_es_una_sola_linea_apta_para_github_output(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(frescura, "CONSOLIDADO", tmp_path / "no_existe.json")
    monkeypatch.setattr(sys, "argv", ["frescura.py", "schedule"])
    assert frescura.main() == 0
    assert capsys.readouterr().out == "saltar=false\n"
