"""Tests unitarios para collector/noticias.py.

Pruebas con datos simulados (no requieren red ni LLM).
"""

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
# 1. Helpers de parsing
# ──────────────────────────────────────────────

class TestHelpers:
    def test_extraer_medio_google_es(self):
        from collector.noticias import _extraer_medio
        assert "Google News ES" in _extraer_medio(
            "https://news.google.com/rss/search?q=petroleo&hl=es-419"
        )

    def test_extraer_medio_google_en(self):
        from collector.noticias import _extraer_medio
        assert "Google News EN" in _extraer_medio(
            "https://news.google.com/rss/search?q=oil&hl=en-US"
        )

    def test_extraer_medio_oilprice(self):
        from collector.noticias import _extraer_medio
        assert "OilPrice" in _extraer_medio("https://oilprice.com/rss/main")

    def test_extraer_medio_eia(self):
        from collector.noticias import _extraer_medio
        assert "EIA" in _extraer_medio("https://www.eia.gov/rss/todayinenergy.xml")

    def test_normalizar_fecha_valida(self):
        from collector.noticias import _normalizar_fecha_publicacion
        resultado = _normalizar_fecha_publicacion(
            "Tue, 22 Sep 2026 14:30:00 GMT"
        )
        assert resultado is not None
        assert "2026-09-22" in resultado

    def test_normalizar_fecha_vacia(self):
        from collector.noticias import _normalizar_fecha_publicacion
        assert _normalizar_fecha_publicacion("") is None


# ──────────────────────────────────────────────
# 2. Fetch de feeds — mock de requests
# ──────────────────────────────────────────────

class TestFetchFeedRss:
    def test_feed_con_entries(self):
        from collector.noticias import fetch_feed_rss

        # Mock de respuesta HTTP con XML RSS válido
        xml_mock = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Petroleo sube 5%</title>
      <link>https://ejemplo.com/noticia1</link>
      <pubDate>Tue, 22 Sep 2026 14:30:00 GMT</pubDate>
      <description>Resumen de la noticia sobre petroleo.</description>
    </item>
  </channel>
</rss>"""

        mock_resp = MagicMock()
        mock_resp.text = xml_mock
        mock_resp.raise_for_status = MagicMock()

        with patch("collector.noticias.requests.get", return_value=mock_resp):
            result = fetch_feed_rss("https://ejemplo.com/feed.xml")

        assert result is not None
        entries = result.get("entries", [])
        assert len(entries) == 1
        assert entries[0]["title"] == "Petroleo sube 5%"
        assert entries[0]["link"] == "https://ejemplo.com/noticia1"

    def test_feed_sin_entries(self):
        from collector.noticias import fetch_feed_rss

        xml_mock = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed Vacio</title></channel></rss>"""

        mock_resp = MagicMock()
        mock_resp.text = xml_mock
        mock_resp.raise_for_status = MagicMock()

        with patch("collector.noticias.requests.get", return_value=mock_resp):
            result = fetch_feed_rss("https://ejemplo.com/feed.xml")

        # Cuando no hay entries, la función retorna None (sin error)
        assert result is None or len(result.get("entries", [])) == 0

    def test_error_http(self):
        from collector.noticias import fetch_feed_rss

        import requests as req_mod
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = req_mod.HTTPError("404")

        with patch("collector.noticias.requests.get", return_value=mock_resp):
            result = fetch_feed_rss("https://ejemplo.com/feed.xml")

        assert result is None


# ──────────────────────────────────────────────
# 3. Clasificación con LLM (mock)
# ──────────────────────────────────────────────

class TestLlmClasificacion:
    def test_llm_disponible_sin_config(self, cfg):
        from collector.noticias import _llm_available
        # config.json tiene llm.base_url y model vacíos
        assert _llm_available(cfg) is False

    def test_clasificar_con_llm_mock(self):
        from collector.noticias import clasificar_noticia_llm

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": '{"categoria": "geopolitica", "relevancia": 5, "resumen_es": "Ataque a pipeline en Arabia afecta exportaciones"}'
                    }
                }
            ]
        }

        mock_cfg = {"llm": {"base_url": "http://localhost:1234", "model": "test-model"}}

        with patch("collector.noticias.requests.post", return_value=mock_resp):
            result = clasificar_noticia_llm(
                "Ataque a pipeline en Arabia",
                "Resumen de la noticia...",
                mock_cfg,
            )

        assert result is not None
        assert result["categoria"] == "geopolitica"
        assert result["relevancia"] == 5

    def test_clasificar_sin_llm(self):
        from collector.noticias import clasificar_noticia_llm
        result = clasificar_noticia_llm("Test", "Test", {"llm": {}})
        assert result is None


# ──────────────────────────────────────────────
# 4. Integración con DB
# ──────────────────────────────────────────────

class TestIntegracionDb:
    def test_guardar_noticias_no_falla(self, cfg):
        """Verifica que guardar_noticias no lanza excepción."""
        from collector.noticias import guardar_noticias

        items_simulados = [
            {
                "title": "Petroleo sube 5%",
                "link": "https://ejemplo.com/noticia1",
                "published": "Tue, 22 Sep 2026 14:30:00 GMT",
                "summary": "Resumen de la noticia.",
                "source_url": "https://oilprice.com/rss/main",
            },
        ]

        try:
            inserted = guardar_noticias(items_simulados, cfg)
            assert isinstance(inserted, int)
        except Exception as exc:
            pytest.fail(f"guardar_noticias lanzó excepción: {exc}")


# ──────────────────────────────────────────────
# 5. Orquestador principal
# ──────────────────────────────────────────────

class TestEjecutar:
    def test_sin_feeds(self, cfg):
        from collector.noticias import ejecutar
        cfg_vacio = {"noticias": {"feeds": []}}
        resultado = ejecutar(cfg_vacio)
        assert resultado["fuente"] == "vacio"

    def test_con_feeds_mock(self, cfg):
        from collector.noticias import ejecutar

        with patch("collector.noticias.fetch_feed_rss") as mock_fetch:
            mock_fetch.return_value = {
                "feed_url": "https://ejemplo.com/feed.xml",
                "entries": [
                    {"title": "Test", "link": "https://ejemplo.com/1"},
                ],
            }
            with patch("collector.noticias.guardar_noticias") as mock_save:
                mock_save.return_value = 1
                resultado = ejecutar(cfg)

        assert resultado["fuente"] == "rss_feeds"
        assert resultado["feeds_procesados"] >= 0
