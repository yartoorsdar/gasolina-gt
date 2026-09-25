# Progreso gasolina-gt — COMPLETADO ✅
Estado: todas las etapas implementadas y validadas

## Etapas aprobadas
- [x] Etapa 0 — Setup (2026-09-22)
- [x] Etapa 1 — impuestos.py (funciones puras + tests 24/24)
- [x] Etapa 2 — db.py (SQLite capa datos + tests 30/30)
- [x] Etapa 3 — precios_mem.py (PDF MEM parseado con extract_words(), validado contra PDF real INFORME EJECUTIVO 2026-09-21: 6/6 precios correctos. Tests: 14/14)
- [x] Etapa 4 — importar_historico.py (XLSX diario MEM parseado con openpyxl, validado contra archivo real: 10,809 registros desde 2013 hasta hoy. Tests: 15/15)
- [x] Etapa 5 — petroleo.py (Brent RBRTE + WTI RWTC vía EIA API v2, validado con API key real: Brent $89.73/bbl, WTI $84.62/bbl. Tests: 14/14)
- [x] Etapa 6 — noticias.py (4 feeds RSS: Google News ES/EN, oilprice.com, EIA; parseo con feedparser + fallback XML; clasificación LLM opcional. Tests: 17/17)
- [x] Etapa 7 — main.py (orquestador CLI `--all`/`--export`; exporta 6 JSONs a data/export/: resumen.json, precios_combustible.json, petroleo.json, historial_precios.json, historial_petroleo.json, noticias.json. Tests: 13/13)
- [x] Etapa 8 — web/index.html (dashboard local autocontenido con CSS+JS vanilla; lee JSONs de data/export/; responsive; sin dependencias externas; servidor `serve.py`. Tests: 7/7)
- [x] Etapa 9 — scheduler.py (programador de actualizaciones: `--run-all` ejecucion unica, `--schedule N` repetitivo cada N segundos, generador XML para Windows Task Scheduler con `--export-task`, instalador directo con `--install-task`. Tests: 10/10)

## Etapas completadas
- [x] Etapa 0 — Setup
- [x] Etapa 1 — impuestos.py
- [x] Etapa 2 — db.py
- [x] Etapa 3 — precios_mem.py
- [x] Etapa 4 — importar_historico.py
- [x] Etapa 5 — petroleo.py
- [x] Etapa 6 — noticias.py
- [x] Etapa 7 — main.py
- [x] Etapa 8 — web/index.html
- [x] Etapa 9 — scheduler.py

## Resumen final
**138 tests passing.** Sistema completo funcional.

