// Paridad con Python: los mismos casos de tests/fixtures/clasificar_casos.json que verifica
// pytest (collector/calibracion.py). Si una lógica cambia y la otra no, esto falla.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { clasificar, validarVoto, ahoraGT, mediana, referenciaSinImpuestos } from '../src/logica.js';

const CASOS = JSON.parse(readFileSync(new URL('../../tests/fixtures/clasificar_casos.json', import.meta.url), 'utf8'));

for (const c of CASOS) {
  test(`clasificar (paridad con Python): ${c.nombre}`, () => {
    const [estado, med] = clasificar(c.n_votos, c.n_coincide, c.escritos, c.ref, c.externo);
    assert.equal(estado, c.estado);
    if (c.mediana === null) assert.equal(med, null);
    else assert.ok(Math.abs(med - c.mediana) < 1e-3, `${med} vs ${c.mediana}`);
  });
}

test('mediana de par e impar', () => {
  assert.equal(mediana([3, 1, 2]), 2);
  assert.equal(mediana([4, 1, 3, 2]), 2.5);
  assert.equal(mediana([]), null);
});

test('ahoraGT resta 6 horas sin horario de verano', () => {
  assert.deepEqual(ahoraGT(new Date('2026-09-29T16:00:00Z')), { fecha: '2026-09-29', ts: '2026-09-29T10:00:00-06:00' });
  assert.equal(ahoraGT(new Date('2026-09-30T05:59:59Z')).fecha, '2026-09-29');
  assert.equal(ahoraGT(new Date('2026-09-30T06:00:00Z')).fecha, '2026-09-30');
});

test('validarVoto normaliza sinónimos y redondea a centavos', () => {
  const { voto } = validarVoto({ producto: 'Diesel', tipo: 'OTRO', precio: '49.404', token: 'abcdefghijklmnop' });
  assert.equal(voto.producto, 'diésel');
  assert.equal(voto.tipo, 'otro');
  assert.equal(voto.precio, 49.4);
  assert.equal(voto.modalidad, 'autoservicio');
});

test('referencia sin IVA+IDP desde el Decreto 22-2026', () => {
  // precio publicado antes del decreto + hoy ya en vigencia -> se le quitan IVA e IDP
  assert.equal(referenciaSinImpuestos('regular', 43.29, '2026-09-28', '2026-10-01'), 34.54);
  assert.equal(referenciaSinImpuestos('superior', 45.29, '2026-09-28', '2026-10-01'), 36.24);
  assert.equal(referenciaSinImpuestos('diésel', 49.39, '2026-09-28', '2026-10-01'), 42.94);
  // antes de la vigencia, o precio ya de la vigencia, o sin fecha: no se toca
  assert.equal(referenciaSinImpuestos('regular', 43.29, '2026-09-28', '2026-09-30'), 43.29);
  assert.equal(referenciaSinImpuestos('regular', 34.5, '2026-10-01', '2026-10-01'), 34.5);
  assert.equal(referenciaSinImpuestos('regular', 43.29, undefined, '2026-10-01'), 43.29);
  assert.equal(referenciaSinImpuestos('regular', 43.29, '2026-09-28', '2027-01-02'), 43.29);
});
