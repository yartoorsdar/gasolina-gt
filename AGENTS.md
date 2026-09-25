# Gasolina GT — Agent Instructions

## Quick start
```powershell
cd C:\Users\manue\Documents\proyectos\web\Gasolina
python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_db.py   # suite principal (test_db.py pendiente de migrar al esquema tabla-única)
python collector/main.py --alternos --petroleo --noticias --export   # run collectors + export JSONs to data/export/
python serve.py                             # dashboard at http://localhost:8089/web/index.html
```

## Architecture
- `collector/*.py` — data collectors: `db.py` (schema + `ahora_gt_iso()`/`hoy_gt()` helpers), `impuestos.py`, `precios_mem.py`, `mem_html.py`, `fuentes_alternas.py`, `consenso_precios.py`, `importar_historico.py`, `petroleo.py`, `noticias.py`, `main.py`, `scheduler.py`
- `web/index.html` — single-file dashboard (CSS+JS vanilla, no build step)
- `index.html` — copy of web/index.html at repo root (Vercel serves this at `/`)
- `data/export/*.json` — 7 JSON files consumed by dashboard: `resumen.json`, `consolidado.json`, `precios_combustible.json`, `petroleo.json`, `historial_precios.json`, `historial_petroleo.json`, `noticias.json`
- `data/historial.db` — SQLite DB with tables: `precios` (single table for ALL prices), `noticias`, `ejecuciones`

## DB schema (single-table)
```sql
CREATE TABLE precios (id, fecha TEXT, producto TEXT, precio REAL, fuente TEXT, fetched_at TEXT);
-- UNIQUE(fecha, producto) via CREATE UNIQUE INDEX (inline UNIQUE broken on Windows/SQLite)
-- Products: 'superior', 'regular', 'diésel' (combustible), 'wti' (petróleo)
```

## Timezone (Guatemala = UTC-6, sin DST)
- **Backend**: NUNCA `datetime.now().strftime("...-06:00")` ingenuo — en el runner GitHub (reloj UTC) queda 6h adelantado. Usar `datetime.now(timezone(timedelta(hours=-6)))` o los helpers `ahora_gt_iso()` / `hoy_gt()` de `collector/db.py`.
- **Noticias**: `parsedate_to_datetime()` devuelve aware (GMT) — convertir con `.astimezone(_GT)` antes de etiquetar `-06:00`, no solo reformatear.

## Tax formula
- `IVA = max(0, (precioFinal - IDP) * 12 / 112)` — IVA included in final price
- `baseSinImpuestos = precioFinal - IVA - IDP` — base pure price without any taxes
- IDP per gallon: superior=Q4.70, regular=Q4.60, diésel=Q1.30 (Decreto 38-92)

## Collector gotchas
- **SQLite Row**: `conn.row_factory = sqlite3.Row`. Rows support `row["col"]` but NOT `.get()`. Use direct indexing: `e["mensaje"]`, not `e.get("mensaje")`.
- **Import when run as __main__**: Modules that import from other collector modules add parent to sys.path via `os.sys.path.insert(0, str(_root))` inside `if __name__ == "__main__":`.
- **Delete-before-insert**: Each collector deletes today's prices by source before inserting new ones (idempotent).
- **Commit before close**: `_ejecutar_modulo` in main.py calls `conn.commit()` before `close()` to flush to disk.

## Petróleo (OilPriceAPI — solo WTI)
- Endpoint: `https://api.oilpriceapi.com/v1/prices/latest?by_code=WTI_CRUDE_USD`
- Key vía `OILPRICEAPI_KEY` (secreto GitHub + `.env`, ver `.env.example`)
- La doc vieja de "EIA API v2 / DCOILWTICO / EIA_API_KEY" ya no aplica al código actual

## LLM noticias (Groq, OpenAI-compatible)
- `config.json → llm`: `base_url https://api.groq.com/openai`, `model llama-3.3-70b-versatile`
- Key: secreto `GROK_API_KEY` (fallback `GEMINI_API_KEY`, luego `llm.api_key`) vía `collector/noticias.py:_obtener_api_key`
- Auto-detecta proveedor por `base_url` (Gemini nativo vs OpenAI-compatible con Bearer)
- Lote de 15 (round-robin por feed) + fallback 3×10s; `_sanear_error` quita `key=***` del diagnóstico público

## PDF parser (precios_mem.py)
- Uses `pdfplumber.extract_words()` + Y-position grouping (NOT `extract_text()`)
- Extracts AutoServicio and Servicio Completo for superior/regular/diésel
- Takes penultimate price per line (`precios[-2]`) because last value is the difference
- MEM page returns 403 Cloudflare — Plan B processes PDFs from `data/inbox/`

## XLSX parser (importar_historico.py)
- Fixed column indices: A=FECHA, C=Superior, D=Regular, E=Diésel
- Data rows start at row 8 until metadata/source lines are detected
- Idempotent via insert-or-ignore in DB

