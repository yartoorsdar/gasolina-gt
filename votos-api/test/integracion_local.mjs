// Prueba de integración con el runtime REAL de Cloudflare en local (workerd + D1 local).
// No usa cuenta ni red de Cloudflare; el Worker sí lee el precio oficial de GitHub (público).
//   npm run test:integracion
import { spawn, spawnSync } from 'node:child_process';
import { readFileSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import assert from 'node:assert/strict';

const AQUI = path.dirname(fileURLToPath(import.meta.url));
const API = path.resolve(AQUI, '..');
const RAIZ = path.resolve(API, '..');
const TMP = path.join(API, '.tmp');
const ESTADO = path.join(TMP, 'estado');
const PUERTO = 8799;
const BASE = `http://127.0.0.1:${PUERTO}`;
const ORIGEN = 'http://localhost:8089';
const EXPORT_TOKEN = (readFileSync(path.join(API, '.dev.vars'), 'utf8').match(/^EXPORT_TOKEN=(.+)$/m) || [])[1];
const env = { ...process.env, CI: 'true', WRANGLER_SEND_METRICS: 'false', NO_COLOR: '1' };
const npx = process.platform === 'win32' ? 'npx.cmd' : 'npx';

let ok = 0;
const paso = (msg) => { ok++; console.log(`  [ok] ${msg}`); };

rmSync(TMP, { recursive: true, force: true });
mkdirSync(TMP, { recursive: true });

console.log('[integracion] Creando la base D1 local con schema.sql ...');
const d1 = spawnSync(npx, ['wrangler', 'd1', 'execute', 'gasolina-votos', '--local', '--persist-to', ESTADO, '--file=schema.sql'],
  { cwd: API, env, encoding: 'utf8', shell: process.platform === 'win32' });
if (d1.status !== 0) { console.error(d1.stdout, d1.stderr); process.exit(1); }

console.log('[integracion] Levantando wrangler dev (workerd) ...');
const srv = spawn(npx, ['wrangler', 'dev', '--local', '--test-scheduled', '--port', String(PUERTO), '--persist-to', ESTADO],
  { cwd: API, env, shell: process.platform === 'win32', stdio: ['ignore', 'pipe', 'pipe'] });
let log = '';
srv.stdout.on('data', (d) => { log += d; });
srv.stderr.on('data', (d) => { log += d; });

function apagar() {
  if (process.platform === 'win32') spawnSync('taskkill', ['/pid', String(srv.pid), '/T', '/F'], { stdio: 'ignore' });
  else srv.kill('SIGTERM');
}
process.on('exit', apagar);

async function esperarListo() {
  for (let i = 0; i < 120; i++) {
    try { if ((await fetch(`${BASE}/api/salud`)).ok) return; } catch { /* aun no */ }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error('wrangler dev no arrancó a tiempo:\n' + log.slice(-2000));
}

const post = (cuerpo, ip = '10.1.1.1') => fetch(`${BASE}/api/voto`, {
  method: 'POST', headers: { 'content-type': 'application/json', Origin: ORIGEN, 'CF-Connecting-IP': ip },
  body: JSON.stringify(cuerpo),
});
const tok = (n) => `integ${String(n).padStart(20, '0')}`;

try {
  await esperarListo();
  paso('el Worker arrancó sobre workerd con D1 local');

  // El Cron Trigger (disparador del workflow) está registrado y se ejecuta; sin token no sale a la red
  const cron = await fetch(`${BASE}/__scheduled?cron=52+10+*+*+*`);
  assert.equal(cron.status, 200);
  await new Promise((r) => setTimeout(r, 800));
  assert.ok(log.includes('GITHUB_DISPATCH_TOKEN'), 'el handler scheduled no se ejecutó en workerd');
  paso('el Cron Trigger se ejecuta en workerd (sin token avisa y no llama a GitHub)');

  const pre = await fetch(`${BASE}/api/voto`, { method: 'OPTIONS', headers: { Origin: ORIGEN } });
  assert.equal(pre.status, 204);
  assert.equal(pre.headers.get('access-control-allow-origin'), ORIGEN);
  paso('CORS: el origen permitido recibe sus cabeceras');

  const sinOrigen = await fetch(`${BASE}/api/voto`, { method: 'POST', body: '{}' });
  assert.equal(sinOrigen.status, 403);
  paso('un POST sin origen permitido se rechaza (403)');

  // 10 personas distintas a la vez
  const r10 = await Promise.all(Array.from({ length: 10 }, (_, i) =>
    post({ producto: 'regular', tipo: 'otro', precio: 43.2 + i * 0.01, token: tok(i) }, `10.2.0.${i}`)));
  assert.deepEqual(r10.map((r) => r.status), Array(10).fill(201));
  paso('10 personas votando a la vez: 10 votos guardados (201)');

  // la misma persona pulsando 10 veces a la vez: la clave primaria de D1 debe dejar pasar solo una
  const mismo = await Promise.all(Array.from({ length: 10 }, () => post({ producto: 'superior', tipo: 'coincide', token: tok(99) })));
  const estados = mismo.map((r) => r.status).sort();
  assert.equal(estados.filter((s) => s === 201).length, 1, `estados: ${estados}`);
  assert.equal(estados.filter((s) => s === 409).length, 9, `estados: ${estados}`);
  paso('10 toques simultáneos de la misma persona: solo 1 cuenta, 9 dan 409 (atómico en D1 real)');

  // ráfaga mixta: 40 personas en 3 productos a la vez
  const prods = ['superior', 'regular', 'diésel'];
  const rafaga = await Promise.all(Array.from({ length: 40 }, (_, i) =>
    post({ producto: prods[i % 3], tipo: i % 2 ? 'otro' : 'coincide', ...(i % 2 ? { precio: 44 } : {}), token: tok(1000 + i) }, `10.3.0.${i}`)));
  assert.ok(rafaga.every((r) => r.status === 201 || r.status === 400), `estados: ${rafaga.map((r) => r.status)}`);
  const buenos = rafaga.filter((r) => r.status === 201).length;
  paso(`ráfaga de 40 votos simultáneos: ${buenos} guardados, ${40 - buenos} rechazados por validación (sin errores 5xx)`);

  const resumen = await (await fetch(`${BASE}/api/resumen`, { headers: { Origin: ORIGEN } })).json();
  assert.ok(resumen.productos.regular.n_votos >= 10);
  // El Worker debe LEER el precio oficial de GitHub dentro de workerd (regresion: "Illegal invocation" lo dejaba en null)
  for (const p of ['superior', 'regular', 'diésel']) {
    assert.equal(typeof resumen.productos[p].precio_oficial, 'number', `precio_oficial de ${p} vacío: el Worker no pudo leer consolidado.json`);
  }
  paso(`el Worker lee el precio oficial real: superior=${resumen.productos.superior.precio_oficial}, regular=${resumen.productos.regular.precio_oficial}, diésel=${resumen.productos['diésel'].precio_oficial}`);
  paso(`resumen en vivo: regular=${resumen.productos.regular.n_votos} votos, estado ${resumen.productos.regular.estado}`);

  const sinTok = await fetch(`${BASE}/api/exportar?fecha=${resumen.fecha}`);
  assert.equal(sinTok.status, 401);
  const exp = await fetch(`${BASE}/api/exportar?fecha=${resumen.fecha}`, { headers: { Authorization: `Bearer ${EXPORT_TOKEN}` } });
  assert.equal(exp.status, 200);
  const votos = (await exp.json()).votos;
  const totalEsperado = 10 + 1 + buenos;
  assert.equal(votos.length, totalEsperado);
  paso(`exportación protegida: ${votos.length} votos (esperados ${totalEsperado})`);

  // el export real de workerd debe entrar limpio por el importador de Python
  mkdirSync(path.join(TMP, 'inbox'), { recursive: true });
  writeFileSync(path.join(TMP, 'inbox', 'votos.json'), JSON.stringify({ votos }), 'utf8');
  const py = spawnSync('python', ['-c',
    'import sys, json; sys.path.insert(0, "."); from collector.db import conectar_temporal; from collector.calibracion import importar_votos\n' +
    'c = conectar_temporal(); r = importar_votos(c, sys.argv[1]); print(json.dumps(r))', path.join(TMP, 'inbox')],
  { cwd: RAIZ, encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8', PYTHONUTF8: '1' } });
  assert.equal(py.status, 0, py.stderr);
  const imp = JSON.parse(py.stdout);
  assert.deepEqual(imp.invalidos, {});
  assert.equal(imp.insertados, totalEsperado);
  paso(`Python importó los ${imp.insertados} votos exportados sin rechazar ninguno (contrato compatible)`);

  console.log(`\n[integracion] TODO BIEN: ${ok} comprobaciones sobre workerd + D1 local`);
} catch (e) {
  console.error('\n[integracion] FALLO:', e.message);
  console.error('--- log de wrangler (final) ---\n' + log.slice(-1500));
  process.exitCode = 1;
} finally {
  apagar();
}
