// API de votos ciudadanos de gasolinasogt.com (Cloudflare Worker + D1).
//
//   POST /api/voto       registra un voto (anónimo; 1 por dispositivo, producto y día)
//   GET  /api/resumen    estado de hoy por producto (caché corta de 30 s en el borde)
//   GET  /api/exportar   votos de un día, protegido con EXPORT_TOKEN (lo consume el workflow)
//   GET  /api/salud      comprobación de vida
//
// Privacidad: nunca se guarda IP ni nada personal. El "dispositivo" es un hash con sal
// secreta del token aleatorio que genera el navegador. La IP solo alimenta un contador
// por hora (también hasheada) para frenar abuso.

import { UMBRALES, PRODUCTOS, ahoraGT, clasificar, validarVoto } from './logica.js';

const MAX_CUERPO = 2048;      // bytes: un voto real pesa menos de 300
const TTL_OFICIAL_MS = 5 * 60 * 1000;
let cacheOficial = { t: 0, datos: null };

// ── utilidades ─────────────────────────────────────────────

async function sha256hex(texto) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(texto));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, '0')).join('');
}

function iguales(a, b) {
  // comparación en tiempo casi constante (evita filtrar el token por diferencias de tiempo)
  if (typeof a !== 'string' || typeof b !== 'string' || a.length !== b.length) return false;
  let r = 0;
  for (let i = 0; i < a.length; i++) r |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return r === 0;
}

