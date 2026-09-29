# Gasolina GT — Agent Instructions

## Objetivo del producto (decisión del dueño, 2026-09-28)
- El sitio existe para que la población guatemalteca tenga un **registro diario, lo más reciente y útil posible, de Superior, Regular y Diésel** (solo esos 3; sin otros combustibles). Contexto clave: la aprobación del **Decreto 22-2026** (exención temporal de IVA e IDP hasta 2026-12-31) puede mover el precio Q4.60–5.04 de golpe; el dato debe reflejarlo **el mismo día**, no una semana después.
- **Frescura manda**: no se acepta información de hace 7 días como "precio de hoy". Toda fuente nueva (incluidas las de validación cruzada, ej. datos de comunidad tipo Google Maps/Waze) debe aportar dato diario o casi en tiempo real (umbral de referencia: ≤ 24–48 h); si una fuente solo da datos semanales, sirve de histórico/ancla, no de precio actual.
- Por eso varias fuentes independientes (contar ORÍGENES, no notas: medios que republican el mismo texto cuentan como uno) y validación cruzada, sin depender solo del MEM ni de un medio.
- **Votos ciudadanos = pilar del dato, junto al MEM (decisión del dueño, 2026-09-29)**: el MEM es una fuente centralizada; las personas son quienes ven y pagan el precio en la bomba. Los votos de la comunidad NO son solo un aviso o testigo: son la base, a la par del MEM, y son lo más importante de la web (el bloque de votos va de primero en la tarjeta de precios). Lo que queda por resolver es la **calidad de los votos**, no si cuentan.
  - Un voto entra como fuente `Comunidad` con procedencia (nº de votos, nº de dispositivos distintos, mediana, dispersión, ventana de 24 h, `fetched_at` vía `ahora_gt_iso()`); nunca como valor suelto ni inventado.
  - Calidad (umbrales de ARRANQUE, a calibrar con datos reales, no cifras probadas): mínimo ~10 votos válidos (1 por dispositivo) para señal comunitaria; ≥ 60 % de los precios escritos dentro de ±Q0.50 de la mediana; rangos absurdos descartados; un voto por producto/día/dispositivo; usar mediana, nunca promedio. "Coincide" pesa menos que un precio escrito (efecto ancla: la gente confirma lo que ya ve); idealmente parte de los usuarios vota sin ver el precio oficial.
  - Estados visibles: sin datos → pocos votos → coincide → posible cambio (≥ Q1 del oficial, aún sin respaldo) → cambio confirmado (respaldado por fuente independiente). Textos de esos avisos: prototipo `prototipos/dashboard-comunidad.html`.
  - **Fase de prueba de 7 días seguidos** cruzando votos vs MEM antes de que los votos muevan el precio mostrado; guardar cada día cuánto se acercó la mediana de la comunidad al MEM y calibrar umbrales con eso. Se espera participación real (influencers y gobierno).
  - PENDIENTE de decidir tras la fase de prueba: regla de desempate cuando MEM y Comunidad difieren (hoy `db.prioridad_fuente` da MEM=3, Consejo/manual=2, alternas=1 y "el MEM siempre gana"; una fuente `Comunidad` cambia esa premisa y habrá que revisarla en `db._FUENTES` y en `consenso_precios.py`). Mientras tanto, mostrar ambos por separado y nunca escribir votos en la serie oficial `precios` sin esa decisión.
- Deuda conocida frente a este objetivo: el workflow diario NO corre `--mem-html` (Cloudflare da 403 al MEM, también desde PC), GlobalPetrolPrices falla el parser, y el consejo (`consenso_precios.py`) usa ventana de 7 días. Revisar estas piezas al añadir fuentes.

## Quick start
```powershell
cd C:\Users\manue\Documents\proyectos\web\Gasolina
python -m pytest tests/ -q -p no:cacheprovider   # suite completa (0 fallas esperadas)
python collector/main.py --memoria --alternos --consenso --petroleo --noticias --export   # CSV→DB, colectores, consolidado.json + CSV
python serve.py                             # dashboard at http://localhost:8089/web/index.html
```

