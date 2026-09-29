// Lógica pura del sistema de votos (sin red ni base de datos).
// Réplica en JS de collector/calibracion.py: `clasificar` y las reglas de validación
// deben mantenerse idénticas. El contrato compartido está en
// tests/fixtures/clasificar_casos.json (lo verifican pytest y node --test).

export const UMBRALES = {
  min_votos: 10,        // votos válidos para hablar de señal comunitaria
  min_debil: 3,         // por debajo: "sin datos suficientes"
  min_escritos: 5,      // precios escritos necesarios para hablar de cambio
  tol_acuerdo: 0.5,     // ±Q de la mediana para contar un voto "de acuerdo"
  min_acuerdo: 0.6,     // fracción mínima de acuerdo
  tol_cambio: 1.0,      // diferencia vs oficial que dispara "posible cambio"
  precio_min: 10,
  precio_max: 100,
  max_desvio_ref: 15,   // un escrito a más de Q15 del oficial se rechaza
};

export const PRODUCTOS = ['superior', 'regular', 'diésel'];
const ALIAS = { diessel: 'diésel', diesel: 'diésel', 'diésel': 'diésel', super: 'superior', superior: 'superior', regular: 'regular' };
const MODALIDADES = { autoservicio: 'autoservicio', 'auto servicio': 'autoservicio', as: 'autoservicio',
  'servicio completo': 'servicio_completo', servicio_completo: 'servicio_completo', sc: 'servicio_completo' };

export function canonProducto(p) {
  return ALIAS[String(p ?? '').trim().toLowerCase()] ?? null;
}

export function canonModalidad(m) {
  if (m === undefined || m === null || m === '') return 'autoservicio';
  return MODALIDADES[String(m).trim().toLowerCase().replace(/[_-]/g, ' ')] ?? null;
}

export function mediana(valores) {
  if (!valores.length) return null;
  const s = [...valores].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Estado de un producto en un día: sin | debil | coincide | cambio | respaldado. */
export function clasificar(nVotos, nCoincide, escritos, ref, externo = false, u = UMBRALES) {
  const med = mediana(escritos);
  if (nVotos >= u.min_votos) {
    if (escritos.length >= u.min_escritos) {
      const cerca = escritos.filter((x) => Math.abs(x - med) <= u.tol_acuerdo).length / escritos.length;
      if (cerca < u.min_acuerdo) return ['debil', med];
      if (ref !== null && ref !== undefined && Math.abs(med - ref) >= u.tol_cambio) return [externo ? 'respaldado' : 'cambio', med];
      return ['coincide', med];
    }
    return [nCoincide / nVotos >= u.min_acuerdo ? 'coincide' : 'debil', med];
  }
  if (nVotos >= u.min_debil) return ['debil', med];
  return ['sin', med];
}

/** Fecha y hora de Guatemala (UTC-6, sin horario de verano) a partir de un instante UTC. */
export function ahoraGT(ahora = new Date()) {
  const gt = new Date(ahora.getTime() - 6 * 3600 * 1000);
  const iso = gt.toISOString(); // 2026-09-29T14:03:05.123Z (ya desplazado)
  return { fecha: iso.slice(0, 10), ts: `${iso.slice(0, 19)}-06:00` };
}

const RE_TOKEN = /^[A-Za-z0-9_-]{16,64}$/;

/**
 * Valida el cuerpo crudo de un voto que envía el navegador.
 * Devuelve { voto } normalizado o { error: motivo }. El servidor decide fecha, hora,
 * dispositivo y precio mostrado: el cliente NO puede fijarlos.
 */
export function validarVoto(cuerpo, u = UMBRALES) {
  if (!cuerpo || typeof cuerpo !== 'object') return { error: 'cuerpo_invalido' };
  const producto = canonProducto(cuerpo.producto);
  if (!producto || !PRODUCTOS.includes(producto)) return { error: 'producto_invalido' };
  const modalidad = canonModalidad(cuerpo.modalidad);
  if (!modalidad) return { error: 'modalidad_invalida' };
  const tipo = String(cuerpo.tipo ?? '').trim().toLowerCase();
  if (tipo !== 'coincide' && tipo !== 'otro') return { error: 'tipo_invalido' };
  if (typeof cuerpo.token !== 'string' || !RE_TOKEN.test(cuerpo.token)) return { error: 'token_invalido' };

  let precio = null;
  if (tipo === 'otro') {
    precio = typeof cuerpo.precio === 'string' ? Number(cuerpo.precio) : cuerpo.precio;
    if (typeof precio !== 'number' || !Number.isFinite(precio)) return { error: 'precio_no_numerico' };
    if (precio < u.precio_min || precio > u.precio_max) return { error: 'precio_fuera_de_rango' };
    precio = Math.round(precio * 100) / 100;
  } else if (cuerpo.precio !== undefined && cuerpo.precio !== null && cuerpo.precio !== '') {
    return { error: 'coincide_con_precio' };
  }

  const vioOficial = cuerpo.vio_oficial === 0 || cuerpo.vio_oficial === false || cuerpo.vio_oficial === '0' ? 0 : 1;
  if (vioOficial === 0 && tipo === 'coincide') return { error: 'coincide_ciego' };

  let zona = typeof cuerpo.zona === 'string' ? cuerpo.zona.trim().slice(0, 40) : '';
  zona = zona || null;
  return { voto: { producto, modalidad, tipo, precio, vio_oficial: vioOficial, zona, token: cuerpo.token } };
}
