// Simulador mínimo de la API de D1 sobre node:sqlite (incluido en Node), para probar el
// Worker sin red ni cuenta. La prueba de integración (integracion_local.mjs) usa el D1
// local real de Cloudflare (miniflare); esto cubre la lógica rápido y sin dependencias.
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';

const SCHEMA = new URL('../schema.sql', import.meta.url);

export function crearD1() {
  const db = new DatabaseSync(':memory:');
  db.exec(readFileSync(SCHEMA, 'utf8'));
  return {
    _db: db,
    prepare(sql) {
      const stmt = db.prepare(sql);
      let params = [];
      const api = {
        bind(...p) { params = p; return api; },
        async run() {
          const r = stmt.run(...params);
          return { success: true, meta: { changes: Number(r.changes), last_row_id: Number(r.lastInsertRowid) } };
        },
        async first() { return stmt.get(...params) ?? null; },
        async all() { return { success: true, results: stmt.all(...params) }; },
      };
      return api;
    },
  };
}