## Architecture
- `collector/*.py` — `db.py` (schema + `ahora_gt_iso()`/`hoy_gt()` + `canon_producto()`), `impuestos.py`, `mem_html.py`, `fuentes_alternas.py`, `consenso_precios.py`, `importar_historico.py`, `petroleo.py`, `noticias.py`, `memoria.py`, `calibracion.py` (fase de prueba de votos), `main.py`, `scheduler.py` (`precios_mem.py` eliminado)
- `web/index.html` — single-file dashboard (CSS+JS vanilla, no build step)
- `index.html` — copy of web/index.html at repo root (Vercel serves this at `/`)
- `data/export/consolidado.json` — ÚNICO archivo exportado (`main.py:exportar_json` → `construir_consolidado`). Los viejos resumen/precios_combustible/petroleo/historial_*/noticias.json y las copias en raíz y `web/` se eliminaron (2026-09-25).
- `data/db/<archivo>.csv` — historial persistente de cada producto, en git (`regular.csv`, `superior.csv`, `diesel.csv`, `wti.csv`; columnas `fecha,modalidad,precio,fuente` — un CSV viejo sin `modalidad` se lee como la principal) + `observaciones.csv`, `articulos.csv`, `consenso.csv` (tablas del consejo, `memoria.TABLAS_MEMORIA`) + `mensual.csv` (promedios mensuales oficiales MEM 2020-01→, autoservicio) + `anual_semilla.csv` (2002–2019, sin fuente verificada). Procedencia de cada tramo en `data/db/FUENTES.md`. Serie = precio promedio MONITOREADO autoservicio Ciudad Capital; NO mezclar con "precios de referencia" semanales del MEM (otra serie). `collector/memoria.py` los importa/exporta. Reemplaza a `data/memory/precios.csv`.
- `data/historial.db` — SQLite, NOT in git (ephemeral in CI): `productos`, `precios`, vistas `historial_<archivo>`, `noticias`, `ejecuciones`

## DB schema (single-table)
```sql
CREATE TABLE productos (codigo PK, nombre, categoria, unidad, archivo, orden);  -- catálogo = db.PRODUCTOS
CREATE TABLE precios (id, fecha, producto, precio, fuente, fetched_at, modalidad);
-- UNIQUE(fecha, producto, modalidad) = idx_precios_clave (inline UNIQUE broken on Windows/SQLite)
-- modalidad: combustibles 'autoservicio' (PRINCIPAL) | 'servicio_completo'; wti 'spot'. db.PRODUCTOS[p]['modalidades'][0] = principal
CREATE TABLE observaciones (fecha, producto, modalidad, precio, tipo, medio, url, cita, extractor, fetched_at);  -- insumo del consejo
CREATE TABLE articulos (url PK, medio, titulo, publicado, procesado_at, extractor, n_obs, error);            -- notas ya leídas
CREATE TABLE consenso (fecha, producto, modalidad, precio, confianza, n_coinciden, n_fuentes, fuentes JSON); -- veredicto
CREATE TABLE votos (id_voto PK, fecha, producto, modalidad, tipo, precio, precio_mostrado, vio_oficial, dispositivo, zona, ts);  -- votos ciudadanos (hash anónimo)
CREATE TABLE calibracion (fecha, producto, modalidad, ref_precio, ..., estado, alerta, cambio_real, ...);  -- medición diaria vs MEM (PK fecha+producto+modalidad)
-- Vistas historial_regular / historial_superior / historial_diesel / historial_wti (generadas del catálogo)
-- Products: 'regular', 'superior', 'diésel' (combustible), 'wti' (petróleo). Otro producto (ej. bunker) = inválido.
```

## Memoria persistente de precios (`collector/memoria.py`)
La DB es efímera en CI → sin memoria, cada run "reiniciaba" el historial a la vista de Vercel. El sistema:
1. **Inicio del run** — `importar_memoria()`: restaura `precios` y las tablas del consejo desde `data/db/*.csv` (commitados). Insert-or-ignore: siembra el historial sin pisar valores nuevos.
2. **Colectores** — agregan el día de hoy con su patrón delete-hoy-antes-de-insertar: re-ejecutar un mismo día ACTUALIZA el precio, no duplica (UNIQUE fecha+producto).
3. **Fin del run** — `exportar_memoria()`: vuelca la tabla completa al CSV → se commitea junto con los JSONs; el próximo run parte de ahí. El archivo es determinista (orden fijo por fecha+canon-producto, sin fetched_at): nada cambió = byte-idéntico = cero diff.
- Deduplica grafías (`diessel`/`diésel` del mismo día → 1 fila, la de `fetched_at` más reciente). Productos se guardan canónicos en el CSV.
- CI: `python collector/main.py --memoria --alternos --consenso --petroleo --noticias --export`; el paso de push añade `data/db/*.csv`.

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
- **Una sola vía de escritura**: todo colector usa `db.guardar_precios(conn, filas, fuente)` (upsert por producto+modalidad+fecha; fila sin `modalidad` = la principal). Lecturas (`leer_historial/leer_ultimo/leer_actuales`) devuelven la modalidad PRINCIPAL salvo `modalidad=` o `todas=True`. Re-ejecutar = actualizar; NO borrar antes de insertar. Conflicto entre fuentes el mismo día: gana mayor `prioridad_fuente` (MEM/OilPriceAPI 3 > manual 2 > alternas 1). `sobrescribir=False` = solo sembrar (lo usa `importar_memoria`). Borrar: `borrar_precios(conn, producto, desde, hasta, fuente)`; leer: `leer_historial` / `leer_ultimo` / `leer_actuales` — mismos filtros en todas.
- **Fuentes canónicas**: `canon_fuente()` normaliza (`Ministerio de Energía y Minas`/`MEM HTML`/… → `MEM`). Fuente nueva: agregarla a `db._FUENTES` con su prioridad.
- **Commit before close**: `_ejecutar_modulo` in main.py calls `conn.commit()` before `close()` to flush to disk.

