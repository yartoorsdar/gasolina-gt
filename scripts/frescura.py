"""Decide si un run PROGRAMADO de GitHub debe saltarse porque ya hay datos frescos.

Contexto: los runs puntuales (04:52 y 11:52 hora de Guatemala) los dispara Cloudflare con
`workflow_dispatch`. El cron de GitHub queda solo como red de seguridad por si Cloudflare falla,
y GitHub lo retrasa 4-6 horas, así que suele caer 2-5 h después del run de las 11:52: en ese caso
se salta para no gastar llamadas de OilPriceAPI ni tokens del LLM.

Uso (en el workflow):  python scripts/frescura.py "<github.event_name>" >> "$GITHUB_OUTPUT"
Imprime SOLO una línea:  saltar=true | saltar=false
Cualquier duda o error = saltar=false (mejor un run de más que uno de menos).
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

CONSOLIDADO = Path(__file__).resolve().parent.parent / "data" / "export" / "consolidado.json"
HORAS_FRESCO = 6   # > retraso máximo del cron de GitHub tras el run de las 11:52 y < 7 h entre runs


def edad_horas(ruta: Path = CONSOLIDADO, ahora: datetime | None = None) -> float | None:
    """Horas desde `actualizado_at` del consolidado; None si no se puede saber."""
    try:
        actualizado = datetime.fromisoformat(json.loads(Path(ruta).read_text(encoding="utf-8"))["actualizado_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if actualizado.tzinfo is None:
        return None
    ahora = ahora or datetime.now(timezone.utc)
    return (ahora - actualizado).total_seconds() / 3600


def decidir(evento: str, ruta: Path = CONSOLIDADO, ahora: datetime | None = None, horas: float = HORAS_FRESCO) -> bool:
    """True = saltar el run. Solo el evento `schedule` puede saltarse; todo lo demás corre siempre."""
    if evento != "schedule":
        return False
    edad = edad_horas(ruta, ahora)
    return edad is not None and 0 <= edad < horas


def main() -> int:
    evento = sys.argv[1] if len(sys.argv) > 1 else ""
    print(f"saltar={'true' if decidir(evento, CONSOLIDADO) else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
