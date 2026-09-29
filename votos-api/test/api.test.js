// Pruebas del Worker de votos: reglas, privacidad, seguridad y concurrencia.
// Se llama a `manejar()` directamente con un D1 simulado; nada toca la red ni datos reales.
import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { crearD1 } from './d1_shim.js';
import { manejar, _reiniciarCacheOficial } from '../src/index.js';

const ORIGEN = 'http://localhost:8089';
const AHORA = new Date('2026-09-29T16:00:00Z'); // 10:00 en Guatemala
const OFICIALES = { superior: 45.29, regular: 43.29, 'diésel': 49.4 };
const RAIZ = fileURLToPath(new URL('../../', import.meta.url));

function entorno(extra = {}) {
  _reiniciarCacheOficial();
  return {
    DB: crearD1(), ALLOWED_ORIGINS: `${ORIGEN},https://gasolinasogt.com`,
    OFICIAL_URL: 'https://oficial.test/consolidado.json', LIMITE_IP_HORA: '120',
    VOTOS_SALT: 'sal-de-prueba-solo-local-1234567890', EXPORT_TOKEN: 'token-de-prueba-solo-local-1234567890',
    ...extra,
  };
}

const oficialOk = async () => Response.json({
  productos: Object.fromEntries(Object.entries(OFICIALES).map(([p, v]) => [p, { modalidades: { autoservicio: { actual: { precio: v } } } }])),
});
const oficialCaido = async () => { throw new Error('sin red'); };

const tok = (i) => `tok${String(i).padStart(20, '0')}`;

async function llamar(env, metodo, ruta, { cuerpo, origen = ORIGEN, ip = '10.0.0.1', headers = {}, ahora = AHORA, fetchFn = oficialOk } = {}) {
  const h = { ...headers };
  if (origen) h.Origin = origen;
  if (ip) h['CF-Connecting-IP'] = ip;
  if (cuerpo !== undefined) h['content-type'] = 'application/json';
  const req = new Request(`https://api.test${ruta}`, {
    method: metodo, headers: h,
    body: cuerpo === undefined ? undefined : (typeof cuerpo === 'string' ? cuerpo : JSON.stringify(cuerpo)),
  });
  const res = await manejar(req, env, { ahora: () => ahora, fetchFn });
  const texto = await res.text();
  return { res, estado: res.status, json: texto ? JSON.parse(texto) : null, texto };
}

const votar = (env, i, extra = {}, opts = {}) =>
  llamar(env, 'POST', '/api/voto', { cuerpo: { producto: 'regular', tipo: 'coincide', token: tok(i), ...extra }, ...opts });

// ── básicos ────────────────────────────────────────────────

test('salud responde', async () => {
  const { estado, json } = await llamar(entorno(), 'GET', '/api/salud');
  assert.equal(estado, 200);
  assert.deepEqual(json, { ok: true });
});

test('ruta desconocida da 404', async () => {
  assert.equal((await llamar(entorno(), 'GET', '/api/otra')).estado, 404);
});

test('un voto "coincide" se guarda y devuelve el resumen', async () => {
  const env = entorno();
  const { estado, json } = await votar(env, 1);
  assert.equal(estado, 201);
  assert.equal(json.resumen.regular.n_votos, 1);
  assert.equal(json.resumen.regular.estado, 'sin');
  assert.equal(json.resumen.superior.n_votos, 0);
});

test('el servidor fija fecha, hora, dispositivo y precio mostrado (el cliente no puede)', async () => {
  const env = entorno();
  const { estado } = await votar(env, 2, { tipo: 'otro', precio: 43.5, fecha: '1999-01-01', ts: 'x', dispositivo: 'aaaa', precio_mostrado: 1 });
  assert.equal(estado, 201);
  const fila = await env.DB.prepare('SELECT * FROM votos').first();
  assert.equal(fila.fecha, '2026-09-29');
  assert.equal(fila.ts, '2026-09-29T10:00:00-06:00');
  assert.match(fila.dispositivo, /^[0-9a-f]{32}$/);
  assert.notEqual(fila.dispositivo, tok(2));
  assert.equal(fila.precio_mostrado, 43.29);   // el oficial que el SERVIDOR conoce, no lo que diga el cliente
  assert.equal(fila.vio_oficial, 1);
  assert.equal(fila.precio, 43.5);
  assert.equal(fila.modalidad, 'autoservicio');
});