## Consejo de precios (`collector/consenso_precios.py`, flag `--consenso`, en el workflow diario)
- Descubre notas con **Bing News RSS** (`config.json → consejo.consultas`; enlaces directos al medio) + GlobalPetrolPrices. Notas ya leídas (`articulos`) no se reprocesan.
- Extrae cada precio con **LLM (Groq)**: producto, modalidad, tipo (monitoreado/referencia/estimado/historico/otro_pais/maximo_legal), fecha del precio y cita. Solo `monitoreado`/`referencia` con modalidad conocida y fecha coherente entran como `observaciones`. Máx. 8 notas/run con pausa 20 s (Groq free: 8K tokens/min). Sin LLM → `extraer_regex`: conservador (una modalidad por oración, producto/precio alternados sin ambigüedad, sin fechas explícitas ni "estimado/pasó de/US$/otro país").
- Veredicto por producto×modalidad: ventana 7 días, bloque de los últimos 3; de cada medio su dato más reciente + el oficial MEM (tabla precios). Peso = precisión del medio (1/(1+2·error medio vs MEM), 0.2–1; sin historial 0.5) × 0.85^días. Grupo ganador ±Q0.20, precio = mediana ponderada. Confianza alta (≥3 coinciden y ≥60 %, o MEM+otra) / media (2, o solo MEM) / baja (1 no oficial o desacuerdo — NO se escribe en `precios`). Fuente `Consejo` prioridad 2: el MEM siempre gana.
- Serie AS vs SC: GPP "gasoline" = Superior **servicio completo** del MEM (45.74 = SC 21-sep). Nunca mezclar series: los "precios de referencia" semanales del MEM (con subsidio may-jul 2026) son otra cosa.

## Calibración de votos (`collector/calibracion.py`, flag `--calibracion`, opt-in)
Mide la fase de prueba de 7 días del sistema de votos ciudadanos; NO escribe en `precios` ni cambia lo que ve el público. NO está en `--all`, pero SÍ en `daily-update.yml` desde 2026-09-29 (flag `--calibracion` tras el paso `Descargar votos ciudadanos`; sin votos imprime "Aún no hay votos").
- **Entrada**: exports del backend de votos en `data/inbox/votos/*.json|csv` (contrato completo en el docstring del módulo: `id_voto, ts, fecha, producto, modalidad, tipo coincide|otro, precio, precio_mostrado, vio_oficial, dispositivo, zona`). Idempotente por `id_voto`. Un voto inválido se cuenta por motivo (`dispositivo_no_anonimo`, `precio_fuera_de_rango`, `coincide_ciego`, `fecha_incoherente`…), nunca se "arregla". Solo superior/regular/diésel (WTI no).
- **Privacidad (dura)**: `dispositivo` DEBE ser un hash hexadecimal de 12–64 caracteres generado por el backend con sal; IP, correos o uuid con guiones se rechazan. `votos.csv` va a git (público): jamás guardar datos personales. `tests/test_calibracion.py` vigila que el CSV no contenga `@`.
- **Medición** (`medir()` → tabla `calibracion`, una fila por día × producto × modalidad): votos válidos (1 por dispositivo; duplicados y escritos a > Q15 del MEM se descartan y se cuentan), mediana/MAD/acuerdo de los precios escritos, % que confirma ("efecto ancla"), mediana de los votos ciegos (que no vieron el oficial), error vs MEM (`error_mediana`, `error_abs`), estado (`clasificar()`: sin/debil/coincide/cambio), `alerta` vs `cambio_real` del MEM, hora del primer voto y hora en que se alcanzó el mínimo.
- **Referencia = solo precios con fuente `MEM`** del mismo día (no Consejo ni alternas). Sin MEM ese día → `ref_precio` NULL y se mide igual (no falla). OJO: el MEM puede ir atrasado respecto a la calle; una "falsa alarma" es una discrepancia a REVISAR, no necesariamente un error de la comunidad.
- **`reporte()`**: ventana de días CONSECUTIVOS con votos, error medio/sesgo/% de días dentro de tolerancia por producto, matriz de alertas, efecto ancla, curva "error de la mediana vs nº de votos" (bootstrap determinista por semilla) y `votos_recomendados`; veredicto explícito contra `CRITERIOS_SALIDA` (7 días consecutivos, ≥ 10 votos/día, error ≤ Q0.30 en ≥ 80 % de los días). Todos los números de `UMBRALES`/`CRITERIOS_SALIDA` son de ARRANQUE: calibrarlos con esta misma medición.
- **Sincronía**: `clasificar()` es la fuente de verdad; el prototipo `prototipos/dashboard-comunidad.html` la replica en JS y debe mantenerse igual.
- **Persistencia**: `votos` y `calibracion` están en `memoria.TABLAS_MEMORIA` → `data/db/votos.csv` y `data/db/calibracion.csv` (deterministas, en git como el resto de la memoria).
- Los prints de este módulo son ASCII/cp1252-safe (regla de consola del runner); no usar `→ ≥ ±` en prints.

