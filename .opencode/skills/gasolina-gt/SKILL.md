---
name: gasolina-gt
description: Staged build of a local web dashboard that tracks Guatemala fuel prices (super, regular, diesel) with and without taxes (IVA + IDP, Decreto 22-2026 exemption), price history in SQLite, Brent/WTI crude prices, and sourced news alerts about wars and attacks on oil infrastructure. Use this skill for ANY work on the gasolina-gt project - collectors, database, tax logic, news classification, the HTML page, or scheduling - even if the user only says "siguiente etapa", "continúa", "gasolina" or "combustibles".
---

# gasolina-gt — Guatemala fuel price dashboard (staged build)

You are building this project **one stage at a time** under the supervision of the user (Rodrigo). He reviews and approves every stage before the next one starts. Supervision is the whole point of this skill: speed is secondary.

## 0. Non-negotiable rules

1. **Talk to the user in Spanish.** Code comments and docstrings in Spanish. Identifiers (variables, functions, files) in Spanish or English, but consistent inside each file.
2. **One stage per turn cycle.** Finish the current stage, run its checks, present the CHECKPOINT (section 4), then STOP. Never start the next stage until the user writes `siguiente`, `aprobado`, `continúa` or clearly equivalent.
3. **If the user asks for changes, stay in the current stage.** Apply the change, re-run the checks, present the checkpoint again.
4. **Never invent data.** Every price, rate, date or news fact shown in the page must come from a fetched source or from `config.json`. Never write a price, barrel value or event from your own knowledge — your knowledge is outdated. If a source fails, record the failure and show "sin datos"; never fill in a guess.
5. **Never guess URLs or API endpoints.** When a stage needs a source URL that is not already in `config.json`, ask the user for it and wait.
6. **Every stored record carries provenance:** `fuente` (name), `url`, and `fetched_at` (ISO 8601, America/Guatemala timezone).
7. **Small edits.** Read a file before editing it. Change only what the stage requires. Do not rewrite working files from scratch.
8. **If a command or tool call fails twice in a row, stop** and report the exact error to the user instead of trying random fixes.
9. **Keep `PROGRESS.md` updated** at the end of every stage (section 3). At the start of every session, read `PROGRESS.md` first and resume from the stage it indicates.
10. **Secrets** (API keys) live only in `.env`, never in code, `config.json`, or git. `.env` is in `.gitignore`.

## 1. Environment

- OS: Windows 11, shell PowerShell. Use Windows paths and PowerShell syntax in any command you give the user.
- Python 3.11+ in a local venv: `python -m venv .venv` then `.\.venv\Scripts\Activate.ps1`.
- Allowed dependencies (add others only after asking): `requests`, `beautifulsoup4`, `lxml`, `feedparser`, `openpyxl`, `pdfplumber`, `python-dotenv`, `pytest`. Database: built-in `sqlite3`.
- Local LLM for news classification: OpenAI-compatible endpoint (llama-server or LM Studio). URL and model name come from `config.json` → `llm.base_url`, `llm.model`. Use plain `requests` POST to `/v1/chat/completions`; no SDK needed.
- The page is served locally with `python -m http.server 8000` from `web/` (or VS Code Live Server). Opening `index.html` via `file://` breaks JSON loading — never suggest that.

## 2. Project structure

```
gasolina-gt/
├── .opencode/skills/gasolina-gt/SKILL.md
├── .env                  # EIA_API_KEY=...   (not in git)
├── .env.example
├── .gitignore
├── config.json           # sources, tax constants, decree status, RSS feeds, LLM
├── requirements.txt
├── PROGRESS.md
├── collector/
│   ├── __init__.py
│   ├── impuestos.py
│   ├── db.py
│   ├── precios_mem.py
│   ├── importar_historico.py
│   ├── petroleo.py
│   ├── noticias.py
│   └── main.py
├── tests/
│   └── test_*.py
├── data/
│   └── historial.db
├── logs/
│   └── collector.log
└── web/
    ├── index.html
    ├── style.css
    ├── app.js
    ├── vendor/chart.umd.min.js   # local copy, works offline
    └── data/                     # JSON exported by main.py
        ├── actual.json
        ├── historial.json
        └── noticias.json
```

## 3. PROGRESS.md format

```
# Progreso gasolina-gt
Etapa actual: N — <nombre>
Estado: en curso | esperando aprobación | aprobada

## Etapas aprobadas
- [x] Etapa 0 — Setup (fecha)
- [ ] Etapa 1 — ...

## Decisiones tomadas
- <decision> (etapa, fecha)

## Pendientes / preguntas abiertas
- ...
```

