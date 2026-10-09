# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

La referencia técnica completa (esquema, colectores, contrato del dashboard, CI, votos, gotchas) está en AGENTS.md y se importa aquí:

@AGENTS.md

## Reglas de trabajo (de `.opencode/skills/gasolina-gt/SKILL.md`)
- Hablar con el usuario en español; comentarios y docstrings en español.
- **Nunca inventar datos**: todo precio, tasa, fecha o noticia sale de una fuente consultada o de `config.json`. Si una fuente falla → "sin datos", nunca un valor estimado. Vale también para votos de prueba: jamás votar en la D1 de producción.
- **Nunca adivinar URLs ni endpoints**: si falta una fuente que no está en `config.json`, preguntar. Para descubrir feeds de un medio, leer su `robots.txt`/sitemap (así se encontró el de Publinews).
- Cada registro guardado lleva procedencia (`fuente`, `url` si aplica, `fetched_at` en hora de Guatemala vía `ahora_gt_iso()`).
- Ediciones pequeñas; leer antes de editar. Si un comando falla dos veces seguidas, parar y reportar el error exacto.
- Registrar decisiones y bugs relevantes en `PROGRESS.md`.
- Claves y tokens los pone el dueño (`wrangler secret put NOMBRE`, secretos de GitHub); no pedirlos ni imprimirlos. OJO: los NOMBRES de secretos no son secretos (`wrangler secret list` los muestra tal cual): si alguien pega un token donde va el nombre, queda expuesto; enmascarar al listar.

## Comandos
```powershell
# Python (colectores, calibración, dashboard estático)
python -m pytest tests/ -q -p no:cacheprovider                       # suite completa (0 fallas esperadas)
python -m pytest tests/test_calibracion.py -v                        # un archivo
python -m pytest tests/test_web.py::test_vercel_web_analytics -v     # un test
python collector/main.py --memoria --alternos --consenso --petroleo --noticias --export --calibracion   # run completo como en CI
python collector/main.py --calibracion                               # solo medir votos (opt-in, no está en --all)
python serve.py                                                      # http://localhost:8089/web/index.html (usar "localhost", no 127.0.0.1: CORS del Worker)

# Servidor de votos (Cloudflare Worker + D1, carpeta votos-api/, requiere Node >= 22)
cd votos-api
npm test                                  # node --test test/*.test.js (D1 simulado con node:sqlite)
node --test test/api.test.js              # un archivo;  añadir --test-name-pattern="<texto>" para un test
npm run test:integracion                  # workerd + D1 locales reales (10 votos simultáneos, Cron Trigger, export -> Python)
npm run db:local ; npm run dev            # Worker local en http://127.0.0.1:8787 (el dashboard lo usa si la página es localhost)
npx wrangler deploy                       # publica (ya logueado); secretos: npx wrangler secret put NOMBRE (el nombre es obligatorio)
```
No hay build ni linter: el dashboard es HTML/CSS/JS vanilla. Vercel solo sirve el sitio estático (`.vercelignore` es lista blanca: `votos-api/`, `collector/`, etc. no llegan a Vercel).

## Arquitectura: dos sistemas que se tocan en pocos puntos
1. **Pipeline de precios (Python, GitHub Actions `daily-update.yml`, windows-latest)**: `main.py` orquesta colectores sobre una DB SQLite efímera.
   - `--memoria` restaura la DB desde `data/db/*.csv` (en git) → los colectores escriben vía `db.guardar_precios` (prioridad de fuente: MEM > Consejo/manual > alternas) → `--export` genera `data/export/consolidado.json` y re-vuelca los CSV → el workflow los commitea a `main`.
   - El consejo de medios (`consenso_precios.py`) descubre notas por feeds directos (Prensa Libre, Publinews) y Bing, extrae precios con LLM y emite un veredicto con confianza. El MEM NO se puede leer desde CI ni desde el navegador integrado (Cloudflare 403): no depender de él.
   - Los runs puntuales (04:52 y 11:52 GT) los dispara el Worker de Cloudflare vía `workflow_dispatch`; el cron de GitHub (retrasado 4-6 h) es solo red de seguridad, guardado por `scripts/frescura.py`.