## Servidor de votos (`votos-api/`, Cloudflare Workers + D1)
Backend de los votos ciudadanos (decisión del dueño, 2026-09-29: Cloudflare + D1 por ser gratis, sin tarjeta y con SQL atómico; login NO por ahora). Carpeta aparte con su `package.json`; Vercel no la ve (`.vercelignore` es lista blanca) y NO forma parte de `daily-update.yml` todavía. Detalle y pasos de publicación en `votos-api/README.md`.
- Rutas: `POST /api/voto`, `GET /api/resumen` (caché 30 s), `GET /api/exportar?fecha=` (Bearer `EXPORT_TOKEN`), `GET /api/salud`. El servidor fija fecha/hora GT, hash de dispositivo y `precio_mostrado`; el cliente no puede.
- "Un voto por dispositivo, producto y día" lo garantiza la CLAVE PRIMARIA `dia|producto|modalidad|dispositivo` de D1 (atómico bajo concurrencia; verificado con 10 toques simultáneos en workerd real). Otros KV de Cloudflare NO sirven (consistencia eventual, 1000 escrituras/día).
- Privacidad: solo hashes (`sha256(VOTOS_SALT|disp|token)` y de la IP para un contador horario). Nunca guardar IP, token crudo ni correo. `VOTOS_SALT` y `EXPORT_TOKEN` son secretos de Cloudflare/GitHub, jamás en git; `.dev.vars` (valores de prueba) está fuera de git.
- Paridad de lógica: `votos-api/src/logica.js` replica `collector/calibracion.py` (`clasificar`, validación). El fixture `tests/fixtures/clasificar_casos.json` lo verifican pytest y `node --test`; el export del Worker se valida con `calibracion.validar_voto` real. Si cambias una lógica, actualiza las dos y el fixture.
- Pruebas: `cd votos-api && npm test` (48, D1 simulado) y `npm run test:integracion` (workerd + D1 local reales). Requiere Node ≥ 22 (`node:sqlite`); en la máquina del dueño hay Node 24.
- **PUBLICADO (2026-09-29)**: `https://gasolina-votos.gasolinasogt.workers.dev` (cuenta Cloudflare del dueño; D1 `gasolina-votos` id `d6ba4561-007b-4316-8f48-66f9b7e651c0`, binding `DB`; tablas creadas con `d1 execute --remote`; secretos `VOTOS_SALT` y `EXPORT_TOKEN` cargados por el dueño con `wrangler secret put`). Verificado en producción: salud 200, resumen 200 con `precio_oficial` real, origen ajeno 403, voto inválido 400, exportar sin token 401, CORS 204. NO se hicieron votos de prueba en la D1 real. Un subdominio workers.dev nuevo tarda unos minutos en tener certificado TLS (handshake failure al inicio). En Windows `curl` (schannel) falla el TLS con workers.dev: probar con Node `fetch`.
- Gotcha resuelto: en Workers `fetch` NO puede guardarse en un objeto y llamarse como `deps.fetchFn()` ("Illegal invocation"): el precio oficial quedaba en null sin avisar. Se envuelve `(...a) => fetch(...a)` y `integracion_local.mjs` ahora exige `precio_oficial` numérico.
- **DASHBOARD CONECTADO (2026-09-29)**: `web/index.html` e `index.html` traen el bloque de votos justo bajo "(Área Metropolitana)" (JS al final del archivo, hilo `IIFE` con `API`: `localhost` usa `http://127.0.0.1:8787`, producción usa el Worker), las marcas sobre la gráfica de 30 días (`window.__marcasComunidad`, gancho en `drawWeeklyPriceChartAnimated`) y el aviso de IA arriba de la gráfica. El bloque envuelve `renderPreciosCombustible`. Token anónimo en `localStorage` (`gt_votos_token`), voto del día en `gt_votado`. Si el Worker no responde, el bloque se reemplaza por un mensaje y los precios siguen normales. El aviso de fase de prueba (`FASE_INICIO = 2026-09-29`, 7 días) se oculta solo al terminar. Sin login ni racha por ahora (no prometer lo que no existe).
- **Workflow**: paso `Descargar votos ciudadanos` (`scripts/descargar_votos.py`, `continue-on-error`, baja ayer y hoy de `/api/exportar` a `data/inbox/votos/`, que está en `.gitignore`) y `--calibracion` en el comando principal. Requiere el secreto de GitHub `VOTOS_EXPORT_TOKEN` (mismo valor que `EXPORT_TOKEN` del Worker); sin él el paso se omite sin fallar. `data/db/votos.csv` y `calibracion.csv` los commitea el mismo paso de push (`data/db/*.csv`) y son PÚBLICOS: solo hashes con sal secreta.
- Para probar el dashboard en local: `cd votos-api && npm run db:local` y `npm run dev` (Worker en 8787) + `python serve.py` (sitio en `http://localhost:8089`, el origen debe ser `localhost`, no `127.0.0.1`, por CORS). Si se agrega un dominio nuevo al sitio, sumarlo a `ALLOWED_ORIGINS` en `votos-api/wrangler.toml` y redesplegar.

