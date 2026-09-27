# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

La referencia técnica completa (esquema, colectores, contrato del dashboard, CI, gotchas) está en AGENTS.md y se importa aquí:

@AGENTS.md

## Reglas de trabajo (de `.opencode/skills/gasolina-gt/SKILL.md`)
- Hablar con el usuario en español; comentarios y docstrings en español.
- **Nunca inventar datos**: todo precio, tasa, fecha o noticia sale de una fuente consultada o de `config.json`. Si una fuente falla → "sin datos", nunca un valor estimado.
- **Nunca adivinar URLs ni endpoints**: si falta una fuente que no está en `config.json`, preguntar.
- Cada registro guardado lleva procedencia (`fuente`, `url` si aplica, `fetched_at` en hora de Guatemala vía `ahora_gt_iso()`).
- Ediciones pequeñas; leer antes de editar. Si un comando falla dos veces seguidas, parar y reportar el error exacto.
- Registrar decisiones y bugs relevantes en `PROGRESS.md`.

## Comandos
```powershell
python -m pytest tests/ -q -p no:cacheprovider        # suite completa (0 fallas esperadas)
python -m pytest tests/test_web.py -v                  # un archivo
python -m pytest tests/test_web.py::test_vercel_web_analytics -v   # un test
python collector/main.py --memoria --alternos --consenso --petroleo --noticias --export   # run completo como en CI
python serve.py                                        # http://localhost:8089/web/index.html
```
No hay build ni linter: el dashboard es HTML/CSS/JS vanilla.

## Flujo de datos (visión general)
1. `daily-update.yml` (Actions, windows-latest) corre `main.py` con DB SQLite efímera.
2. `--memoria` restaura la DB desde `data/db/*.csv` (en git) → los colectores escriben vía `db.guardar_precios` (prioridad de fuente: MEM > Consejo/manual > alternas) → `--export` genera `data/export/consolidado.json` y re-vuelca los CSV.
3. El workflow commitea `consolidado.json` + `data/db/*.csv` a `main`.
4. El dashboard (Vercel, `gasolinasogt.com`) lee `consolidado.json` desde `raw.githubusercontent.com`, no desde Vercel: un deploy de Vercel no cambia los datos, un push del workflow sí.

## Ojo con PROGRESS.md
Su encabezado describe las etapas originales (EIA, Brent, 6 JSONs, `precios_mem.py`); esa arquitectura fue reemplazada (OilPriceAPI solo WTI, `consolidado.json`, tabla única). Para el estado actual, confiar en AGENTS.md y el código.
