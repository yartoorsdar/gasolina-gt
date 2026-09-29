// Disparador puntual del workflow (Cron Trigger): llama a la API de GitHub sin filtrar el token.
import test from 'node:test';
import assert from 'node:assert/strict';
import worker, { dispararWorkflow } from '../src/index.js';

const TOKEN = 'github_pat_token-de-prueba-solo-local-1234567890';
const sinPausa = async () => {};

function espia() {
  const llamadas = [];
  return { llamadas, fn: async (url, opts) => { llamadas.push({ url, opts }); return respuestas.shift(); } };
}
let respuestas = [];

function capturarErrores() {
  const original = console.error;
  const lineas = [];
  console.error = (...a) => lineas.push(a.map(String).join(' '));
  return { lineas, restaurar: () => { console.error = original; } };
}

test('sin token no llama a GitHub y lo dice en el log', async () => {
  const e = espia(); const log = capturarErrores();
  const r = await dispararWorkflow({}, { fetchFn: e.fn, esperar: sinPausa });
  log.restaurar();
  assert.deepEqual(r, { ok: false, motivo: 'sin_token' });
  assert.equal(e.llamadas.length, 0);
  assert.ok(log.lineas.some((l) => l.includes('GITHUB_DISPATCH_TOKEN')));
});

test('con token dispara daily-update.yml en main con la API correcta', async () => {
  respuestas = [new Response(null, { status: 204 })];
  const e = espia();
  const r = await dispararWorkflow({ GITHUB_DISPATCH_TOKEN: TOKEN }, { fetchFn: e.fn, esperar: sinPausa });
  assert.deepEqual(r, { ok: true, estado: 204, intentos: 1 });
  const { url, opts } = e.llamadas[0];
  assert.equal(url, 'https://api.github.com/repos/yartoorsdar/gasolina-gt/actions/workflows/daily-update.yml/dispatches');
  assert.equal(opts.method, 'POST');
  assert.equal(opts.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(opts.headers['X-GitHub-Api-Version'], '2022-11-28');
  assert.ok(opts.headers['User-Agent']);                       // GitHub rechaza peticiones sin User-Agent
  assert.deepEqual(JSON.parse(opts.body), { ref: 'main' });
});

test('repositorio, workflow y rama salen de las variables si se definen', async () => {
  respuestas = [new Response(null, { status: 204 })];
  const e = espia();
  await dispararWorkflow({ GITHUB_DISPATCH_TOKEN: TOKEN, GITHUB_REPO: 'a/b', GITHUB_WORKFLOW: 'x.yml', GITHUB_REF: 'dev' },
    { fetchFn: e.fn, esperar: sinPausa });
  assert.equal(e.llamadas[0].url, 'https://api.github.com/repos/a/b/actions/workflows/x.yml/dispatches');
  assert.deepEqual(JSON.parse(e.llamadas[0].opts.body), { ref: 'dev' });
});

test('un 401/403/404 no se reintenta (no se arregla reintentando)', async () => {
  respuestas = [new Response('{}', { status: 401 })];
  const e = espia(); const log = capturarErrores();
  const r = await dispararWorkflow({ GITHUB_DISPATCH_TOKEN: TOKEN }, { fetchFn: e.fn, esperar: sinPausa });
  log.restaurar();
  assert.equal(r.ok, false); assert.equal(r.motivo, 'http_401');
  assert.equal(e.llamadas.length, 1);
});

test('un 5xx se reintenta una vez y si funciona el resultado es ok', async () => {
  respuestas = [new Response('x', { status: 502 }), new Response(null, { status: 204 })];
  const e = espia(); const log = capturarErrores();
  const r = await dispararWorkflow({ GITHUB_DISPATCH_TOKEN: TOKEN }, { fetchFn: e.fn, esperar: sinPausa });
  log.restaurar();
  assert.deepEqual(r, { ok: true, estado: 204, intentos: 2 });
  assert.equal(e.llamadas.length, 2);
});

test('si la red falla dos veces devuelve error sin lanzar excepción', async () => {
  let n = 0; const log = capturarErrores();
  const r = await dispararWorkflow({ GITHUB_DISPATCH_TOKEN: TOKEN }, { fetchFn: async () => { n++; throw new TypeError('sin red'); }, esperar: sinPausa });
  log.restaurar();
  assert.deepEqual(r, { ok: false, motivo: 'red' });
  assert.equal(n, 2);
});

test('el token nunca aparece en los logs, ni siquiera en los errores', async () => {
  const log = capturarErrores();
  respuestas = [new Response('{"message":"Bad credentials"}', { status: 401 })];
  await dispararWorkflow({ GITHUB_DISPATCH_TOKEN: TOKEN }, { fetchFn: espia().fn, esperar: sinPausa });
  await dispararWorkflow({ GITHUB_DISPATCH_TOKEN: TOKEN }, { fetchFn: async () => { throw new Error(`fallo con ${TOKEN}`); }, esperar: sinPausa });
  log.restaurar();
  assert.ok(log.lineas.length > 0);
  assert.ok(!log.lineas.join('\n').includes(TOKEN), 'el token se filtró al log');
});

test('el handler scheduled del Worker dispara el workflow mediante waitUntil', async () => {
  const log = capturarErrores();
  let promesa = null;
  worker.scheduled({ cron: '52 10 * * *' }, {}, { waitUntil: (p) => { promesa = p; } });   // sin token: no sale a la red
  assert.ok(promesa instanceof Promise);
  assert.deepEqual(await promesa, { ok: false, motivo: 'sin_token' });
  log.restaurar();
});

test('el Worker sigue exponiendo fetch además de scheduled', () => {
  assert.equal(typeof worker.fetch, 'function');
  assert.equal(typeof worker.scheduled, 'function');
});
