# ⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️
######## INICIA SCRIPT DE VALIDAR FEEDS ########
"""Valida los feeds RSS de config.json -> noticias.feeds.

Revisa por feed: HTTP 200, que sea RSS/Atom real, que tenga entries y que la
noticia más reciente no sea más vieja que MAX_DIAS. Usa el mismo User-Agent
que collector/noticias.py para que el resultado refleje lo que verá el colector.

Uso:
    python scripts/validar_feeds.py            # solo avisa (exit 0)
    python scripts/validar_feeds.py --estricto # exit 1 si algún feed falla
"""

import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import feedparser
import requests

ROOT = Path(__file__).resolve().parent.parent
TIMEOUT = 25
MAX_DIAS = 7

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
}


def nombre_corto(url: str) -> str:
    p = urlparse(url)
    if "news.google.com" in p.netloc:
        q = parse_qs(p.query).get("q", [""])[0]
        return f"Google News: {q[:70]}"
    return p.netloc.replace("www.", "") + p.path


def probar(url: str) -> dict:
    r = {"url": url, "nombre": nombre_corto(url), "ok": False, "http": None,
         "entries": 0, "ultima": None, "ms": 0, "error": None}
    t0 = time.time()
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        r["http"] = resp.status_code
        r["ms"] = int((time.time() - t0) * 1000)
        resp.raise_for_status()

        feed = feedparser.parse(resp.content)
        if feed.get("bozo") and not feed.get("entries"):
            raise ValueError(f"No es RSS/Atom válido ({type(feed.get('bozo_exception')).__name__})")
        r["entries"] = len(feed.entries)
        if r["entries"] == 0:
            raise ValueError("Feed sin entries")

        fechas = []
        for e in feed.entries:
            st = e.get("published_parsed") or e.get("updated_parsed")
            if st:
                fechas.append(datetime(*st[:6], tzinfo=timezone.utc))
        if fechas:
            ultima = max(fechas)
            r["ultima"] = ultima.isoformat()
            dias = (datetime.now(timezone.utc) - ultima).total_seconds() / 86400
            if dias > MAX_DIAS:
                raise ValueError(f"Última noticia hace {dias:.1f} días")
        r["ok"] = True
    except requests.Timeout:
        r["error"] = f"Timeout ({TIMEOUT}s)"
    except Exception as exc:
        r["error"] = str(exc)[:200]
    r["ms"] = r["ms"] or int((time.time() - t0) * 1000)
    return r


def main() -> int:
    estricto = "--estricto" in sys.argv
    cfg = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    feeds = cfg.get("noticias", {}).get("feeds", [])
    if not feeds:
        print("::error::config.json no tiene noticias.feeds")
        return 1

    with ThreadPoolExecutor(max_workers=8) as ex:
        resultados = list(ex.map(probar, feeds))

    for r in resultados:
        estado = "OK  " if r["ok"] else "FALLA"
        print(f"[{estado}] {r['nombre']} | HTTP {r['http']} | entries {r['entries']} "
              f"| ultima {r['ultima'] or '-'} | {r['ms']}ms" + (f" | {r['error']}" if r["error"] else ""))
        if not r["ok"]:
            print(f"::warning title=Feed RSS con problemas::{r['nombre']} -> {r['error']}")

    resumen = os.environ.get("GITHUB_STEP_SUMMARY")
    if resumen:
        md = ["## Estado de feeds RSS", "", "| | Feed | HTTP | Entries | Última noticia (UTC) | Error |",
              "|---|---|---|---|---|---|"]
        for r in resultados:
            md.append(f"| {'✅' if r['ok'] else '❌'} | [{r['nombre']}]({r['url']}) | {r['http'] or '-'} "
                      f"| {r['entries']} | {r['ultima'] or '-'} | {r['error'] or ''} |")
        with open(resumen, "a", encoding="utf-8") as f:
            f.write("\n".join(md) + "\n")

    logs = ROOT / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "feeds_estado.json").write_text(
        json.dumps({"revisado": datetime.now(timezone.utc).isoformat(), "resultados": resultados},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fallidos = [r for r in resultados if not r["ok"]]
    print(f"\n{len(resultados) - len(fallidos)}/{len(resultados)} feeds OK")
    return 1 if (estricto and fallidos) else 0


if __name__ == "__main__":
    sys.exit(main())
######## FINALIZA SCRIPT DE VALIDAR FEEDS ########
# ⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️⚙️
