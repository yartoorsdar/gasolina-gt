"""Aislamiento global de tests: NINGÚN test toca la DB ni la memoria reales.

Sin esto, tests que llaman colectores con APIs simuladas (ej. petroleo con
usd_barril=71.45 / 68.20) escribían en data/historial.db con fecha de hoy, y
ese valor falso terminaba en data/db/wti.csv y en el dashboard.
"""

import pytest


@pytest.fixture(autouse=True)
def _aislar_datos(tmp_path, monkeypatch):
    from collector import db, memoria

    db_path = tmp_path / "historial_test.db"
    monkeypatch.setattr(db, "_default_db_path", lambda: str(db_path))
    monkeypatch.setattr(memoria, "MEMORIA_DIR", tmp_path / "memoria")
    monkeypatch.setattr(memoria, "SEMILLA_ANUAL_CSV", tmp_path / "memoria" / "anual_semilla.csv")
    monkeypatch.setattr(memoria, "MENSUAL_CSV", tmp_path / "memoria" / "mensual.csv")
