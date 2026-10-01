"""Descubrimiento por feeds RSS directos de los medios (consenso_precios.descubrir_feeds)."""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from collector import consenso_precios as cp
from collector.db import _GT

AHORA = datetime.now(_GT)


def _rss(items):
    cuerpo = "".join(
        f"<item><title>{t}</title><link>{u}</link><pubDate>{format_datetime(d)}</pubDate></item>"
        for t, u, d in items)
    return f'<?xml version="1.0"?><rss version="2.0"><channel>{cuerpo}</channel></rss>'.encode("utf-8")


class _Resp:
    def __init__(self, contenido):
        self.content = contenido

    def raise_for_status(self):
        pass


def _get(contenido):
    return lambda url, headers=None, timeout=0: _Resp(contenido)


def test_solo_entran_notas_de_combustibles_dentro_de_la_ventana_mas_recientes_primero():
    feed = _rss([
        ("Precios de la gasolina hoy", "https://medio.gt/a", AHORA - timedelta(days=1)),
        ("Resultados del fútbol", "https://medio.gt/b", AHORA),
        ("Combustibles: diésel sube", "https://medio.gt/c", AHORA),
        ("Gasolina de hace un mes", "https://medio.gt/d", AHORA - timedelta(days=30)),
    ])
    notas = cp.descubrir_feeds(["https://medio.gt/feed"], dias=10, get=_get(feed))
    assert [n["url"] for n in notas] == ["https://medio.gt/c", "https://medio.gt/a"]
    assert notas[0]["medio"] == "medio.gt" and notas[0]["publicado"] == AHORA.strftime("%Y-%m-%d")


def test_la_fecha_se_toma_en_hora_de_guatemala_no_en_utc():
    # 03:00 UTC del día 30 = 21:00 del día 29 en Guatemala
    utc = datetime(2026, 9, 30, 3, 0, tzinfo=timezone.utc)
    feed = _rss([("Gasolina hoy", "https://medio.gt/x", utc)])
    notas = cp.descubrir_feeds(["f"], dias=10_000, get=_get(feed))
    assert notas[0]["publicado"] == "2026-09-29"


def test_sin_duplicados_entre_feeds_y_feed_caido_no_rompe(capsys):
    feed = _rss([("Combustibles hoy", "https://medio.gt/a", AHORA)])

    def get(url, headers=None, timeout=0):
        if "caido" in url:
            raise TimeoutError("sin red")
        return _Resp(feed)

    notas = cp.descubrir_feeds(["https://x/caido", "https://x/1", "https://x/2"], get=get)
    assert len(notas) == 1
    salida = capsys.readouterr().out
    salida.encode("cp1252")
    assert "TimeoutError" in salida


def test_sitemap_de_noticias_tema_por_url_y_filtro_de_medio_regional():
    hoy = AHORA.strftime("%Y-%m-%d")
    sitemap = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
<url><loc>https://soy502.com/articulo/asi-amanecieron-precios-combustibles-1</loc>
 <news:news><news:publication_date>{hoy}T07:20:00-06:00</news:publication_date>
 <news:title>Así amanecieron los precios este 1 de octubre</news:title></news:news></url>
<url><loc>https://soy502.com/articulo/futbol-2</loc>
 <news:news><news:publication_date>{hoy}T07:00:00-06:00</news:publication_date><news:title>Gol</news:title></news:news></url>