## Decisiones tomadas
- MEM precios actuales: PDF semanal "INFORME EJECUTIVO DE PRECIOS DE LOS COMBUSTIBLES". Se parsea con pdfplumber extract_words() + agrupación por posición Y. Extrae precios AutoServicio y Servicio Completo (superior, regular, diésel). Filtra sección de histórico semanal ("ÚLTIMAS SEMANAS"). Plan B: data/inbox/ con PDFs manuales.
- MEM histórico: XLSX diario "PUBLICACIÓN WEB" con columnas FECHA, Gasolina Superior, Regular, Diésel, Bunker, GLP. openpyxl parsea fila 8+ (encabezados en fila 6). Plan B: data/inbox/historico/.
- Esquema precios_combustible ampliado con columnas "regimen" y "nota". Regímenes: normal, apoyo_social_2026, exencion_decreto_22_2026.
- LLM local: mismos base_url y model que OpenCode; verificar con GET /models antes de codificar.
- EIA API v2 endpoint: https://api.eia.gov/v2/petroleum/pri/spt/data/. Series Brent RBRTE, WTI RWTC. Gasolina/diésel US Gulf Coast pendientes de metadata.
- Feeds RSS: Google News ES y EN, oilprice.com, EIA todayinenergy.xml. Probar cada feed antes de Etapa 6.
- Revisión workflow daily-update (2026-09-25): paso commit reordenado a add → diff → **commit → pull --rebase → push HEAD:main** sin `|| true` (antes el rebase fallaba en silencio con índice dirty y los pushes rechazados eran invisibles). Concurrency global `daily-update-global` (sin cancel) serializa schedule+push+dispatch. `actualizado_at` ahora usa `ahora_gt_iso()` de collector/db.py (GT tz-aware determinista, sin red); worldtimeapi.org fallaba en CI y dejaba UTC (+00:00). `.debug_time.txt` movido a `logs/`. Ojo: cron corre ~18:2x UTC real (GitHub Actions gratis) → datos llegan ~12:20 GT, no 08:00. Secreto `GROK_API_KEY` devuelve 401 → clasificación LLM apagada; títulos ES cubiertos por fallback MyMemory. Falta actualizar el secreto en GitHub.
- Debug pipeline IA (2026-09-25): workflow nuevo **test-apis.yml** (solo dispatch) + `scripts/test_apis.py` — prueba provider-aware del LLM primario de config.json con prompt mínimo, catálogo de modelos, Groq secundario y MyMemory; job rojo = ningún LLM completó el prompt. Hallazgos desde runner real: Gemini `gemini-3.6-flash` da **402 prepayment credits depleted** (free tier agotada, NO bloqueo IP — tiempos normales ~1s) → Groq pasa a **primario**: config.json llm = `api.groq.com/openai` + `openai/gpt-oss-120b` (verificado: `/v1/models` 200 con 11 modelos). Fixes en noticias.py: (a) `_llm_post` rama OpenAI ahora manda `Authorization: Bearer` (sin él 401 en el POST); (b) `_obtener_api_key(llm_cfg, base_url)` discrimina proveedor — Gemini→GEMINI_API_KEY, compatible→GROK_API_KEY; (c) fallback de traducción a 2 pasadas (ES gratis primero, EN vía MyMemory después: antes un `break` por cuota dejaba sin categoría las ES restantes); (d) lote de traducción usa `_seleccion_rotativa(pendientes)` en vez de `pendientes[:15]` — el primer feed ES se comía todo y los EN nunca entraban.
- Pipeline IA v2 + UI semáforo (2026-09-25): bug reportado "noticia 4 de drones con URL en lugar de traducir" → raíz: feeds Google News traen `summary` como HTML `<a href="...">título</a><font>medio</font>` y el fallback guardaba ese crudo en `resumen_es`. Rediseño del flujo por exigencia (verificar → ordenar → traducir al final, descartando defectuosos hasta dejar 5 fichas limpias): `_limpiar_texto` limpia HTML/entidades al recibir; `_item_valido` descarta estructura rota; `_ordenar_candidatos` prioriza por relevancia determinística (el lote LLM ahora traduce el TOP rankeado, no round-robin); `_validar_cls` exige título+descripción presentes/legibles/sin URL-HTML-ininteligibles (lo que falla se pasa a la siguiente; sin cuerpo → descripción vacía permitida y oculta en UI). `NOTICIAS_EXCELENTES=5`: si el LLM no cubre 5, `_traducir_fallback(objetivo=N)` solo completa lo faltante; items rechazados NO reciben categoria/relevancia (no ensucian el top-10). Prompt del lote: "(sin cuerpo)" → escribir resumen desde título + sin sufijos de medio en titulo_es. UI: `analizarImpactoPetrolero` devuelve `motivo` separado y el card lo muestra como línea `.semaforo-motivo`; en ≤768px mensaje clamped a 3 líneas y motivo a 2 (el rectángulo ya no ocupa media pantalla en móvil). Tests: +8 nuevos (TestPipelineCalidad), suite 107 passing / 7 fallidos preexistentes de esquema viejo.
- Datos MEM manuales (2026-09-25): serie oficial 16–24 sep insertada a la DB local con `fuente='MEM'` (delete-before-insert por fecha+producto; el campo "estado" verificado/arrastre es contexto, la tabla única no lo guarda). Ojo: (a) la DB de CI es efímera — cada run regenera los 6 JSONs desde cero, así que estas filas solo viven en vivo hasta el próximo cron (~12:2x GT); (b) `python main.py --export` SIN flags corre TODOS los colectores por defecto (y consenso_retry duerme 45min): para exportar solo, llamar `from collector.main import exportar_json`.
- Memoria persistente de precios (2026-09-25): nueva `collector/memoria.py` — la tabla `precios` vive commitada en `data/memory/precios.csv`: cada run IMPORTA el CSV al inicio (DB efímera → se siembra todo el historial), los colectores agregan/actualizan el día, y al final RE-EXPORTA el CSV completo que se commitea junto con los JSONs. El archivo es determinista (orden fecha+producto canónico, sin fetched_at) → nada cambió = cero diff. Deduplica grafías diessel/diésel del mismo día (conserva la `fetched_at` más reciente; había 1344 pares duplicados de imports XLSX). Flag `--memoria` en main.py; CI lo usa siempre. Tests: +5 (test_memoria.py), suite 112 passing / 7 fallidos preexistentes.

## Bugs conocidos
- SQLite UNIQUE inline bug: `CREATE TABLE ... UNIQUE(...)` en executescript() no crea índice implícito en esta versión Windows/SQLite. Se usa CREATE UNIQUE INDEX explícito después de crear tablas. Afecta conectar_temporal() en tests.

## Pendientes / preguntas abiertas
- EIA_API_KEY: variable en .env, necesita valor del usuario.
- Gemini (gemini-3.6-flash) en pausa por 402 cuota agotada: renovar key/billing en AI Studio y revertir config.json a generativelanguage cuando vuelva a responder (test-apis lo confirma).

## Archivos de arranque (bat)
### `iniciar.bat` — Dashboard local (puerto 9090)
```powershell
.\iniciar.bat
# → Actualiza datos JSON + abre http://localhost:9090/web/index.html
```
Sirve solo en red local. Para ver desde celular mismo WiFi: `http://192.168.0.11:9090/web/index.html`

### `iniciar_externo.bat` — Dashboard + ngrok (acceso público)
```powershell
.\iniciar_externo.bat
# → Actualiza datos JSON + abre servidor 9090 + tunnel ngrok
```
Genera URL tipo `https://xxx.ngrok-free.app` compartible con clientes. Requiere token ngrok guardado en `~\AppData\Local\ngrok\ngrok.yml`.