2. **Votos ciudadanos (`votos-api/`, Cloudflare Worker + D1)**: el navegador habla directo con el Worker (`POST /api/voto`, `GET /api/resumen`), sin pasar por Vercel ni por el pipeline. Una vez por run, `scripts/descargar_votos.py` baja `/api/exportar` a `data/inbox/votos/` y `collector/calibracion.py` los importa (`data/db/votos.csv`), los mide contra una referencia autónoma (MEM → consejo de medios) y guarda `calibracion.csv`. La lógica de estados vive DUPLICADA en `calibracion.py` y `votos-api/src/logica.js`; el fixture `tests/fixtures/clasificar_casos.json` la mantiene en paridad (cambiar una = actualizar la otra y el fixture).
3. **Dashboard**: `web/index.html` y `index.html` (raíz, la que sirve Vercel) son copias que deben recibir EXACTAMENTE la misma edición (solo difieren rutas relativas). Lee `consolidado.json` desde `raw.githubusercontent.com` (no desde Vercel) y, por eso, el repositorio debe seguir PÚBLICO: privado rompería el sitio y el Worker (que lee el mismo archivo). El JS de votos y el aviso de IA están al final del archivo.

## Bloques generados del dashboard (no editar a mano)
- **Árbol animado de empresas** (importadores -> aduana/grúas -> pipas -> gasolineras, bajo la tarjeta del WTI): lo escribe `python scripts/generar_logos_pixel.py` entre `<!-- Empresas:` y `<!-- /Empresas -->` (y su CSS entre `/* ── Árbol de empresas` y `/* ── Legibilidad`) en `web/index.html` e `index.html` a la vez. Para cambiar un dibujo o una ruta, editar el script y volver a correrlo; editar el HTML a mano se pisa. Emblemas/barcos/pipas son ilustrativos (no logos oficiales); solo se anima `opacity` (regla anti-parpadeo móvil). Las empresas y vínculos salen de fuentes citadas en el pie del bloque: no añadir empresas sin fuente.
- **App instalable (PWA)**: botón "Instalar como app" bajo el título (script inline en el `<header>`), `manifest.webmanifest`, `sw.js` (solo hace instalable la página, NO cachea: el precio debe ser el más reciente) e `iconos/icon-*.png` generados con `python scripts/generar_iconos_app.py` (necesita Pillow y la fuente Segoe UI Bold de Windows). Archivos nuevos en la raíz deben añadirse a la lista blanca de `.vercelignore` o Vercel no los publica. `related_applications` del manifiesto apunta al dominio `gasolinasogt.com`.
- `fuentes.html` NO debe enlazar al GitHub del dueño (decisión del dueño); el JS de `index.html` sí lee `raw.githubusercontent.com`, eso no se toca.

## Flujo de git
- Pedir confirmación antes de CADA `git push` (memoria del usuario). Antes de empujar: `git pull --rebase` (el run diario commitea datos a `main` y rechaza el push si no).

## Gotchas de edición en Windows
- El árbol de trabajo está en CRLF (`core.autocrlf=true`). Al editar con Python usar `open(..., newline="")` o `read_bytes/write_bytes` para no reescribir el archivo entero; en pytest/`git diff` los archivos nuevos avisan "LF will be replaced by CRLF" (inofensivo).
- La consola del runner y de la máquina del dueño es cp1252: ningún `print()`/logger de `collector/` o `scripts/` puede llevar `→ ≥ ± ✓` (lo vigila `tests/test_consola.py`).
- En rutas de Windows dentro de strings de Python/heredocs, `\v`, `\i`, `\t` se interpretan como escapes (así se rompió una ruta del workflow): usar cadenas crudas y revisar el diff.
- En Workers, `fetch` no puede guardarse en un objeto y llamarse como `obj.fetch()` ("Illegal invocation"); envolverlo `(...a) => fetch(...a)`.
- `curl` en Windows falla el TLS con `*.workers.dev`; probar con Node `fetch`. Un subdominio workers.dev nuevo tarda unos minutos en tener certificado.
- Los commits de docs/UI llevan `[skip ci]` (GitHub y Vercel lo respetan a la vez); los cambios en `collector/**`, `scripts/**`, `config.json` o el workflow SÍ disparan el workflow y gastan cuota de OilPriceAPI/LLM.

## Ojo con PROGRESS.md
Su encabezado describe las etapas originales (EIA, Brent, 6 JSONs, `precios_mem.py`); esa arquitectura fue reemplazada (OilPriceAPI solo WTI, `consolidado.json`, tabla única). El final del archivo sí es un registro fechado y confiable de decisiones recientes. Para el estado actual, confiar en AGENTS.md y el código.
