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
