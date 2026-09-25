# Gasolina GT — Agent Instructions

## Quick start
```powershell
cd C:\Users\manue\Documents\proyectos\web\Gasolina
python -m pytest tests/ -q -p no:cacheprovider --ignore=tests/test_db.py   # suite principal (test_db.py pendiente de migrar al esquema tabla-única)
python collector/main.py --alternos --petroleo --noticias --export   # run collectors + export JSONs to data/export/
python serve.py                             # dashboard at http://localhost:8089/web/index.html
```

## Architecture
- `collector/*.py` — `db.py` (schema + `ahora_gt_iso()`/`hoy_gt()` + `canon_producto()`), `impuestos.py`, `mem_html.py`, `fuentes_alternas.py`, `consenso_precios.py`, `importar_historico.py`, `petroleo.py`, `noticias.py`, `main.py`, `scheduler.py` (`precios_mem.py` eliminado)
- `web/index.html` — single-file dashboard (CSS+JS vanilla, no build step)
- `index.html` — copy of web/index.html at repo root (Vercel serves this at `/`)
- `data/export/*.json` — `main.py:exportar_json` genera 6 (NO genera `consolidado.json`; el archivo en repo está congelado): `resumen.json`, `precios_combustible.json`, `petroleo.json`, `historial_precios.json`, `historial_petroleo.json`, `noticias.json`
- `data/historial.db` — SQLite, NOT in git (ephemeral in CI): `precios`, `noticias`, `ejecuciones`

## DB schema (single-table)
```sql
CREATE TABLE precios (id, fecha TEXT, producto TEXT, precio REAL, fuente TEXT, fetched_at TEXT);
-- UNIQUE(fecha, producto) via CREATE UNIQUE INDEX (inline UNIQUE broken on Windows/SQLite)
-- Products: 'superior', 'regular', 'diésel' (combustible), 'wti' (petróleo)
```

## Timezone (Guatemala = UTC-6, sin DST)
- **Backend**: NUNCA `datetime.now().strftime("...-06:00")` ingenuo — en el runner GitHub (reloj UTC) queda 6h adelantado. Usar `ahora_gt_iso()` / `hoy_gt()` de `collector/db.py`. Sin red (worldtimeapi fallaba en CI).
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
- OJO: `config.json → petroleo.series.wti = "DCOILWTICO"` es resto de la era EIA, nadie lo lee — no revivir EIA

## LLM noticias (Groq primario desde 2026-09-25; Gemini en pausa por 402)
- `config.json → llm`: `base_url https://api.groq.com/openai`, `model openai/gpt-oss-120b` — OpenAI-compatible vía `_llm_post` (json_object primero, plano si el modelo no lo soporta; POST con header `Authorization: Bearer` OBLIGATORIO sin él da 401 aunque el ping GET /v1/models haya pasado).
- Key por proveedor (`_obtener_api_key(llm_cfg, base_url)`): Gemini nativo → `GEMINI_API_KEY`; OpenAI-compatible → `GROK_API_KEY` (las keys de Gemini NO sirven en ese esquema; si ambas secrets existen y no se discrimina, el POST usa la equivocada). Fallback siempre a `llm.api_key`.
- Verificado desde runner windows-latest (workflow test-apis, 2026-09-25): Groq `/v1/models` → 200 con 11 modelos (gpt-oss-120b/20b, qwen3.8-27b…). El "Groq 401 desde IPs de Actions" era de la key vieja; esta sí responde.
- Gemini `gemini-3.6-flash` da `402 prepayment credits depleted` (free tier agotada) → en pausa hasta renovar key/billing en AI Studio o resetear cuota. Si recupera, revertir config.json a generativelanguage.
- Fallback MyMemory sin key (`_traducir_fallback`, 2 pasadas): 1) titulares ya-español etiquetados GRATIS (titulo_es + categoria + `_relevancia_keywords` — SIEMPRE asignar esas keys o las filas quedan fuera del top-10); 2) EN vía MyMemory hasta agotar cuota (~5000 chars/día). El lote de traducción usa `_seleccion_rotativa(pendientes, por_feed=3, max_total=15)` — NUNCA `pendientes[:15]` (el primer feed ES se comía todo el lote y los EN nunca entraban).
- Pipeline IA en `ejecutar()` (orden fijo): 1) VERIFICAR — `_limpiar_texto` limpia HTML/entidades del summary al recibir (raíz del bug Google News que filtraba `<a href>` a resumen_es) + `_item_valido` descarta títulos rotos; 2) ORDENAR — `_ordenar_candidatos` por relevancia determinística (`_relevancia_keywords`, sin costo LLM); 3) TRADUCIR AL FINAL — lote LLM del top-15 rankeado; cada salida pasa por `_validar_cls` (título+descripción presentes, legibles y sin URL/HTML/caracteres ininteligibles — si falla se pasa a la siguiente). Meta: `NOTICIAS_EXCELENTES = 5` fichas sin fallas; si el LLM no cubre 5, `_traducir_fallback(objetivo=...)` completa SOLO lo faltante (2 pasadas: ES gratis primero, EN vía MyMemory), y los items rechazados NO reciben categoria/relevancia (no entran al top-10 con nulls).

## XLSX parser (importar_historico.py)
- Fixed column indices: A=FECHA, C=Superior, D=Regular, E=Diésel
- Data rows start at row 8 until metadata/source lines are detected
- Idempotent via insert-or-ignore in DB

