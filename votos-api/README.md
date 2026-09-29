# votos-api — servidor de votos ciudadanos (Cloudflare Workers + D1)

API del bloque "comparte cuánto pagaste" de gasolinasogt.com. Anónima (sin login), un voto por
dispositivo, producto y día. Corre gratis en Cloudflare Workers con D1 (SQLite). Vive en su propia
carpeta: Vercel NO la ve (`.vercelignore` es lista blanca) y no toca el sitio ni el workflow diario.

## Rutas
| Ruta | Qué hace |
|---|---|
| `POST /api/voto` | Registra un voto. Cuerpo: `{producto, tipo: coincide\|otro, precio?, vio_oficial?, zona?, token}`. 201 ok · 400 inválido · 403 origen no permitido · 409 ya votó hoy · 429 demasiados intentos |
| `GET /api/resumen` | Estado de hoy por producto (sin / debil / coincide / cambio). Caché de 30 s en el borde |
| `GET /api/exportar?fecha=YYYY-MM-DD` | Votos del día en el contrato de `collector/calibracion.py`. Exige `Authorization: Bearer <EXPORT_TOKEN>` |
| `GET /api/salud` | Comprobación de vida |

El servidor fija la fecha, la hora, el hash del dispositivo y el `precio_mostrado` (el oficial que
él mismo conoce): el navegador no puede falsearlos. `token` es un texto aleatorio de 16–64
caracteres que genera el navegador; el servidor guarda solo `sha256(sal | token)` truncado.

## Probar en local (sin cuenta ni claves)
```powershell
cd votos-api
npm install                 # una vez
npm test                    # 48 pruebas rápidas (D1 simulado)
npm run test:integracion    # workerd + D1 local reales: 10 votos simultáneos, atomicidad, export -> Python
npm run db:local            # crea la D1 local con schema.sql
npm run dev                 # http://127.0.0.1:8787  (usa .dev.vars con valores de PRUEBA)
```

## Pasos para publicarlo (los haces tú: cuenta y claves)
1. `npx wrangler login` — abre el navegador para entrar a tu cuenta de Cloudflare (gratuita, sin tarjeta).
2. `npx wrangler d1 create gasolina-votos` — copia el `database_id` que imprime a `wrangler.toml`.
3. `npx wrangler d1 execute gasolina-votos --remote --file=schema.sql`
4. Genera dos secretos largos y guárdalos: `node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"`
   - `npx wrangler secret put VOTOS_SALT` (la sal: si se pierde o cambia, los dispositivos de días anteriores dejan de coincidir; guárdala)
   - `npx wrangler secret put EXPORT_TOKEN` (y el mismo valor como secreto de GitHub `VOTOS_EXPORT_TOKEN`)
5. `npx wrangler deploy` — imprime la URL `https://gasolina-votos.<tu-subdominio>.workers.dev`.
6. Si el sitio se sirve desde otro dominio, agrégalo a `ALLOWED_ORIGINS` en `wrangler.toml` y vuelve a desplegar.

Los secretos NUNCA van en `wrangler.toml` ni en git. `.dev.vars` (solo local) está en `.gitignore`.

## Límites gratuitos a vigilar (verificar en las páginas oficiales de Cloudflare)
Workers: ~100 mil solicitudes/día. D1: ~100 mil filas escritas/día y 5 millones leídas/día; desde el
2026-09-01 D1 falla (no cobra) al pasar el límite diario. El resumen se cachea 30 s para gastar poco.
El freno por IP (`LIMITE_IP_HORA`, 120) es generoso porque muchas personas comparten IP en redes móviles.

## Login opcional a futuro
La tabla `votos` ya trae `usuario_id` (vacío). Un login opcional (ej. Google) se agrega verificando
un token en el Worker y enlazando el `dispositivo` anónimo con la cuenta; no requiere migrar datos.

## Disparador puntual del workflow diario (Cron Trigger)
GitHub retrasa 4-6 h sus crons gratuitos, así que este Worker dispara `daily-update.yml` a las
**04:52 y 11:52 hora de Guatemala** (`[triggers]` en `wrangler.toml`, 10:52 y 17:52 UTC). Los datos
quedan publicados hacia las 05:00 y las 12:00. El cron de GitHub queda solo como red de seguridad.

Pasos (los haces tú; el token es tuyo y nunca se pega en el repositorio ni en el chat):
1. GitHub -> tu foto -> Settings -> Developer settings -> Personal access tokens -> **Fine-grained tokens** -> Generate new token.
2. Resource owner: tu cuenta. Repository access: **Only select repositories** -> `gasolina-gt`.
3. Repository permissions -> **Actions: Read and write** (Metadata: Read-only se agrega solo). Expiración: la más larga que permita;
   anota la fecha, porque al caducar el disparador deja de funcionar (el cron de GitHub sigue como respaldo, pero tardío).
4. `cd votos-api` y `npx wrangler secret put GITHUB_DISPATCH_TOKEN` (pega el token cuando lo pida).
5. `npx wrangler deploy` (registra los horarios; `[triggers]` ya está en `wrangler.toml`).
6. Comprobar al día siguiente: `gh run list --workflow daily-update.yml --event workflow_dispatch --limit 4` debe mostrar runs creados a las 10:52 y 17:52 UTC.
   Sin el secreto el Worker solo escribe en su log "falta el secreto GITHUB_DISPATCH_TOKEN" y no hace nada.

