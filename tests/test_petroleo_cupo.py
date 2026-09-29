"""Cuidado de la cuota de OilPriceAPI: 1 llamada por run, historial solo cuando hace falta,
y freno cuando el saldo que informa la propia API es bajo."""

from datetime import datetime

import pytest

from collector import petroleo
from collector.db import _GT, conectar, guardar_precios, hace_dias_gt

LUNES_TEMPRANO = datetime(2026, 9, 28, 5, 0, tzinfo=_GT)     # lunes
LUNES_TARDE = datetime(2026, 9, 28, 12, 0, tzinfo=_GT)
MARTES = datetime(2026, 9, 29, 5, 0, tzinfo=_GT)


class _Resp:
    def __init__(self, headers=None, status_code=200):
        self.headers = headers or {}
        self.status_code = status_code


def _sembrar_wti(dias):
    conn = conectar()
    guardar_precios(conn, [{"producto": "wti", "fecha": hace_dias_gt(d), "precio": 90.0 + d} for d in dias], "OilPriceAPI")
    conn.commit()
    conn.close()


# ── saldo de la API ────────────────────────────────────────

def test_registra_el_saldo_que_informa_la_api(monkeypatch):
    monkeypatch.setattr(petroleo, "saldo_api", None)
    petroleo._registrar_saldo(_Resp({"x-ratelimit-remaining": "9661"}))
    assert petroleo.saldo_api == 9661
    petroleo._registrar_saldo(_Resp({"x-calls-remaining": "42"}))
    assert petroleo.saldo_api == 42


def test_respuesta_sin_cabeceras_o_rara_no_rompe(monkeypatch):
    monkeypatch.setattr(petroleo, "saldo_api", 7)
    petroleo._registrar_saldo(_Resp({}))                       # sin cabeceras: conserva lo que sabía
    petroleo._registrar_saldo(_Resp({"x-ratelimit-remaining": "no-es-numero"}))
    petroleo._registrar_saldo(object())
    assert petroleo.saldo_api == 7


@pytest.mark.parametrize("codigo", [401, 402, 403, 429])
def test_avisa_cuando_el_plan_o_el_cupo_fallan(codigo, capsys):
    petroleo._registrar_saldo(_Resp({}, codigo))
    salida = capsys.readouterr().out
    assert f"HTTP {codigo}" in salida and "no se inventa" in salida
    salida.encode("cp1252")


# ── cuándo pedir el historial ──────────────────────────────

def test_el_lunes_temprano_se_pide_el_historial():
    assert petroleo._razon_historial(LUNES_TEMPRANO) == "refresco semanal (lunes)"


def test_el_lunes_a_mediodia_no_se_repite_si_hay_datos():
    _sembrar_wti([1, 2, 3, 4])
    assert petroleo._razon_historial(LUNES_TARDE) is None


def test_entre_semana_con_datos_no_hace_falta():
    _sembrar_wti([1, 2, 3, 4])
    assert petroleo._razon_historial(MARTES) is None


def test_si_faltan_datos_de_los_ultimos_7_dias_se_pide():
    _sembrar_wti([1])
    assert "faltan datos" in petroleo._razon_historial(MARTES)


def test_se_puede_forzar_con_variable_de_entorno(monkeypatch):
    _sembrar_wti([1, 2, 3, 4])
    monkeypatch.setenv("FORZAR_HISTORIAL_WTI", "1")
    assert petroleo._razon_historial(MARTES) == "forzado"


# ── ejecutar: cuántas llamadas hace ────────────────────────

@pytest.fixture
def api(monkeypatch):
    """Simula OilPriceAPI y cuenta las llamadas."""
    llamadas = {"latest": 0, "historial": 0}
    monkeypatch.setenv("OILPRICEAPI_KEY", "clave-de-prueba")

    def latest(codes=None, api_key=""):
        llamadas["latest"] += 1
        return [{"fecha": "2026-09-29", "usd_barril": 93.41, "referencia": "wti"}]

    def historial(api_key, period="past_month"):
        llamadas["historial"] += 1
        return [{"fecha": "2026-09-28", "usd_barril": 92.4}]

    monkeypatch.setattr(petroleo, "fetch_precios_petroleo", latest)
    monkeypatch.setattr(petroleo, "fetch_historial_wti", historial)
    monkeypatch.setattr(petroleo, "guardar_precios_petroleo", lambda precios, cfg=None: 1)
    monkeypatch.setattr(petroleo, "guardar_serie_petroleo", lambda serie: len(serie))
    return llamadas


def test_un_run_normal_hace_una_sola_llamada(api, monkeypatch, capsys):
    monkeypatch.setattr(petroleo, "saldo_api", 80)
    monkeypatch.setattr(petroleo, "_razon_historial", lambda ahora=None: None)
    res = petroleo.ejecutar(cfg={})
    assert api == {"latest": 1, "historial": 0}
    assert res["fuente"] == "oilpriceapi" and res["historial_dias"] == 0
    assert "Historial omitido" in capsys.readouterr().out


def test_cuando_toca_historial_hace_dos_llamadas(api, monkeypatch):
    monkeypatch.setattr(petroleo, "saldo_api", 80)
    monkeypatch.setattr(petroleo, "_razon_historial", lambda ahora=None: "refresco semanal (lunes)")
    res = petroleo.ejecutar(cfg={})
    assert api == {"latest": 1, "historial": 1} and res["historial_dias"] == 1


def test_con_saldo_bajo_se_salta_el_historial_aunque_toque(api, monkeypatch, capsys):
    monkeypatch.setattr(petroleo, "saldo_api", petroleo.SALDO_MINIMO_HISTORIAL - 1)
    monkeypatch.setattr(petroleo, "_razon_historial", lambda ahora=None: "refresco semanal (lunes)")
    res = petroleo.ejecutar(cfg={})
    assert api == {"latest": 1, "historial": 0}
    assert res["saldo_api"] == petroleo.SALDO_MINIMO_HISTORIAL - 1
    salida = capsys.readouterr().out
    assert "quedan solo" in salida
    salida.encode("cp1252")


def test_sin_saldo_conocido_el_historial_si_se_pide_cuando_toca(api, monkeypatch):
    monkeypatch.setattr(petroleo, "saldo_api", None)
    monkeypatch.setattr(petroleo, "_razon_historial", lambda ahora=None: "forzado")
    petroleo.ejecutar(cfg={})
    assert api["historial"] == 1


def test_si_la_api_no_responde_el_run_no_falla_y_no_pide_historial(monkeypatch):
    monkeypatch.setenv("OILPRICEAPI_KEY", "clave-de-prueba")
    llamadas = {"historial": 0}
    monkeypatch.setattr(petroleo, "fetch_precios_petroleo", lambda codes=None, api_key="": [])
    monkeypatch.setattr(petroleo, "fetch_historial_wti", lambda *a, **k: llamadas.__setitem__("historial", 1) or [])
    res = petroleo.ejecutar(cfg={})
    assert res["fuente"] == "vacio" and llamadas["historial"] == 0