## Petróleo (OilPriceAPI — solo WTI)
- Endpoint: `https://api.oilpriceapi.com/v1/prices/latest?by_code=WTI_CRUDE_USD` (precio de hoy)
- Historial: `/v1/prices/historical?by_code=WTI_USD&period=past_month&interval=daily` (promedio diario, solo días de mercado). `ejecutar()` lo rellena en cada run → autocorrige días previos. `period=past_year` ≈ 500 días.
- Key vía `OILPRICEAPI_KEY` (secreto GitHub + `.env`, ver `.env.example`)
- OJO: `config.json → petroleo.series.wti = "DCOILWTICO"` es resto de la era EIA, nadie lo lee — no revivir EIA

## LLM noticias (Groq primario desde 2026-09-25; Gemini en pausa por 402)
- `config.json → llm`: `base_url https://api.groq.com/openai`, `model openai/gpt-oss-120b` — OpenAI-compatible vía `_llm_post` (json_object primero, plano si el modelo no lo soporta; POST con header `Authorization: Bearer` OBLIGATORIO sin él da 401 aunque el ping GET /v1/models haya pasado).
- Key por proveedor (`_obtener_api_key(llm_cfg, base_url)`): Gemini nativo → `GEMINI_API_KEY`; OpenAI-compatible → `GROK_API_KEY` (las keys de Gemini NO sirven en ese esquema; si ambas secrets existen y no se discrimina, el POST usa la equivocada). Fallback siempre a `llm.api_key`.
- Verificado desde runner windows-latest (workflow test-apis, 2026-09-25): Groq `/v1/models` → 200 con 11 modelos (gpt-oss-120b/20b, qwen3.8-27b…). El "Groq 401 desde IPs de Actions" era de la key vieja; esta sí responde.
- Gemini `gemini-3.6-flash` da `402 prepayment credits depleted` (free tier agotada) → en pausa hasta renovar key/billing en AI Studio o resetear cuota. Si recupera, revertir config.json a generativelanguage.
- Fallback MyMemory sin key (`_traducir_fallback`, 2 pasadas): 1) titulares ya-español etiquetados GRATIS (titulo_es + categoria + `_relevancia_keywords` — SIEMPRE asignar esas keys o las filas quedan fuera del top-10); 2) EN vía MyMemory hasta agotar cuota (~5000 chars/día). El lote de traducción usa `_seleccion_rotativa(pendientes, por_feed=3, max_total=15)` — NUNCA `pendientes[:15]` (el primer feed ES se comía todo el lote y los EN nunca entraban).
- **Compuerta temática (Etapa 2)**: `es_relevante(titulo, resumen)` DESCARTA lo que no puede mover el precio en GT. Entra si: (a) petróleo/derivados (`_RE_PETROLEO`: crudo, barril, WTI/Brent, OPEP, refinería/refino, oleoducto, gasolina, diésel, GLP, gas natural, tanker… ES/EN/PT); (b) Guatemala + energía/combustible/subsidio/IDP/importación; (c) conflicto (guerra/ataque/sanción/bloqueo/drones) + región productora o ruta (Ormuz, Mar Rojo, Irán, Saudi, Rusia/Ucrania, Venezuela…). Ventana `DIAS_MAX_NOTICIA = 15` (`_dentro_de_ventana`; sin fecha = fuera). Solo se GUARDAN los candidatos (relevantes+recientes); el export vuelve a filtrar (DB local vieja). Ranking: `_relevancia_keywords` (+4 si Guatemala+combustible) con desempate Guatemala → fecha. Export `noticias.top` (10): traducidas primero, luego relevancia y fecha.
- **Una noticia por hecho** (`quitar_duplicados`, tras ordenar y ANTES de traducir): el LLM agrupa en 1 request los top-40 titulares que cuentan el MISMO hecho (cualquier idioma); queda la mejor rankeada. Sin LLM: respaldo léxico estricto (Jaccard de raíces ≥ 0.5, solo casi-copias — comparar palabras no separa 'mismo hecho' de 'mismo tema'). El export vuelve a aplicar el léxico. Pausa de 20 s antes del lote de traducción (Groq 8K tokens/min).
- Metas: `NOTICIAS_EXCELENTES = NOTICIAS_MINIMAS = 10`; export `noticias.top` = 15; dashboard muestra 10 (`agruparNoticias(…, 10)`): máx. 2 por tema es PREFERENCIA, luego rellena.
- Fallback de traducción, pasada 1: nota ya en español usa SU resumen como `resumen_es` (antes la validación la rechazaba y MyMemory gastaba cuota traduciendo ES→ES). `traducirTituloEnEspañol` ya NO inventa frases por patrón: sin `titulo_es` muestra el título original.
- Pipeline IA en `ejecutar()` (orden fijo): 1) VERIFICAR — `_limpiar_texto` limpia HTML/entidades del summary al recibir (raíz del bug Google News que filtraba `<a href>` a resumen_es) + `_item_valido` descarta títulos rotos; 2) ORDENAR — `_ordenar_candidatos` por relevancia determinística (`_relevancia_keywords`, sin costo LLM); 3) TRADUCIR AL FINAL — lote LLM del top-15 rankeado; cada salida pasa por `_validar_cls` (título+descripción presentes, legibles y sin URL/HTML/caracteres ininteligibles — si falla se pasa a la siguiente). Meta: `NOTICIAS_EXCELENTES` (10) fichas sin fallas; si el LLM no las cubre, `_traducir_fallback(objetivo=...)` completa SOLO lo faltante (2 pasadas: ES gratis primero, EN vía MyMemory), y los items rechazados NO reciben categoria/relevancia (no entran al top-10 con nulls).