## Dashboard data contract (`web/index.html`)
- **Reads from GitHub raw** (not local files): `https://raw.githubusercontent.com/yartoorsdar/gasolina-gt/main/data/export/resumen.json`
- JSON properties: `precios_combustible`, `petroleo`, `ultimas_noticias`, `noticias_count`, `actualizado_at` — NOT `data.precios`
- `noticias_llm: {titulos_es_hoy, total_hoy}` — diagnóstico del pipeline LLM (ground truth desde DB). Si `titulos_es_hoy` es 0 tras un run, leer el log del job en Actions (`[noticias] Lote OK/Fallback OK/Error LLM`).
- `ultimas_noticias[]` trae `titulo_es`/`categoria`/`relevancia` (1-5, impacto GT)/`resumen_es` del LLM cuando hay key; si no, `relevancia` es null y el dashboard ordena por fecha. Top 5 = `relevancia` desc, luego fecha desc (`agruparNoticias`).
- Semáforo (`analizarImpactoPetrolero`): devuelve `motivo` SEPARADO de `mensaje`; el card lo renderiza como línea aparte (`.semaforo-motivo`: "Motivo: <noticia de mayor peso>"). En móvil/tablet (≤768px) el mensaje se clava a 3 líneas y el motivo a 2 (`-webkit-line-clamp`) para que el rectángulo no ocupe media pantalla.
- **Product names**: canónicos `'superior'`, `'regular'`, `'diésel'`, `'wti'`. `db.canon_producto()` normaliza al insertar (`diessel`/`diesel`→`diésel`, `super`→`superior`). Dashboard también normaliza por si acaso.
- **Sort order**: R, S, D via `Map` (NOT `indexOf()` which is unstable in V8 on Windows).
- **Chart rendering**: `renderHistorial()` MUST be called AFTER `contentEl.style.display = 'block'`. While container is hidden (`display:none`), `getBoundingClientRect()` returns 0×0 and canvas draws at wrong size.
- **Date display**: Takes max `fecha` across all products, NOT `precios[0].fecha` (which could be any product depending on sort order).
- **Time extraction**: si `fetched_at` trae offset local (`-06:00`) se muestra tal cual (`slice(11,16)`); solo se restan 6h si viene en UTC puro (`Z` o `+00:00`). El backend emite GT tz-aware, así que el caso normal es mostrar directo. Do NOT use `new Date()` parsing — the runner clock offset is unreliable.
- **Anti-flicker móvil**: cero animaciones `infinite` en el `<style>` inline (precios y borde del barril son estáticos; pulso permitido solo vía `opacity`). En `style-glass.css` el fondo mesh y `border-shimmer` se apagan con `@media (max-width:768px),(pointer:coarse)`. Resize con debounce 250ms que redibuja charts en estado final — NUNCA reiniciar `start*Animation` en resize (en móvil cada scroll = resize por la barra del navegador). Un solo listener global, no uno por render.

## Vercel deployment
- `vercel.json`: static build with cache headers for JSON files (max-age=60) and HTML (max-age=300). OJO: esos headers casi no aplican — el dashboard lee de `raw.githubusercontent.com` (`GITHUB_RAW` en el JS), no de Vercel.
- `_redirects` (`/* /web/index.html 200`) es sintaxis Netlify — Vercel lo ignora. `_routes.json` (sintaxis Azure SWA) también es muerto en Vercel.
- **Two index.html**: `web/index.html` (source of truth), `index.html` at root (copy for Vercel `/`). Always keep them in sync. La copia raíz usa `sprites/barrel-oil.png` y `iconos/favicon.*` (relativas a raíz); la de `web/` usa `../sprites/`, `../iconos/`.

## GitHub Actions (`daily-update.yml` produce datos; `test-apis.yml` solo diagnostica)
- **daily-update**: cron `0 14 * * *` (nominal 08:00 GT; en la práctica GitHub gratis lo ejecuta ~18:2x UTC), push a main, manual dispatch. Runner `windows-latest`, Python 3.11. Runs `python collector/main.py --alternos --petroleo --noticias --export`.
- **test-apis** (solo `workflow_dispatch`): corre `scripts/test_apis.py` contra el LLM primario de config.json (prompt mínimo), Groq secundario y MyMemory — sin DB ni export ni push. Mismo runner que daily-update: si la API responde ahí, responde en el run diario. Job rojo = ningún LLM completó el prompt (contrato del script).
- `concurrency: daily-update-global` (sin cancel) serializa schedule+push+dispatch.
- Push step: orden add → diff → **commit → pull --rebase → push HEAD:main**, SIN `|| true` (un rechazo queda rojo, no se pierde en silencio).
- `[skip ci]` en commits de docs/UI para no disparar runs. Sin `[skip ci]` el push dispara el workflow (útil para validar cambios de colectores).
- Regla: ningún workflow hace export parcial + push (con DB efímera eso sobrescribe el dashboard con datos viejos).
- `resumen.json` trae `precios_actualizados` (bool) + `max_fecha_precios`: guardia de frescura (stale si max fecha > 30 días). Si es false tras un run, revisar colectores.

## Scheduler (`collector/scheduler.py`)
```powershell
python scheduler.py --run-all                  # one-shot execution + export
python scheduler.py --schedule 3600            # repeat every N seconds (foreground)
python scheduler.py --export-task NOMBRE --interval-min N  # genera XML (ver --install-task/--uninstall-task)
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
6. `.opencode/skills/gasolina-gt/SKILL.md` — staged-build rules (hablar español, un stage por ciclo con checkpoint, nunca inventar datos/URLs, PROGRESS.md al día)
