# Gasolina GT — Agent Instructions

## Quick start
```powershell
cd C:\Users\manue\Documents\proyectos\web\Gasolina
python -m pytest tests/ -v --tb=short      # 138 passing
python collector/main.py --all --export     # run collectors + export JSONs to data/export/
python serve.py                             # dashboard at http://localhost:8089/web/index.html
```

## Architecture
- `collector/*.py` — data collectors: `db.py`, `impuestos.py`, `precios_mem.py`, `importar_historico.py`, `petroleo.py`, `noticias.py`, `main.py`, `scheduler.py`
- `web/index.html` — single-file dashboard (CSS+JS vanilla, no build step)
- `data/export/*.json` — 7 JSON files consumed by the dashboard: `resumen.json`, `consolidado.json`, `precios_combustible.json`, `petroleo.json`, `historial_precios.json`, `historial_petroleo.json`, `noticias.json`
- `data/historial.db` — SQLite DB with tables: `precios_combustible`, `precios_petroleo`, `noticias`, `ejecuciones`

## Run collectors individually
```powershell
python collector/precios_mem.py        # MEM weekly PDF → DB (AutoServicio + Servicio Completo)
python collector/importar_historico.py # XLSX daily historical → DB
python collector/petroleo.py           # EIA API Brent/WTI → DB
python collector/noticias.py           # RSS feeds → DB
```

## Windows-specific gotchas
- **Emoji in print**: `print("✅")` crashes with `UnicodeEncodeError`. Use ASCII: `print("[OK]")`, or add `sys.stdout.reconfigure(encoding='utf-8')` at module top.
- **Arrow character `→`** also fails on cp1252 console. Use `->`.
- **SQLite Row**: `conn.row_factory = sqlite3.Row` in `db.py`. Rows support `row["col"]` but NOT `.get()`. Use direct indexing: `e["mensaje"]`, not `e.get("mensaje")`.
- **Import when run as __main__**: Modules that import from other collector modules add parent to sys.path via `os.sys.path.insert(0, str(_root))` inside `if __name__ == "__main__":`.

## EIA API v2 (petroleo.py)
- Endpoint: `/v2/petroleum/pri/spt/data/`
- Must use `facets[series][]=RBRTE/RWTC` (NOT `series[]`)
- Must include `data[]=value` in params, otherwise response has NO price values
- API key in `.env` via variable name from config.json (`EIA_API_KEY`)

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
- Reads `data/export/resumen.json` and `data/export/consolidado.json` (relative to web dir: `../data/export/`)
- JSON properties: `precios_combustible`, `petroleo`, `ultimas_noticias`, `noticias_count` — NOT `data.precios`
- **Product names**: `'superior'`, `'regular'`, `'diésel'` (single s, accent on e). The dashboard uses `'diésel'` with accent. Collectors and tests use `'diessel'` without accent — a known inconsistency.
- **Sort stability**: Uses `Map` for ordering products (NOT `indexOf()` which is unstable in V8 on Windows).
- **Chart rendering**: `renderHistorial()` MUST be called AFTER `contentEl.style.display = 'block'`. While the container is hidden (`display:none`), `getBoundingClientRect()` returns 0x0 and the canvas draws at 400×250 (low quality).

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
- Tests use `conectar_temporal()` for isolated in-memory DB operations
- SQLite UNIQUE bug: inline `UNIQUE(...)` in `executescript()` doesn't work on Windows — explicit `CREATE UNIQUE INDEX` is required (handled in db.py)

## Key files to read first when debugging
1. `config.json` — central config (tax constants, regimes, source URLs, feeds, LLM placeholders)
2. `collector/db.py` — database schema and all query helpers
3. `collector/main.py` — orchestrator and JSON export logic
4. `web/index.html` — dashboard HTML/CSS/JS (single file)
5. `PROGRESS.md` — history of decisions and bugs