## XLSX parser (importar_historico.py)
- Fixed column indices: A=FECHA, C=Superior, D=Regular, E=Diésel
- Data rows start at row 8 until metadata/source lines are detected
- Idempotent via insert-or-ignore in DB

## Dashboard data contract (`web/index.html`)
- **Reads from GitHub raw** (not local files): `https://raw.githubusercontent.com/yartoorsdar/gasolina-gt/main/data/export/consolidado.json` (`PRIMARY_DATA_URL`)
- Forma v3 (v2 + `modalidades` + `consejo`): `productos.<codigo>.modalidades.<modalidad> = {nombre, actual, consenso:{fecha,precio,confianza,n_coinciden,n_fuentes,fuentes[]}, historial (30 días)}`; `consejo = {precision_fuentes:{medio:{error_medio,n,peso}}, observaciones_7d, notas_leidas, medios}`. El dashboard es compatible con v2 (sin modalidades → solo autoservicio). Resto igual que v2: `{version, actualizado_at, precios_actualizados, max_fecha_precios, productos:{<codigo>:{nombre, categoria, unidad, orden, actual:{fecha,precio,fuente,fetched_at}|null, historial:[{fecha,precio}] (365 días), mensual:[{anio,mes,promedio}], anual:[{anio,promedio,dias,meses,fuente}]}}, noticias:{total, top[10], llm}}`. `anual.fuente` = `diario` (≥300 días), `MEM mensual` (promedio de meses oficiales; si un mes aún no tiene promedio oficial pero sí diarios, entra con su ÚLTIMO precio registrado → fuente `MEM mensual + último diario`; los diarios se filtran a la modalidad principal) o `consolidado histórico` (semilla).
- `adaptarConsolidado()` en el JS lleva esa forma a lo que usan los renderers (`precios_combustible`, `petroleo`, `wti_historial`, `combustibles_historial`, `ultimas_noticias`, `historial_anual`). La gráfica principal de combustibles muestra los últimos 30 días REALES (ventana por fecha; eje X proporcional a la fecha porque hay tramos semanales y diarios; etiquetas espaciadas ≥34 px) y la de WTI los últimos 7 — nunca arrays escritos a mano (test_web lo vigila).
- `noticias.llm: {titulos_es_hoy, total_hoy}` — diagnóstico del pipeline LLM (ground truth desde DB). Si `titulos_es_hoy` es 0 tras un run, leer el log del job en Actions (`[noticias] Lote OK/Fallback OK/Error LLM`).
- `noticias.top[]` trae `titulo_es`/`categoria`/`relevancia` (1-5, impacto GT)/`resumen_es` del LLM cuando hay key; si no, `relevancia` es null y el dashboard ordena por fecha. Top 5 = `relevancia` desc, luego fecha desc (`agruparNoticias`).
- Semáforo (`analizarImpactoPetrolero`): devuelve `motivo` SEPARADO de `mensaje`; el card lo renderiza como línea aparte (`.semaforo-motivo`: "Motivo: <noticia de mayor peso>"). En móvil/tablet (≤768px) el mensaje se clava a 3 líneas y el motivo a 2 (`-webkit-line-clamp`) para que el rectángulo no ocupe media pantalla.
- **Product names**: canónicos `'superior'`, `'regular'`, `'diésel'`, `'wti'`. `db.canon_producto()` normaliza al insertar (`diessel`/`diesel`→`diésel`, `super`→`superior`). Dashboard también normaliza por si acaso.
- **Sort order**: R, S, D via `Map` (NOT `indexOf()` which is unstable in V8 on Windows).
- **Chart rendering**: `renderHistorial()` MUST be called AFTER `contentEl.style.display = 'block'`. While container is hidden (`display:none`), `getBoundingClientRect()` returns 0×0 and canvas draws at wrong size.
- **Date display**: Takes max `fecha` across all products, NOT `precios[0].fecha` (which could be any product depending on sort order).
- **Time extraction**: si `fetched_at` trae offset local (`-06:00`) se muestra tal cual (`slice(11,16)`); solo se restan 6h si viene en UTC puro (`Z` o `+00:00`). El backend emite GT tz-aware, así que el caso normal es mostrar directo. Do NOT use `new Date()` parsing — the runner clock offset is unreliable.
- **Íconos pixel art animados** (`PX_ICONOS` + `pxIcon(nombre, estilo, clase)`; HTML estático con `<span class="px-slot" data-px=…>` que llena `llenarIconos()` antes de `cargarDashboard()`): 16x16, fotogramas alternados SOLO por opacity (`@keyframes px2/px3/px4`, `steps(1,end)`), apagados con `prefers-reduced-motion`. Se regeneran con `scripts/generar_iconos_pixel.py` (el favicon sale del barril). Nada de emojis en títulos (test_web lo vigila).
- **Semáforo** = SVG completo `svgSemaforo(lamp)` (carcasa, viseras, tornillos, soporte, poste); luz encendida con degradado + reflejo + resplandor difuminado (`.sm-brillo`, pulso de opacity). **Ranking** de noticias `01…10` en degradado (top 3 cálidos, resto plateado). WTI: indicadores 7 días / máx. y mín. 30 días desde `wti_historial_30`. Fecha de precios: "vigentes al <fecha del precio> · datos actualizados <actualizado_at>".
- Estilos nuevos en un 2º `<style>` DESPUÉS de `style-glass.css` (esa hoja se carga después del `<style>` inicial y lo anula). `--text-muted` = #b9c3d1 en tema oscuro.
- **Anti-flicker móvil**: cero animaciones `infinite` en el `<style>` inline salvo de `opacity` (precios y borde del barril son estáticos; pulso y fotogramas de íconos solo vía `opacity`). En `style-glass.css` el fondo mesh y `border-shimmer` se apagan con `@media (max-width:768px),(pointer:coarse)`. Resize con debounce 250ms que redibuja charts en estado final — NUNCA reiniciar `start*Animation` en resize (en móvil cada scroll = resize por la barra del navegador). Un solo listener global, no uno por render.

