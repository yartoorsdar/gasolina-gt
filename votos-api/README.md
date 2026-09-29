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