<url><loc>https://medio.com/honduras/combustibles-3</loc><lastmod>{hoy}</lastmod></url>
<url><loc>https://medio.com/guatemala/combustibles-4</loc><lastmod>{hoy}</lastmod></url>
</urlset>""".encode("utf-8")
    notas = cp.descubrir_feeds(["s"], get=_get(sitemap))
    assert [n["url"] for n in notas] == ["https://soy502.com/articulo/asi-amanecieron-precios-combustibles-1",
                                         "https://medio.com/honduras/combustibles-3",
                                         "https://medio.com/guatemala/combustibles-4"]
    notas = cp.descubrir_feeds([{"url": "s", "filtro_url": "/guatemala/"}], get=_get(sitemap))
    assert [n["url"] for n in notas] == ["https://medio.com/guatemala/combustibles-4"]


def test_feed_con_xml_imperfecto_no_se_pierde():
    """La Hora: espacios antes de <?xml; CRN: caracteres inválidos → lectura por patrón."""
    cuerpo = _rss([("Gasolina amanece más barata", "https://lahora.gt/a", AHORA)])
    assert len(cp.descubrir_feeds(["f"], get=_get(b"\n\n  " + cuerpo))) == 1
    roto = cuerpo.replace(b"<channel>", b"<channel>\x01&nbsp;")
    assert [n["url"] for n in cp.descubrir_feeds(["f"], get=_get(roto))] == ["https://lahora.gt/a"]


class TestHayQueLeer:
    HOY = "2026-10-01"
    NOTA = {"url": "u", "publicado": "2026-10-01", "titulo": "t"}

    def _previo(self, **k):
        return {"publicado": "2026-10-01", "titulo": "t", "extractor": cp.EXTRACTOR_LLM, "n_obs": 3,
                "error": None, **k}

    def test_motivos(self):
        f = lambda previo, nota=self.NOTA: cp._hay_que_leer(previo, nota, cp.EXTRACTOR_LLM, self.HOY)
        assert f(None) == "nueva"
        assert f(self._previo()) is None
        # Publinews reescribe la nota del 28-sep con los precios del día: misma URL, fecha nueva
        assert f(self._previo(publicado="2026-09-28")) == "actualizada"
        assert f(self._previo(error="timeout", n_obs=0)) == "reintento"
        assert f(self._previo(n_obs=0, extractor=cp.SIN_PRECIOS)) == "revisar"
        assert f(self._previo(n_obs=0, extractor="llm")) == "extractor nuevo"
        assert f(self._previo(n_obs=0)) is None
        vieja = {**self.NOTA, "publicado": "2026-09-25"}
        assert f(self._previo(publicado="2026-09-25", n_obs=0, extractor=cp.SIN_PRECIOS), vieja) is None


def test_texto_de_html_conserva_listas_cortas_y_filas_de_tabla():
    html = """<html><body><nav><p>Menú con un enlace largo de navegación del sitio web</p></nav>
    <article><h1>Tras exoneración, gasolineras aplican cambios</h1>
    <p>Los precios reportados este jueves 1 de octubre son los siguientes:</p>
    <h2>Autoservicio</h2><ul><li><b>Súper: Q36.19</b></li><li><b>Regular: Q34.89</b></li></ul>
    <table><tr><th>Producto</th><th>Antes</th><th>Ahora</th></tr>
    <tr><td>Superior</td><td>Q45.29</td><td>Q36.24</td></tr></table>
    <p>Texto suficientemente largo para que este contenedor sea el cuerpo de la nota, con más de
    doscientos caracteres de párrafos, de modo que el selector lo elija por encima de la página
    completa y deje fuera la navegación y lo que no es la nota.</p></article>
    <aside><h3>Notas relacionadas: otra cosa</h3></aside></body></html>"""
    lineas = cp.texto_de_html(html).split("\n")
    assert lineas[0] == "Tras exoneración, gasolineras aplican cambios"
    assert "Autoservicio" in lineas and "Súper: Q36.19" in lineas and "Regular: Q34.89" in lineas
    assert "Superior | Q45.29 | Q36.24" in lineas
    assert not any("Menú" in l or "relacionadas" in l for l in lineas)


def test_ejecutar_relee_la_nota_que_el_medio_actualizo_con_la_misma_url(monkeypatch):
    """Raíz del bug del 1-oct-2026: la nota de Publinews del 28-sep traía ya los
    precios del 1-oct, pero como su URL "ya estaba leída" el consejo la ignoraba."""
    from collector.db import conectar, registrar_articulo
    url = "https://www.publinews.gt/noticias/2026/09/28/asi-amanecieron-los-precios/"
    conn = conectar()
    registrar_articulo(conn, url, "publinews.gt", "Así amanecieron (28-sep)", "2026-09-28", "llm", 6, None)
    conn.close()
    hoy = AHORA.strftime("%Y-%m-%d")
    nota = {"url": url, "medio": "publinews.gt", "titulo": "Tras exoneración, gasolineras aplican cambios",
            "publicado": hoy}
    texto = "Tras exoneración\nAutoservicio\nSúper: Q36.19\nRegular: Q34.89\nDiésel: Q41.69"
    monkeypatch.setattr(cp, "descubrir_articulos", lambda *a, **k: [])
    monkeypatch.setattr(cp, "descubrir_feeds", lambda feeds, dias=0: [nota])
    monkeypatch.setattr(cp, "texto_articulo", lambda u: texto)
    monkeypatch.setattr(cp, "observaciones_gpp", lambda: [])
    from collector import noticias
    monkeypatch.setattr(noticias, "_llm_available", lambda cfg: False)
    res = cp.ejecutar(cfg={"consejo": {"feeds_directos": ["f"]}})
    assert res["notas_leidas"] == 1 and res["observaciones_nuevas"] == 3


def test_ejecutar_no_gasta_llm_en_notas_sin_cifras(monkeypatch):
    nota = {"url": "https://medio.gt/sat", "medio": "medio.gt", "titulo": "SAT explica la factura del combustible",
            "publicado": AHORA.strftime("%Y-%m-%d")}
    llamadas = []
    monkeypatch.setattr(cp, "descubrir_articulos", lambda *a, **k: [])
    monkeypatch.setattr(cp, "descubrir_feeds", lambda feeds, dias=0: [nota])
    monkeypatch.setattr(cp, "texto_articulo", lambda u: "La factura mostrará el IVA en cero.")
    monkeypatch.setattr(cp, "observaciones_gpp", lambda: [])
    monkeypatch.setattr(cp, "extraer_llm", lambda *a, **k: llamadas.append(1) or [])
    from collector import noticias
    monkeypatch.setattr(noticias, "_llm_available", lambda cfg: True)
    res = cp.ejecutar(cfg={"consejo": {"feeds_directos": ["f"]}})
    assert res["notas_leidas"] == 1 and llamadas == []


def test_ejecutar_lee_el_feed_directo_y_guarda_observaciones(monkeypatch):
    nota = {"url": "https://medio.gt/precios-hoy", "medio": "medio.gt", "titulo": "Precios de combustibles hoy",
            "publicado": AHORA.strftime("%Y-%m-%d")}
    texto = ("De acuerdo con los precios observados, en la modalidad de autoservicio, el galón de "
             "gasolina regular se cotiza en Q43.29, mientras que la gasolina súper alcanza los Q45.29.")
    monkeypatch.setattr(cp, "descubrir_articulos", lambda *a, **k: [])       # Bing no la encuentra
    monkeypatch.setattr(cp, "descubrir_feeds", lambda feeds, dias=0: [nota] if feeds else [])
    monkeypatch.setattr(cp, "texto_articulo", lambda url: texto)
    monkeypatch.setattr(cp, "observaciones_gpp", lambda: [])
    from collector import noticias
    monkeypatch.setattr(noticias, "_llm_available", lambda cfg: False)
    res = cp.ejecutar(cfg={"consejo": {"feeds_directos": ["https://medio.gt/feed"]}})
    assert res["notas_descubiertas"] == 1 and res["notas_leidas"] == 1
    assert res["observaciones_nuevas"] == 2