## Vercel deployment
- `vercel.json`: static build with cache headers for `consolidado.json` (max-age=60) and HTML (max-age=300). OJO: esos headers casi no aplican — el dashboard lee de `raw.githubusercontent.com` (`GITHUB_RAW` en el JS), no de Vercel.
- `_redirects` (`/* /web/index.html 200`) es sintaxis Netlify — Vercel lo ignora. `_routes.json` (sintaxis Azure SWA) también es muerto en Vercel.
- **Web Analytics (visitas)**: `<script defer src="/_vercel/insights/script.js">` + stub `window.va` en el `<head>` de `index.html`, `web/index.html` y `fuentes.html` (lo agregó el PR 1 de Vercel Agent) (ruta absoluta, sirve igual en `/` y `/web/`; en local da 404 inofensivo). Requiere Analytics activado en el dashboard del proyecto `gasolina-gt` (Project → Analytics → Enable). `test_web.py::test_vercel_web_analytics` exige exactamente 1 script por página (2 = visitas contadas doble).
- **Two index.html**: `web/index.html` (source of truth), `index.html` at root (copy for Vercel `/`). Always keep them in sync. La copia raíz usa `sprites/barrel-oil.png` y `iconos/favicon.*` (relativas a raíz); la de `web/` usa `../sprites/`, `../iconos/`.