## 4. CHECKPOINT format (end of every stage)

Present exactly this, in Spanish, then stop:

```
## ✅ Etapa N — <nombre> terminada

**Qué hice:** 2–4 frases.
**Archivos creados/modificados:** lista con ruta.
**Cómo verificarlo tú mismo:** comandos exactos de PowerShell y qué resultado esperar.
**Resultado de mis pruebas:** salida real de pytest / del script (resumida si es larga).
**Dudas o riesgos:** lo que no pude confirmar, o "ninguno".

Escribe **siguiente** para pasar a la Etapa N+1 — <nombre>, o dime qué cambiar.
```

## 5. Domain facts (tax logic)

These are the ONLY hardcoded domain facts allowed. Store them in `config.json`, not in code.

- **IVA:** 12% applied on top of (base price + IDP).
- **IDP per gallon (Decreto 38-92):** superior Q4.70, regular Q4.60, diésel Q1.30.
- **Formulas:**
  - `sin_impuestos = con_impuestos / 1.12 - idp`
  - `con_impuestos = (sin_impuestos + idp) * 1.12`
  - Round to 2 decimals only for display; keep full precision internally.
- **Verification values (use as unit tests):**
  - regular: Q42.00 con impuestos → Q32.90 sin impuestos
  - superior: Q44.00 → Q34.59
  - diésel: Q47.00 → Q40.66
- **Decreto 22-2026** (initiative 6852): temporary exemption of IVA and IDP on superior, regular, diésel (also gas oil and alcohol carburante), approved by Congress on 2026-09-22, valid until 2026-12-31. It requires presidential sanction and publication to take effect.
- In `config.json`:
  ```json
  "decreto_22_2026": {
    "estado": "pendiente_sancion",
    "fecha_aprobacion_congreso": "2026-09-22",
    "fecha_vigencia_inicio": null,
    "fecha_vigencia_fin": "2026-12-31"
  }
  ```
  The **user** updates `estado` and `fecha_vigencia_inicio` manually when the decree is sanctioned and published. Never change these fields yourself.
- **Key rule:** whether an observed pump price includes taxes depends on its observation date. If the date falls inside `[fecha_vigencia_inicio, fecha_vigencia_fin]`, the observed price is WITHOUT taxes and the page computes the with-taxes value as a reference ("lo que costaría con impuestos"). Outside that range, the observed price is WITH taxes and the page computes the without-taxes value. Store `incluye_impuestos` (0/1) on every price row.
- Decree status derived for the page: `pendiente_sancion`, `vigente` (today inside range), `vencido` (today > fin).

## 6. Stages

### Etapa 0 — Setup
Create the folder structure, venv instructions, `requirements.txt`, `.gitignore` (`.venv/`, `.env`, `data/*.db`, `logs/`, `__pycache__/`), `.env.example`, `config.json` with the section 5 values and empty placeholders for sources, `PROGRESS.md`, and `git init`. Do not write any logic yet.
**Check:** `pip install -r requirements.txt` succeeds; `python -c "import json; json.load(open('config.json'))"` succeeds.

### Etapa 1 — impuestos.py
Pure functions, no I/O except reading constants passed in: `quitar_impuestos(precio, producto, cfg)`, `agregar_impuestos(precio, producto, cfg)`, `estado_decreto(hoy, cfg)`, `precio_incluye_impuestos(fecha_obs, cfg)`. Write `tests/test_impuestos.py` with the three verification values, round-trip tests, and decree state tests for dates before, during and after the range (including `fecha_vigencia_inicio = null`).
**Check:** `pytest tests/test_impuestos.py -v` all green.

### Etapa 2 — db.py (SQLite)
Tables:
- `precios_combustible(id, fecha_observacion, producto, precio, incluye_impuestos, fuente, url, fetched_at, UNIQUE(fecha_observacion, producto, fuente))`
- `precios_petroleo(id, fecha, referencia /*brent|wti*/, usd_barril, fuente, url, fetched_at, UNIQUE(fecha, referencia))`
- `noticias(id, url UNIQUE, titulo, medio, publicado_at, categoria, pais, relevancia, resumen_es, fetched_at)`
- `ejecuciones(id, inicio, fin, modulo, ok, mensaje)`
Helpers for insert-or-ignore and simple queries. Use a temp DB in tests.
**Check:** `pytest tests/test_db.py -v`; duplicate inserts are ignored, not duplicated.