## Dashboard data contract (`web/index.html`)
- **Reads from GitHub raw** (not local files): `https://raw.githubusercontent.com/yartoorsdar/gasolina-gt/main/data/export/resumen.json`
- JSON properties: `precios_combustible`, `petroleo`, `ultimas_noticias`, `noticias_count`, `actualizado_at` — NOT `data.precios`
- `noticias_llm: {titulos_es_hoy, total_hoy}` — diagnóstico del pipeline LLM (ground truth desde DB). Si `titulos_es_hoy` es 0 tras un run, leer el log del job en Actions (`[noticias] Lote OK/Fallback OK/Error LLM`).
- `ultimas_noticias[]` trae `titulo_es`/`categoria`/`relevancia` (1-5, impacto GT)/`resumen_es` del LLM Gemini cuando hay `GEMINI_API_KEY`; si no, `relevancia` es null y el dashboard ordena por fecha. Top 5 = `relevancia` desc, luego fecha desc (`agruparNoticias`).
- Semáforo (`analizarImpactoPetrolero`): el mensaje siempre cierra con `Motivo: <noticia de mayor peso>` (fundamento en pocas palabras).
- **Product names**: canónicos `'superior'`, `'regular'`, `'diésel'`, `'wti'`. `db.canon_producto()` normaliza al insertar (`diessel`/`diesel`→`diésel`, `super`→`superior`) — el Diésel ya no desaparece del export. Dashboard también normaliza por si acaso.
- **Sort order**: R, S, D via `Map` (NOT `indexOf()` which is unstable in V8 on Windows).
- **Chart rendering**: `renderHistorial()` MUST be called AFTER `contentEl.style.display = 'block'`. While container is hidden (`display:none`), `getBoundingClientRect()` returns 0×0 and canvas draws at wrong size.
- **Date display**: Takes max `fecha` across all products, NOT `precios[0].fecha` (which could be any product depending on sort order).
- **Time extraction**: si `fetched_at` trae offset local (`-06:00`) se muestra tal cual (`slice(11,16)`); solo se restan 6h si viene en UTC puro (`Z` o `+00:00`). El backend ahora emite GT tz-aware, así que el caso normal es mostrar directo. Do NOT use `new Date()` parsing — the runner clock offset is unreliable.
- **Anti-flicker móvil**: cero animaciones `infinite` en el `<style>` inline (precios y borde del barril son estáticos; solo queda el `prefers-reduced-motion` guard). En `style-glass.css` el fondo mesh y `border-shimmer` se apagan con `@media (max-width:768px),(pointer:coarse)`. Resize con debounce 250ms que redibuja charts en estado final — NUNCA reiniciar `start*Animation` en resize (en móvil cada scroll = resize por la barra del navegador). Un solo listener global, no uno por render.

## Vercel deployment
- `vercel.json`: static build with cache headers for JSON files (max-age=60) and HTML (max-age=300). OJO: esos headers casi no aplican — el dashboard lee de `raw.githubusercontent.com` (`GITHUB_RAW` en el JS), no de Vercel.
- `_redirects` (`/* /web/index.html 200`) es sintaxis Netlify — Vercel lo ignora. `_routes.json` (sintaxis Azure SWA) también es muerto en Vercel.
- **Two index.html**: `web/index.html` (source of truth), `index.html` at root (copy for Vercel `/`). Always keep them in sync. La copia raíz usa `sprites/barrel-oil.png` (relativa a raíz); la de `web/` usa `../sprites/`.

## GitHub Actions workflows (`.github/workflows/`)
- **daily-update.yml**: cron `0 14 * * *` (14:00 UTC = 08:00 GT), push a main, manual dispatch. Runner `windows-latest`, Python 3.11. Runs `python collector/main.py --alternos --petroleo --noticias --export`. Push con `git pull --rebase origin main || true` + `[skip ci]` (evita loops y races con el trigger de push).
- ~~weekly-pdfs.yml~~ eliminado (2026-09-25): corría pipeline parcial y con DB efímera sobrescribía el dashboard con datos viejos. Solo queda `daily-update.yml`.
- `resumen.json` trae `precios_actualizados` (bool) + `max_fecha_precios`: guardia de frescura (stale si max fecha > 30 días). Si es false tras un run, revisar colectores.

## Scheduler (`collector/scheduler.py`)
```powershell
python scheduler.py --run-all                  # one-shot execution + export
python scheduler.py --schedule 3600            # repeat every N seconds (foreground)
python scheduler.py --export-task "name"       # generate Windows Task Scheduler XML
python scheduler.py --install-task             # install directly in Windows Task Scheduler
```

## Testing
- Suite principal: `pytest tests/ -q -p no:cacheprovider --ignore=tests/test_db.py`
- Single test file: `pytest tests/test_module.py -v`
- Tests use `conectar_temporal()` for isolated in-memory DB operations.
- `tests/test_db.py`, partes de `test_main.py`/`test_consenso.py`/`test_petroleo.py` aún referencian el esquema viejo (dos tablas `precios_combustible`/`precios_petroleo`, `fecha_observacion`, producto `brent`) — pendientes de migrar al esquema tabla-única. No reescribir asserts existentes sin migrar el setup.
- SQLite UNIQUE bug: inline `UNIQUE(...)` in `executescript()` doesn't work on Windows — explicit `CREATE UNIQUE INDEX` required (handled in db.py).

## Key files to read first when debugging
1. `config.json` — central config (tax constants, regimes, source URLs, feeds)
2. `collector/db.py` — database schema and all query helpers
3. `collector/main.py` — orchestrator and JSON export logic
4. `web/index.html` — dashboard HTML/CSS/JS (single file)
5. `PROGRESS.md` — history of decisions and bugs