// ── una persona, un voto por producto y día ────────────────

test('votar dos veces el mismo producto el mismo día da 409; otro producto sí se puede', async () => {
  const env = entorno();
  assert.equal((await votar(env, 3)).estado, 201);
  const otra = await votar(env, 3);
  assert.equal(otra.estado, 409);
  assert.equal(otra.json.error, 'ya_votaste_hoy');
  assert.equal((await votar(env, 3, { producto: 'superior' })).estado, 201);
});

test('al día siguiente la misma persona puede votar otra vez', async () => {
  const env = entorno();
  assert.equal((await votar(env, 4)).estado, 201);
  const manana = new Date(AHORA.getTime() + 24 * 3600 * 1000);
  assert.equal((await votar(env, 4, {}, { ahora: manana })).estado, 201);
});

test('el cambio de día usa la hora de Guatemala, no la UTC', async () => {
  const env = entorno();
  // 03:00 UTC del 30 = 21:00 del 29 en Guatemala: sigue siendo el día 29
  const noche = new Date('2026-09-30T03:00:00Z');
  assert.equal((await votar(env, 5, {}, { ahora: noche })).estado, 201);
  assert.equal((await votar(env, 5, {}, { ahora: AHORA })).estado, 409);   // mismo día GT (29)
});

// ── validación ─────────────────────────────────────────────

const INVALIDOS = [
  [{ producto: 'wti' }, 'producto_invalido'],
  [{ producto: 'bunker' }, 'producto_invalido'],
  [{ tipo: 'quizas' }, 'tipo_invalido'],
  [{ modalidad: 'spot' }, 'modalidad_invalida'],
  [{ token: 'corto' }, 'token_invalido'],
  [{ token: 'persona@correo.com-xxxxxxxxxxxx' }, 'token_invalido'],
  [{ tipo: 'otro' }, 'precio_no_numerico'],
  [{ tipo: 'otro', precio: 'abc' }, 'precio_no_numerico'],
  [{ tipo: 'otro', precio: 9.99 }, 'precio_fuera_de_rango'],
  [{ tipo: 'otro', precio: 100.5 }, 'precio_fuera_de_rango'],
  [{ tipo: 'coincide', precio: 43.29 }, 'coincide_con_precio'],
  [{ tipo: 'coincide', vio_oficial: 0 }, 'coincide_ciego'],
];
for (const [cambio, motivo] of INVALIDOS) {
  test(`voto inválido se rechaza: ${JSON.stringify(cambio)} -> ${motivo}`, async () => {
    const r = await votar(entorno(), 6, cambio);
    assert.equal(r.estado, 400);
    assert.equal(r.json.error, motivo);
  });
}

test('json roto da 400 y cuerpo enorme da 413', async () => {
  const env = entorno();
  assert.equal((await llamar(env, 'POST', '/api/voto', { cuerpo: '{no es json' })).json.error, 'json_invalido');
  const grande = await llamar(env, 'POST', '/api/voto', { cuerpo: JSON.stringify({ x: 'a'.repeat(3000) }) });
  assert.equal(grande.estado, 413);
});

test('un precio escrito muy lejos del oficial se rechaza; sin dato oficial no se inventa nada', async () => {
  const env = entorno();
  const lejos = await votar(env, 7, { tipo: 'otro', precio: 65 });
  assert.equal(lejos.estado, 400);
  assert.equal(lejos.json.error, 'precio_lejos_del_oficial');
  const sinOficial = await votar(entorno(), 7, { tipo: 'otro', precio: 65 }, { fetchFn: oficialCaido });
  assert.equal(sinOficial.estado, 201);
});

