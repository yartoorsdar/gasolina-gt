"""Tests unitarios para collector/petroleo.py (OilPriceAPI).

Pruebas con datos simulados (no requieren red ni API key real).
"""
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def cfg():
    config_path = Path(__file__).resolve().parent.parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# 1. Helpers de API key y sesión
# ──────────────────────────────────────────────

class TestApiSession:
    def test_obtener_api_key_no_configurada(self, monkeypatch):
        from collector.petroleo import obtener_api_key
        monkeypatch.delenv("OILPRICEAPI_KEY", raising=False)
        assert obtener_api_key() is None

    def test_crear_session_con_api_key(self):
        from collector.petroleo import crear_session
        session = crear_session(api_key="test-key-123")
        assert session is not None
        assert "Authorization" in session.headers
        assert "Token test-key-123" in session.headers["Authorization"]


# ──────────────────────────────────────────────
# 2. Fetch de precios — mock de respuesta OilPriceAPI
# ──────────────────────────────────────────────

class TestFetchPrecioPetroleo:
    def _mock_oilpriceapi_response(self, price=71.45):
        """Crea una respuesta simulada de OilPriceAPI."""
        fecha = datetime.now().strftime("%Y-%m-%d")
        return {
            "status": "success",
            "data": {
                "code": "BRENT_CRUDE_USD",
                "price": price,
                "currency": "USD",
                "created_at": f"{fecha}T12:00:00.000Z",
                "type": "spot_price",
            }
        }

    def test_referencia_siempre_wti(self):
        """El proyecto usa solo WTI (Brent se retiró): aunque se pida otro código,
        la referencia guardada es 'wti'."""
        from collector.petroleo import fetch_precio_petroleo

        mock_resp = MagicMock()
        mock_resp.json.return_value = self._mock_oilpriceapi_response(71.45)
        mock_resp.raise_for_status = MagicMock()

        with patch("collector.petroleo.crear_session") as mock_session:
            mock_sess_obj = MagicMock()
            mock_sess_obj.get.return_value = mock_resp
            mock_session.return_value = mock_sess_obj

            result = fetch_precio_petroleo("BRENT_CRUDE_USD", api_key="test-key")

        assert result is not None
        assert "fecha" in result
        assert abs(result["usd_barril"] - 71.45) < 0.01
        assert result["referencia"] == "wti"

    def test_wti_con_api_key(self):
        from collector.petroleo import fetch_precio_petroleo

        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "status": "success",
            "data": {
                "code": "WTI_CRUDE_USD",
                "price": 68.20,
                "currency": "USD",
                "created_at": datetime.now().strftime("%Y-%m-%dT12:00:00Z"),
            }
        }
        mock_resp.raise_for_status = MagicMock()

        with patch("collector.petroleo.crear_session") as mock_session:
            mock_sess_obj = MagicMock()
            mock_sess_obj.get.return_value = mock_resp
            mock_session.return_value = mock_sess_obj

            result = fetch_precio_petroleo("WTI_CRUDE_USD", api_key="test-key")

        assert result is not None
        assert abs(result["usd_barril"] - 68.20) < 0.01
        assert result["referencia"] == "wti"

    def test_respuesta_vacia(self):
        from collector.petroleo import fetch_precio_petroleo

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "success", "data": {}}
        mock_resp.raise_for_status = MagicMock()

        with patch("collector.petroleo.crear_session") as mock_session:
            mock_sess_obj = MagicMock()
            mock_sess_obj.get.return_value = mock_resp
            mock_session.return_value = mock_sess_obj

            result = fetch_precio_petroleo("BRENT_CRUDE_USD", api_key="test-key")
            assert result is None

    def test_status_error(self):
        from collector.petroleo import fetch_precio_petroleo

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "error", "message": "bad request"}
        mock_resp.raise_for_status = MagicMock()

        with patch("collector.petroleo.crear_session") as mock_session:
            mock_sess_obj = MagicMock()
            mock_sess_obj.get.return_value = mock_resp
            mock_session.return_value = mock_sess_obj

            result = fetch_precio_petroleo("BRENT_CRUDE_USD", api_key="test-key")
            assert result is None


# ──────────────────────────────────────────────
# 3. fetch_precios_petroleo (múltiples codes)
# ──────────────────────────────────────────────

