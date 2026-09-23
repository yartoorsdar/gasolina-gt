# Gasolina GT — Agent Instructions

## Quick start
```powershell
cd C:\Users\manue\Documents\proyectos\web\Gasolina
python -m pytest tests/ -v --tb=short      # 138 passing
python collector/main.py --alternos --petroleo --noticias --export   # run collectors + export JSONs to data/export/
python serve.py                             # dashboard at http://localhost:8089/web/index.html
```

## Architecture
- `collector/*.py` — data collectors: `db.py`, `impuestos.py`, `precios_mem.py`, `importar_historico.py`, `petroleo.py`, `noticias.py`, `main.py`, `scheduler.py`
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

## Tax formula
- `IVA = max(0, (precioFinal - IDP) * 12 / 112)` — IVA included in final price
- `baseSinImpuestos = precioFinal - IVA - IDP` — base pure price without any taxes
- IDP per gallon: superior=Q4.70, regular=Q4.60, diésel=Q1.30 (Decreto 38-92)

## Collector gotchas
- **SQLite Row**: `conn.row_factory = sqlite3.Row`. Rows support `row["col"]` but NOT `.get()`. Use direct indexing: `e["mensaje"]`, not `e.get("mensaje")`.
- **Import when run as __main__**: Modules that import from other collector modules add parent to sys.path via `os.sys.path.insert(0, str(_root))` inside `if __name__ == "__main__":`.
- **Delete-before-insert**: Each collector deletes today's prices by source before inserting new ones (idempotent).
- **Commit before close**: `_ejecutar_modulo` in main.py calls `conn.commit()` before `close()` to flush to disk.

## EIA API v2 (petroleo.py)
- Endpoint: `/v2/petroleum/pri/spt/data/`
- Must use `facets[series][]=DCOILWTICO` (NOT `series[]`)
- Must include `data[]=value` in params, otherwise response has NO price values
- API key from `.env` via variable name in config.json (`EIA_API_KEY`)

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
- **Product names**: `'superior'`, `'regular'`, `'diésel'` (single s, accent on e). Dashboard normalizes `'diessel'` → `'diésel'`. Collectors/tests use `'diessel'` without accent — known inconsistency.
- **Sort order**: R, S, D via `Map` (NOT `indexOf()` which is unstable in V8 on Windows).
- **Chart rendering**: `renderHistorial()` MUST be called AFTER `contentEl.style.display = 'block'`. While container is hidden (`display:none`), `getBoundingClientRect()` returns 0×0 and canvas draws at wrong size.
- **Date display**: Takes max `fecha` across all products, NOT `precios[0].fecha` (which could be any product depending on sort order).
- **Time extraction**: Parses HH:MM from `fetched_at` ISO string directly (`slice(11,13)`), subtracts 6 for UTC→Guatemala conversion. Do NOT use `new Date()` parsing or timezone functions — the runner clock offset is unreliable.

## Vercel deployment
- `vercel.json`: static build with cache headers for JSON files (max-age=60) and HTML (max-age=300).
- `_redirects`: `/* /web/index.html 200` — routes all paths to dashboard.
- **Two index.html**: `web/index.html` (source of truth), `index.html` at root (copy for Vercel `/`). Always keep them in sync.

## GitHub Actions workflow (`.github/workflows/daily-update.yml`)
- Triggers: schedule cron `0 14 * * *` (14:00 UTC = 08:00 GT), push to main, manual dispatch.
- Runner: `windows-latest`, Python 3.11.
- Runs: `python collector/main.py --alternos --petroleo --noticias --export`.
- Push step uses `git pull --rebase origin main || true` before commit+push (avoids race condition with push trigger).
- Uses `[skip ci]` in commit message to prevent recursive runs.

## Scheduler (`collector/scheduler.py`)
```powershell
python scheduler.py --run-all                  # one-shot execution + export
python scheduler.py --schedule 3600            # repeat every N seconds (foreground)
python scheduler.py --export-task "name"       # generate Windows Task Scheduler XML
python scheduler.py --install-task             # install directly in Windows Task Scheduler
```

## Testing
- All tests: `pytest tests/ -v`
- Single test file: `pytest tests/test_module.py -v`
- Tests use `conectar_temporal()` for isolated in-memory DB operations.
- SQLite UNIQUE bug: inline `UNIQUE(...)` in `executescript()` doesn't work on Windows — explicit `CREATE UNIQUE INDEX` required (handled in db.py).

## Key files to read first when debugging
1. `config.json` — central config (tax constants, regimes, source URLs, feeds)
2. `collector/db.py` — database schema and all query helpers
3. `collector/main.py` — orchestrator and JSON export logic
4. `web/index.html` — dashboard HTML/CSS/JS (single file)
5. `PROGRESS.md` — history of decisions and bugs