test('voto ciego: no guarda precio_mostrado', async () => {
  const env = entorno();
  assert.equal((await votar(env, 8, { tipo: 'otro', precio: 43.1, vio_oficial: 0 })).estado, 201);
  const f = await env.DB.prepare('SELECT vio_oficial, precio_mostrado FROM votos').first();
  assert.equal(f.vio_oficial, 0);
  assert.equal(f.precio_mostrado, null);
});

// ── seguridad ──────────────────────────────────────────────

test('origen no permitido o ausente da 403; el permitido recibe cabeceras CORS', async () => {
  const env = entorno();
  assert.equal((await votar(env, 9, {}, { origen: 'https://malo.example' })).estado, 403);
  assert.equal((await votar(env, 9, {}, { origen: null })).estado, 403);
  const ok = await votar(env, 9);
  assert.equal(ok.res.headers.get('Access-Control-Allow-Origin'), ORIGEN);
  const pre = await llamar(env, 'OPTIONS', '/api/voto');
  assert.equal(pre.estado, 204);
  assert.match(pre.res.headers.get('Access-Control-Allow-Methods'), /POST/);
});

test('sin sal secreta el servidor se niega a operar', async () => {
  const r = await votar(entorno({ VOTOS_SALT: '' }), 10);
  assert.equal(r.estado, 503);
});

test('freno por IP: pasado el límite da 429, otra IP no se afecta y la hora siguiente se reinicia', async () => {
  const env = entorno({ LIMITE_IP_HORA: '5' });
  for (let i = 0; i < 5; i++) assert.equal((await votar(env, 100 + i)).estado, 201);
  const bloqueado = await votar(env, 110);
  assert.equal(bloqueado.estado, 429);
  assert.equal(bloqueado.res.headers.get('Retry-After'), '3600');
  assert.equal((await votar(env, 111, {}, { ip: '10.0.0.2' })).estado, 201);
  const proxHora = new Date(AHORA.getTime() + 3600 * 1000);
  assert.equal((await votar(env, 112, {}, { ahora: proxHora })).estado, 201);
});

// ── privacidad ─────────────────────────────────────────────

test('la base no guarda el token, ni la IP, ni el correo', async () => {
  const env = entorno();
  await votar(env, 12, { tipo: 'otro', precio: 43.3, zona: 'zona 10' }, { ip: '190.86.10.4' });
  const filas = (await env.DB.prepare('SELECT * FROM votos').all()).results;
  const limites = (await env.DB.prepare('SELECT * FROM limites').all()).results;
  const volcado = JSON.stringify(filas) + JSON.stringify(limites);
  assert.ok(!volcado.includes(tok(12)), 'el token crudo no debe guardarse');
  assert.ok(!volcado.includes('190.86.10.4'), 'la IP no debe guardarse');
  assert.match(limites[0].clave, /^[0-9a-f]{16}$/);
});

// ── exportación ────────────────────────────────────────────

test('exportar exige el token secreto y una fecha válida', async () => {
  const env = entorno();
  const sinAuth = await llamar(env, 'GET', '/api/exportar?fecha=2026-09-29', { origen: null });
  assert.equal(sinAuth.estado, 401);
  const mal = await llamar(env, 'GET', '/api/exportar?fecha=2026-09-29', { origen: null, headers: { Authorization: 'Bearer otro-token-cualquiera-123456' } });
  assert.equal(mal.estado, 401);
  const h = { Authorization: `Bearer ${env.EXPORT_TOKEN}` };
  assert.equal((await llamar(env, 'GET', '/api/exportar?fecha=ayer', { origen: null, headers: h })).estado, 400);
  assert.equal((await llamar(entorno({ EXPORT_TOKEN: '' }), 'GET', '/api/exportar?fecha=2026-09-29', { origen: null })).estado, 503);
});