class TestFetchPreciosPetroleo:
    def test_fetch_brent_y_wti(self):
        from collector.petroleo import fetch_precios_petroleo

        fecha = datetime.now().strftime("%Y-%m-%d")

        with patch("collector.petroleo.fetch_precio_petroleo") as mock_fetch:
            def side_effect(code, api_key):
                if "BRENT" in code.upper():
                    return {"fecha": fecha, "usd_barril": 71.45, "referencia": "brent"}
                elif "WTI" in code.upper():
                    return {"fecha": fecha, "usd_barril": 68.20, "referencia": "wti"}
                return None

            mock_fetch.side_effect = side_effect
            resultados = fetch_precios_petroleo({"brent": "BRENT_CRUDE_USD", "wti": "WTI_CRUDE_USD"}, api_key="test-key")

        assert len(resultados) == 2
        refs = {r["referencia"] for r in resultados}
        assert "brent" in refs
        assert "wti" in refs


# ──────────────────────────────────────────────
# 4. Integración con DB
# ──────────────────────────────────────────────

class TestIntegracionDb:
    def test_guardar_precios_petroleo_no_falla(self, cfg):
        """Verifica que guardar_precios_petroleo no lanza excepción."""
        from collector.petroleo import guardar_precios_petroleo

        precios_simulados = [
            {"referencia": "brent", "fecha": "2026-09-21", "usd_barril": 71.45},
            {"referencia": "wti", "fecha": "2026-09-21", "usd_barril": 68.20},
        ]

        try:
            inserted = guardar_precios_petroleo(precios_simulados, cfg)
            assert isinstance(inserted, int)
        except Exception as exc:
            pytest.fail(f"guardar_precios_petroleo lanzó excepción: {exc}")


# ──────────────────────────────────────────────
# 5. Orquestador principal
# ──────────────────────────────────────────────

class TestEjecutar:
    def test_sin_api_key(self, cfg):
        from collector.petroleo import ejecutar
        with patch("collector.petroleo.obtener_api_key", return_value=None):
            resultado = ejecutar(cfg)
        assert resultado["fuente"] == "sin_api_key"

    def test_con_api_key_mock(self, cfg):
        from collector.petroleo import ejecutar

        fecha = datetime.now().strftime("%Y-%m-%d")

        with patch("collector.petroleo.obtener_api_key", return_value="test-key"):
            with patch(
                "collector.petroleo.fetch_precios_petroleo"
            ) as mock_fetch:
                mock_fetch.return_value = [
                    {"referencia": "brent", "fecha": fecha, "usd_barril": 71.45},
                ]
                with patch(
                    "collector.petroleo.guardar_precios_petroleo"
                ) as mock_save, patch(
                    "collector.petroleo.fetch_historial_wti", return_value=[]
                ):
                    mock_save.return_value = 1
                    resultado = ejecutar(cfg)

        assert resultado["fuente"] == "oilpriceapi"
        assert resultado["precios_encontrados"] == 1

    def test_historial_corrige_dias_previos(self):
        """La serie diaria pisa un valor malo de un día anterior (ej. 71.45 de un mock)."""
        from collector.db import conectar, guardar_precios, leer_ultimo
        from collector.petroleo import fetch_historial_wti, guardar_serie_petroleo

        conn = conectar()
        guardar_precios(conn, [{"producto": "wti", "fecha": "2026-09-24", "precio": 71.45}], "OilPriceAPI")
        conn.close()

        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "success", "data": {"prices": [
            {"price": 93.3, "created_at": "2026-09-24T00:00:00.000Z", "synthetic": False},
            {"price": 90.7, "created_at": "2026-09-23T00:00:00.000Z", "synthetic": False},
            {"price": 1.0, "created_at": "2026-09-22T00:00:00.000Z", "synthetic": True},
        ]}}
        with patch("collector.petroleo.crear_session") as mock_session:
            mock_session.return_value.get.return_value = mock_resp
            serie = fetch_historial_wti("test-key")

        assert serie == [{"fecha": "2026-09-23", "usd_barril": 90.7}, {"fecha": "2026-09-24", "usd_barril": 93.3}]
        guardar_serie_petroleo(serie)
        conn = conectar()
        assert leer_ultimo(conn, "wti")["precio"] == 93.3
