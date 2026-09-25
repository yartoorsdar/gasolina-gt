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

## Bugs conocidos
- SQLite UNIQUE inline bug: `CREATE TABLE ... UNIQUE(...)` en executescript() no crea índice implícito en esta versión Windows/SQLite. Se usa CREATE UNIQUE INDEX explícito después de crear tablas. Afecta conectar_temporal() en tests.

## Pendientes / preguntas abiertas
- EIA_API_KEY: variable en .env, necesita valor del usuario.
- LLM base_url/model: valores pendientes para Etapa 6.

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
