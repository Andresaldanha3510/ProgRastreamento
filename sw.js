// sw.js

const CACHE_NAME = 'trackgo-motorista-v2';
// Lista de recursos a serem armazenados como fallback
const urlsToCache = [
  '/motorista_dashboard',
  '/static/caminhaoandando.gif',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/toastify-js/src/toastify.min.css',
  'https://cdn.jsdelivr.net/npm/toastify-js'
];

// Instalação: pré-cache de recursos essenciais e skipWaiting para ativar imediatamente
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  );
});

// Ativação: claim para controlar todas as abas e limpeza de caches antigos
self.addEventListener('activate', event => {
  self.clients.claim();
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(
        keys
          .filter(key => key !== CACHE_NAME)
          .map(key => caches.delete(key))
      )
    )
  );
});

// Fetch: network-first, com fallback para cache
self.addEventListener('fetch', event => {
  event.respondWith(
    fetch(event.request, { cache: 'no-store' })
      .then(networkResponse => {
        // (Opcional) atualizar o cache com a resposta vinda do servidor:
        // if (event.request.method === 'GET') {
        //   caches.open(CACHE_NAME)
        //     .then(cache => cache.put(event.request, networkResponse.clone()));
        // }
        return networkResponse;
      })
      .catch(() => caches.match(event.request))
  );
});

// Periodic Background Sync (se estiver usando)
self.addEventListener('periodicsync', event => {
  if (event.tag === 'get-location-updates') {
    event.waitUntil(requestAndSendLocation());
  }
});

function requestAndSendLocation() {
  // Seu código de geolocalização em background aqui
  return getCurrentPositionAndPostToServer();
}

// (Defina aqui a função getCurrentPositionAndPostToServer, ou importe de outro módulo)
