// Service worker minimo: hace la pagina instalable. NO guarda nada en cache:
// el precio debe ser siempre el mas reciente (objetivo del sitio).
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', () => {});
