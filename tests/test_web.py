"""Tests básicos para web/index.html — Etapa 8."""

import pytest
from pathlib import Path


@pytest.fixture()
def html_path():
    return Path(__file__).resolve().parent.parent / "web" / "index.html"


class TestDashboardHTML:
    def test_html_existe(self, html_path):
        assert html_path.exists(), "web/index.html no existe"

    def test_html_no_vacio(self, html_path):
        content = html_path.read_text(encoding="utf-8")
        assert len(content) > 1000, "index.html está vacío o muy pequeño"

    def test_contiene_secciones_esenciales(self, html_path):
        """Verifica que el HTML tenga todas las secciones del dashboard."""
        content = html_path.read_text(encoding="utf-8")
        secciones = [
            "Precios Combustible",
            "Precio del Petróleo",
            "Historial de Precios Anual",
            "Noticias Recientes",
        ]
        for sec in secciones:
            assert sec.lower() in content.lower(), f"Falta sección: {sec}"

    def test_contiene_fetch_json(self, html_path):
        """Verifica que el JS use fetch para cargar resumen.json."""
        content = html_path.read_text(encoding="utf-8")
        assert "fetch" in content, "No se encontró fetch() en el JS"
        assert "resumen.json" in content, "No se referencia resumen.json"

    def test_contiene_css_embebido(self, html_path):
        """Verifica que el CSS esté embebido (sin dependencias externas)."""
        content = html_path.read_text(encoding="utf-8")
        assert "<style>" in content or 'type="text/css"' in content

    def test_no_dependencias_js_externas(self, html_path):
        """Verifica que no dependa de CDNs externos (funciona offline)."""
        content = html_path.read_text(encoding="utf-8")
        assert "cdn." not in content.lower(), "Depende de CDN externo"
        assert "googleapis.com" not in content, "Depende de Google Fonts"

    def test_contiene_meta_viewport(self, html_path):
        """Verifica responsive design."""
        content = html_path.read_text(encoding="utf-8")
        assert "viewport" in content.lower(), "Falta meta viewport (no es responsive)"