## GitHub Actions (`daily-update.yml` produce datos; `test-apis.yml` solo diagnostica)
- **daily-update**: cron `0 14 * * *` (nominal 08:00 GT; en la práctica GitHub gratis lo ejecuta ~18:2x UTC), push a main, manual dispatch. Runner `windows-latest`, Python 3.11. Runs `python collector/main.py --memoria --alternos --consenso --petroleo --noticias --export`. El paso de push commitea `data/export/consolidado.json` **y** `data/db/*.csv` (memoria persistente: sin los CSV cada run nacería con DB vacía y perdería el historial).
- **test-apis** (solo `workflow_dispatch`): corre `scripts/test_apis.py` contra el LLM primario de config.json (prompt mínimo), Groq secundario y MyMemory — sin DB ni export ni push. Mismo runner que daily-update: si la API responde ahí, responde en el run diario. Job rojo = ningún LLM completó el prompt (contrato del script).
- `concurrency: daily-update-global` (sin cancel) serializa schedule+push+dispatch.
- **Consola cp1252**: el runner windows-latest escribe la consola en cp1252; un `print()` con un carácter fuera de cp1252 (`→`, `←`, `✓`…) lanza UnicodeEncodeError y tumba el job (pasó 2 veces el 2026-09-25). Defensa doble: `env` a nivel de job `PYTHONIOENCODING: utf-8` + `PYTHONUTF8: '1'`, y `tests/test_consola.py` falla si un print/logger de `collector/` o `scripts/` trae esos caracteres.
- Actions en `@v7` (checkout y setup-python, node24).
- Push step: orden add → diff → **commit → pull --rebase → push HEAD:main**, SIN `|| true` (un rechazo queda rojo, no se pierde en silencio).
- `[skip ci]` en commits de docs/UI para no disparar runs. Sin `[skip ci]` el push dispara el workflow (útil para validar cambios de colectores).
- Regla: ningún workflow hace export parcial + push (con DB efímera eso sobrescribe el dashboard con datos viejos).
- `consolidado.json` trae `precios_actualizados` (bool) + `max_fecha_precios`: guardia de frescura calculada SOLO sobre combustibles (stale si max fecha > 30 días; un WTI de hoy ya no enmascara precios viejos). Si es false tras un run, revisar colectores.

## Scheduler (`collector/scheduler.py`)
```powershell
python scheduler.py --run-all                  # one-shot execution + export
python scheduler.py --schedule 3600            # repeat every N seconds (foreground)
python scheduler.py --export-task NOMBRE --interval-min N  # genera XML (ver --install-task/--uninstall-task)
```

## Testing
- **`tests/conftest.py` aísla TODO test**: redirige `db._default_db_path` y las rutas de `memoria` a tmp. Sin eso, tests con APIs simuladas escribían en `data/historial.db` real (así entró el WTI falso 71.45). No quitarlo.
- Suite principal: `pytest tests/ -q -p no:cacheprovider` — **0 fallas** (219 tests al 2026-09-29; más 48 de votos-api con `node --test`).
- Single test file: `pytest tests/test_module.py -v`
- Tests use `conectar_temporal()` for isolated in-memory DB operations.
- `test_db.py`, `test_memoria.py` y `TestExportJson` cubren el esquema genérico. Partes de `test_consenso.py`/`test_petroleo.py` aún referencian el esquema viejo (dos tablas `precios_combustible`/`precios_petroleo`, `fecha_observacion`, producto `brent`) — pendientes de migrar al esquema tabla-única. No reescribir asserts existentes sin migrar el setup.
- SQLite UNIQUE bug: inline `UNIQUE(...)` in `executescript()` doesn't work on Windows — explicit `CREATE UNIQUE INDEX` required (handled in db.py).

## Key files to read first when debugging
1. `config.json` — central config (tax constants, regimes, source URLs, feeds)
2. `collector/db.py` — database schema and all query helpers
3. `collector/main.py` — orchestrator and JSON export logic
4. `web/index.html` — dashboard HTML/CSS/JS (single file)
5. `PROGRESS.md` — history of decisions and bugs
6. `.opencode/skills/gasolina-gt/SKILL.md` — staged-build rules (hablar español, un stage por ciclo con checkpoint, nunca inventar datos/URLs, PROGRESS.md al día)