### Etapa 3 — precios_mem.py (current prices)
FIRST ask the user for the exact URL where the Ministerio de Energía y Minas (MEM) publishes current reference prices, and whether it is HTML, PDF or Excel. Wait for the answer. Then write a parser for that format, returning super/regular/diésel with the observation date the source states. Save to DB with `incluye_impuestos` from Etapa 1 logic. Save a raw copy of the downloaded page/file under `data/raw/` for debugging.
**Check:** run the module; show the parsed values in the checkpoint and ask the user to compare them against the MEM site by eye. The user's confirmation is part of approval.

### Etapa 4 — importar_historico.py (one-time backfill)
Ask the user for the URL/files of MEM historical price series. Write a one-time importer into the same table, idempotent (safe to run twice). Report: date range imported, row count per product, rows skipped and why.
**Check:** run twice; second run inserts 0 rows.

### Etapa 5 — petroleo.py (Brent / WTI)
Use the EIA API v2 with the key from `.env` (daily spot series: Brent `RBRTE`, WTI `RWTC`). Confirm the endpoint with the user before coding. Store the latest available values; note in the page that EIA spot data can lag a few days and show the actual data date, never "hoy" unless it is today.
**Check:** run the module; show the last 5 stored values with their dates.

### Etapa 6 — noticias.py (wars and infrastructure attacks)
- RSS feed list comes from `config.json` → `noticias.feeds`; ask the user which feeds to use.
- For each new item (URL not yet in DB), send title + summary to the local LLM with this instruction, asking for JSON only:
  `categoria` ∈ {`conflicto_productor`, `ataque_infraestructura`, `opep_produccion`, `irrelevante`}, `pais`, `relevancia` 1–5, `resumen_es` (max 2 sentences, only facts present in the item).
- Parse the JSON defensively (strip code fences, validate enum values). On invalid output, retry once, then store as `irrelevante` with a log entry.
- Discard `irrelevante`. Never merge facts from different articles. Never produce a verdict like "hay guerra" — the page shows a list of recent sourced headlines, each with link and date.
**Check:** run on the configured feeds; show counts per category and 3 example classified items with their URLs so the user can verify the classification.

### Etapa 7 — main.py (orchestrator + JSON export)
Runs Etapas 3, 5 and 6 modules in sequence; a failure in one module is logged in `ejecuciones` and does not stop the others. Then exports:
- `web/data/actual.json`: latest price per product with both values (con/sin impuestos), which one is observed, observation date, source; latest Brent/WTI with date; decree state; `generado_at`.
- `web/data/historial.json`: full fuel and oil series.
- `web/data/noticias.json`: last 30 days of relevant news, sorted by `publicado_at` desc.
Log to `logs/collector.log`.
**Check:** `python -m collector.main`; show `actual.json` content.

### Etapa 8 — Web page
`index.html`, `style.css`, `app.js`, Chart.js as a local file in `web/vendor/` (ask the user to download it or give the exact command). Contents:
- Banner with Decreto 22-2026 state and date range.
- Three cards (super, regular, diésel): observed price large, the other value smaller with a clear label, observation date and source link.
- Toggle "con impuestos / sin impuestos" that switches the whole page.
- History chart per product with range selector (30 días, 6 meses, todo); shade the exemption period.
- Brent/WTI card with data date.
- News alerts list: category badge, country, summary, outlet, date, link.
- Footer: `generado_at` and a warning if data is older than 24 h.
Dark/light via `prefers-color-scheme`, responsive. No external requests at runtime except the local JSON files.
**Check:** serve with `python -m http.server 8000` from `web/`, open `http://localhost:8000`; the user reviews it visually.

### Etapa 9 — Automatic updates
Create `ejecutar_collector.bat` (activates venv, runs `python -m collector.main`). Give the user the exact `schtasks` command to run it every 6 hours — the user runs it, not you. Explain how to check the last run in `ejecuciones` and the log.
**Check:** the user runs the .bat manually once and confirms `actual.json` updated.

## 7. Working style for this model

- Before coding a stage, state in 3–5 lines what you are about to do and which files you will touch.
- Prefer several small, verified steps over one big write.
- After every code change, run the relevant test or script and read its output before claiming success.
- When unsure about a source format, a URL, or a user preference: ask one clear question and wait.
