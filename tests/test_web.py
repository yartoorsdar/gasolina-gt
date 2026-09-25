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
        """Verifica que el JS cargue consolidado.json (único archivo de datos)."""
        content = html_path.read_text(encoding="utf-8")
        assert "fetch" in content, "No se encontró fetch() en el JS"
        assert "consolidado.json" in content, "No se referencia consolidado.json"
        assert "resumen.json" not in content, "resumen.json ya no se exporta"
        assert "87.45" not in content, "Volvieron los precios WTI escritos a mano"

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


def test_dashboard_no_inventa_titulos():
    """traducirTituloEnEspañol tenía frases fijas por patrón (texto inventado)."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    for archivo in ("index.html", "web/index.html"):
        html = (raiz / archivo).read_text(encoding="utf-8")
        for frase in ("Trump abordó el petróleo de Venezuela", "Corea del Sur busca reducir",
                      "paralizando parte de la capacidad de refinación rusa"):
            assert frase not in html, f"{archivo}: texto inventado '{frase}'"
        assert "if (!resultado.includes(n)) resultado.push(n);" in html, f"{archivo}: sin relleno a 5 noticias"


def test_dashboard_modalidades_y_confianza():
    """Precios por modalidad + etiqueta de confianza + fuentes del consejo."""
    from pathlib import Path
    raiz = Path(__file__).resolve().parent.parent
    for archivo in ("index.html", "web/index.html"):
        html = (raiz / archivo).read_text(encoding="utf-8")
        for pieza in ("precios_modalidades", "badgeConfianza", "fuentesConsejo",
                      "'Servicio completo'", ".confianza-alta", "escHtml("):
            assert pieza in html, f"{archivo}: falta {pieza}"
