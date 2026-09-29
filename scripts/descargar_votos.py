"""Descarga los votos ciudadanos del Worker (votos-api) para la calibración.

Baja los votos de AYER y de HOY (hora de Guatemala) a data/inbox/votos/votos_<fecha>.json:
así un run diario recoge también los votos que llegaron después del run anterior.
Reimportar es seguro (collector/calibracion.py deduplica por id_voto).

Nunca tumba el run diario: sin VOTOS_EXPORT_TOKEN o con el servidor caído avisa y sale con 0.
Los exports crudos NO se commitean (data/inbox/votos/ está en .gitignore); lo que persiste
en git es data/db/votos.csv, que exporta collector/memoria.py.

Uso:  python scripts/descargar_votos.py          (token en la variable VOTOS_EXPORT_TOKEN)
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

API_POR_DEFECTO = "https://gasolina-votos.gasolinasogt.workers.dev"
DESTINO = _RAIZ / "data" / "inbox" / "votos"


def descargar(fechas, token, api=API_POR_DEFECTO, destino=DESTINO, abrir=urllib.request.urlopen) -> dict:
    """Baja cada fecha y la guarda como JSON. Devuelve {"guardados": [...], "errores": {fecha: motivo}}."""
    res = {"guardados": [], "errores": {}}
    if not token:
        print("[votos] Sin VOTOS_EXPORT_TOKEN: se omite la descarga de votos.")
        return res
    destino = Path(destino)
    for fecha in fechas:
        req = urllib.request.Request(
            f"{api}/api/exportar?fecha={fecha}",
            headers={"Authorization": f"Bearer {token}", "User-Agent": "gasolina-gt-workflow/1.0"},
        )
        try:
            with abrir(req, timeout=30) as r:
                datos = json.loads(r.read().decode("utf-8"))
            votos = datos.get("votos", [])
            destino.mkdir(parents=True, exist_ok=True)
            (destino / f"votos_{fecha}.json").write_text(
                json.dumps({"votos": votos}, ensure_ascii=False), encoding="utf-8")
            res["guardados"].append(fecha)
            print(f"[votos] {fecha}: {len(votos)} votos descargados")
        except urllib.error.HTTPError as exc:
            res["errores"][fecha] = f"HTTP {exc.code}"
            print(f"[votos] {fecha}: el servidor respondio HTTP {exc.code}; se omite")
        except Exception as exc:  # red caida, JSON roto, etc.: nunca romper el run diario
            res["errores"][fecha] = type(exc).__name__
            print(f"[votos] {fecha}: no se pudo descargar ({type(exc).__name__}); se omite")
    return res


def main() -> int:
    from collector.db import hace_dias_gt, hoy_gt

    descargar(
        [hace_dias_gt(1), hoy_gt()],
        os.environ.get("VOTOS_EXPORT_TOKEN", "").strip(),
        os.environ.get("VOTOS_API_URL", API_POR_DEFECTO).rstrip("/"),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