function encabezadosCors(req, env) {
  const origen = req.headers.get('Origin');
  const permitidos = String(env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).filter(Boolean);
  const h = { Vary: 'Origin' };
  if (origen && permitidos.includes(origen)) {
    h['Access-Control-Allow-Origin'] = origen;
    h['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS';
    h['Access-Control-Allow-Headers'] = 'content-type, authorization';
    h['Access-Control-Max-Age'] = '86400';
  }
  return h;
}

function responder(req, env, estado, cuerpo, extra = {}) {
  return new Response(JSON.stringify(cuerpo), {
    status: estado,
    headers: { 'content-type': 'application/json; charset=utf-8', ...encabezadosCors(req, env), ...extra },
  });
}

function origenPermitido(req, env) {
  const origen = req.headers.get('Origin');
  return !!origen && String(env.ALLOWED_ORIGINS || '').split(',').map((s) => s.trim()).includes(origen);
}

// ── precio oficial del día (para validar rangos y registrar qué veía la persona) ──

async function precioOficial(env, producto, modalidad, deps) {
  if (!env.OFICIAL_URL) return null;
  const ahora = deps.ahora().getTime();
  if (!cacheOficial.datos || ahora - cacheOficial.t > TTL_OFICIAL_MS) {
    try {
      const r = await deps.fetchFn(env.OFICIAL_URL, { signal: AbortSignal.timeout(2500) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      cacheOficial = { t: ahora, datos: await r.json() };
    } catch (err) {
      // sin dato oficial no se rechaza ni se inventa nada: simplemente no hay referencia
      console.error('[votos-api] no se pudo leer el precio oficial:', err?.name, err?.message);
      return null;
    }
  }
  const p = cacheOficial.datos?.productos?.[producto];
  const v = p?.modalidades?.[modalidad]?.actual?.precio ?? (modalidad === 'autoservicio' ? p?.actual?.precio : null);
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

export function _reiniciarCacheOficial() { cacheOficial = { t: 0, datos: null }; }

// ── resumen del día ────────────────────────────────────────

async function calcularResumen(env, fecha, deps) {
  const resumen = {};
  for (const producto of PRODUCTOS) {
    const { results } = await env.DB
      .prepare("SELECT tipo, precio FROM votos WHERE fecha = ?1 AND producto = ?2 AND modalidad = 'autoservicio'")
      .bind(fecha, producto).all();
    const nCoincide = results.filter((r) => r.tipo === 'coincide').length;
    const escritos = results.filter((r) => r.tipo === 'otro').map((r) => r.precio);
    const ref = await precioOficial(env, producto, 'autoservicio', deps);
    const [estado, med] = clasificar(results.length, nCoincide, escritos, ref);
    resumen[producto] = {
      n_votos: results.length, n_coincide: nCoincide, n_otro: escritos.length,
      estado, mediana: estado === 'cambio' || estado === 'respaldado' ? Math.round(med * 100) / 100 : null,
      precio_oficial: ref,
    };
  }
  return resumen;
}

// ── manejadores ────────────────────────────────────────────

async function postVoto(req, env, deps) {
  if (!origenPermitido(req, env)) return responder(req, env, 403, { error: 'origen_no_permitido' });
  if (!env.VOTOS_SALT || String(env.VOTOS_SALT).length < 16) return responder(req, env, 503, { error: 'servidor_sin_configurar' });

  const texto = await req.text();
  if (texto.length > MAX_CUERPO) return responder(req, env, 413, { error: 'cuerpo_demasiado_grande' });
  let cuerpo;
  try { cuerpo = JSON.parse(texto); } catch { return responder(req, env, 400, { error: 'json_invalido' }); }

  const { voto, error } = validarVoto(cuerpo);
  if (error) return responder(req, env, 400, { error });

  const { fecha, ts } = ahoraGT(deps.ahora());

  // Freno de abuso por IP (hasheada, ventana de una hora). Límite generoso: muchas
  // personas comparten IP en redes móviles.
  const ip = req.headers.get('CF-Connecting-IP') || req.headers.get('x-forwarded-for') || 'sin-ip';
  const claveIp = (await sha256hex(`${env.VOTOS_SALT}|ip|${ip}`)).slice(0, 16);
  const ventana = ts.slice(0, 13);
  const fila = await env.DB
    .prepare('INSERT INTO limites (clave, ventana, n) VALUES (?1, ?2, 1) ON CONFLICT(clave, ventana) DO UPDATE SET n = n + 1 RETURNING n')
    .bind(claveIp, ventana).first();
  if (fila.n === 1) await env.DB.prepare('DELETE FROM limites WHERE ventana < ?1').bind(ventana).run();
  if (fila.n > Number(env.LIMITE_IP_HORA || 120)) return responder(req, env, 429, { error: 'demasiados_intentos' }, { 'Retry-After': '3600' });

  const dispositivo = (await sha256hex(`${env.VOTOS_SALT}|disp|${voto.token}`)).slice(0, 32);
  const oficial = await precioOficial(env, voto.producto, voto.modalidad, deps);
  if (voto.tipo === 'otro' && oficial !== null && Math.abs(voto.precio - oficial) > UMBRALES.max_desvio_ref) {
    return responder(req, env, 400, { error: 'precio_lejos_del_oficial' });
  }

  // La clave primaria (día|producto|modalidad|dispositivo) hace atómica la regla
  // "un voto por dispositivo, producto y día": dos toques simultáneos no pueden colarse.
  const idVoto = `${fecha}|${voto.producto}|${voto.modalidad}|${dispositivo}`;
  const res = await env.DB.prepare(
    'INSERT INTO votos (id_voto, fecha, producto, modalidad, tipo, precio, precio_mostrado, vio_oficial, dispositivo, zona, ts) ' +
    'VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11) ON CONFLICT(id_voto) DO NOTHING',
  ).bind(idVoto, fecha, voto.producto, voto.modalidad, voto.tipo, voto.precio,
         voto.vio_oficial ? oficial : null, voto.vio_oficial, dispositivo, voto.zona, ts).run();
  if (!res.meta.changes) return responder(req, env, 409, { error: 'ya_votaste_hoy', producto: voto.producto });

  return responder(req, env, 201, { ok: true, resumen: await calcularResumen(env, fecha, deps) });
}

async function getResumen(req, env, deps) {
  const { fecha, ts } = ahoraGT(deps.ahora());
  return responder(req, env, 200, { fecha, generado_at: ts, productos: await calcularResumen(env, fecha, deps) },
    { 'Cache-Control': 'public, max-age=15, s-maxage=30' });
}

async function getExportar(req, env, url) {
  if (!env.EXPORT_TOKEN || String(env.EXPORT_TOKEN).length < 16) return responder(req, env, 503, { error: 'servidor_sin_configurar' });
  const auth = req.headers.get('Authorization') || '';
  if (!iguales(auth, `Bearer ${env.EXPORT_TOKEN}`)) return responder(req, env, 401, { error: 'no_autorizado' });
  const fecha = url.searchParams.get('fecha') || '';
  if (!/^\d{4}-\d{2}-\d{2}$/.test(fecha)) return responder(req, env, 400, { error: 'fecha_invalida' });
  const { results } = await env.DB.prepare(
    'SELECT id_voto, ts, fecha, producto, modalidad, tipo, precio, precio_mostrado, vio_oficial, dispositivo, zona ' +
    'FROM votos WHERE fecha = ?1 ORDER BY ts, id_voto',
  ).bind(fecha).all();
  return responder(req, env, 200, { votos: results }, { 'Cache-Control': 'no-store' });
}

// ── enrutador ──────────────────────────────────────────────

export async function manejar(req, env, deps = {}) {
  // OJO: en Workers `fetch` debe llamarse suelto; guardarlo en un objeto y llamarlo como
  // `deps.fetchFn(...)` lanza "Illegal invocation" (así falló el precio oficial en produccion).
  deps = { ahora: () => new Date(), fetchFn: (...args) => fetch(...args), ...deps };
  const url = new URL(req.url);
  try {
    if (req.method === 'OPTIONS') return new Response(null, { status: 204, headers: encabezadosCors(req, env) });
    if (url.pathname === '/api/salud' && req.method === 'GET') return responder(req, env, 200, { ok: true });
    if (url.pathname === '/api/voto' && req.method === 'POST') return await postVoto(req, env, deps);
    if (url.pathname === '/api/resumen' && req.method === 'GET') return await getResumen(req, env, deps);
    if (url.pathname === '/api/exportar' && req.method === 'GET') return await getExportar(req, env, url);
    return responder(req, env, 404, { error: 'no_encontrado' });
  } catch (err) {
    console.error('[votos-api] error interno:', err?.message); // sin datos del voto ni de la persona
    return responder(req, env, 500, { error: 'error_interno' });
  }
}

export default {
  fetch: (req, env) => manejar(req, env),
};