test('el export cumple el contrato que lee collector/calibracion.py (validado con Python real)', async () => {
  const env = entorno();
  for (let i = 0; i < 6; i++) await votar(env, 200 + i, { producto: 'regular', tipo: i % 2 ? 'otro' : 'coincide', precio: i % 2 ? 43.2 : undefined });
  await votar(env, 300, { producto: 'diésel', tipo: 'otro', precio: 49.1, vio_oficial: 0, zona: 'zona 1' });
  const r = await llamar(env, 'GET', '/api/exportar?fecha=2026-09-29', { origen: null, headers: { Authorization: `Bearer ${env.EXPORT_TOKEN}` } });
  assert.equal(r.estado, 200);
  assert.equal(r.res.headers.get('Cache-Control'), 'no-store');
  assert.equal(r.json.votos.length, 7);
  assert.deepEqual(Object.keys(r.json.votos[0]).sort(),
    ['dispositivo', 'fecha', 'id_voto', 'modalidad', 'precio', 'precio_mostrado', 'producto', 'tipo', 'ts', 'vio_oficial', 'zona']);

  const py = spawnSync('python', ['-c',
    'import sys, json; sys.path.insert(0, "."); from collector.calibracion import validar_voto\n' +
    'v = json.load(sys.stdin)["votos"]; malos = [x["id_voto"] for x in v if validar_voto(x)[1]]\n' +
    'print(json.dumps({"n": len(v), "malos": malos}))'],
  { cwd: RAIZ, input: r.texto, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
  if (py.error) { console.log('   (python no disponible: se omite la validación cruzada)'); return; }
  assert.equal(py.status, 0, py.stderr);
  assert.deepEqual(JSON.parse(py.stdout), { n: 7, malos: [] });
});

// ── resumen ────────────────────────────────────────────────

test('el resumen se calcula con la misma lógica y trae caché corta', async () => {
  const env = entorno();
  const precios = [38.6, 38.7, 38.65, 38.75, 38.7, 38.68, 38.72, 38.66, 38.71, 38.69, 38.7, 38.67];
  for (let i = 0; i < precios.length; i++) await votar(env, 400 + i, { tipo: 'otro', precio: precios[i] }, { fetchFn: oficialOk });
  // el oficial es 43.29 y la comunidad reporta ~38.69: posible cambio (pero el voto lejano >15 no aplica: la diferencia es 4.6)
  const r = await llamar(env, 'GET', '/api/resumen');
  assert.equal(r.estado, 200);
  assert.equal(r.res.headers.get('Cache-Control'), 'public, max-age=15, s-maxage=30');
  assert.equal(r.json.fecha, '2026-09-29');
  assert.equal(r.json.productos.regular.n_votos, 12);
  assert.equal(r.json.productos.regular.estado, 'cambio');
  assert.ok(Math.abs(r.json.productos.regular.mediana - 38.69) < 0.05);
  assert.equal(r.json.productos.regular.precio_oficial, 43.29);
  assert.equal(r.json.productos.superior.estado, 'sin');
});

// ── concurrencia ───────────────────────────────────────────

test('10 personas distintas votando a la vez: los 10 votos quedan guardados', async () => {
  const env = entorno();
  const r = await Promise.all(Array.from({ length: 10 }, (_, i) => votar(env, 500 + i, { tipo: 'otro', precio: 43.2 + i * 0.01 })));
  assert.ok(r.every((x) => x.estado === 201), JSON.stringify(r.map((x) => x.estado)));
  const n = (await env.DB.prepare("SELECT COUNT(*) AS n FROM votos WHERE producto = 'regular'").first()).n;
  assert.equal(n, 10);
});

test('10 toques simultáneos de la misma persona: solo uno cuenta', async () => {
  const env = entorno();
  const r = await Promise.all(Array.from({ length: 10 }, () => votar(env, 600)));
  assert.equal(r.filter((x) => x.estado === 201).length, 1);
  assert.equal(r.filter((x) => x.estado === 409).length, 9);
  assert.equal((await env.DB.prepare('SELECT COUNT(*) AS n FROM votos').first()).n, 1);
});
