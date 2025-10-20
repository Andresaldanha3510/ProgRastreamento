// sw.js

const CACHE_NAME = 'trackgo-pwa-v1'; // Nome genérico
// Lista de recursos genéricos para fallback offline
const urlsToCache = [
  '/login', // Página de fallback principal
  '/static/caminhaoandando.gif',
  '/static/brasão.png', // Adicionei a imagem do logo
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/toastify-js/src/toastify.min.css',
  'https://cdn.jsdelivr.net/npm/toastify-js'
];

// Função para postar a localização (usada apenas pelo motorista)
function getCurrentPositionAndPostToServer() {
    navigator.geolocation.getCurrentPosition(
        (position) => {
            const { latitude, longitude } = position.coords;
            // A rota foi alterada no app.py para receber dados via socket
            // Esta função de fetch pode ser mantida como um fallback
            console.log('SW: Posição obtida, mas o envio agora é via WebSocket na página.');
        },
        (error) => {
            console.error("SW: Erro ao obter geolocalização:", error.message);
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
}

// Instalação: pré-cache de recursos essenciais
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
      .catch(err => console.error("SW: Falha ao fazer cache dos recursos na instalação:", err))
  );
});

// Ativação: limpeza de caches antigos
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

// Fetch: Tenta a rede primeiro. Se falhar (offline), tenta buscar no cache.
self.addEventListener('fetch', event => {
  // Ignora requisições que não são GET (como POST)
  if (event.request.method !== 'GET') {
    return;
  }
  
  event.respondWith(
    fetch(event.request)
      .catch(() => {
        return caches.match(event.request);
      })
  );
});


// Listener do evento 'periodicsync' para rastreamento periódico (SÓ SERÁ ATIVADO PARA O MOTORISTA)
self.addEventListener('periodicsync', event => {
  if (event.tag === 'send-location-periodic-sync') {
    console.log('SW: Sincronização periódica de localização acionada (somente para motoristas).');
    event.waitUntil(getCurrentPositionAndPostToServer());
  }
});

// Listener de clique na notificação (permanece igual)
self.addEventListener('notificationclick', event => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(clientList => {
      const urlToOpen = '/consultar_viagens';
      for (const client of clientList) {
        if (client.url.includes(urlToOpen) && 'focus' in client) {
          return client.focus();
        }
      }
      if (clients.openWindow) {
        return clients.openWindow(urlToOpen);
      }
    })
  );
});