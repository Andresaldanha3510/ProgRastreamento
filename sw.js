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

// --- Início da Lógica Corrigida ---

// Função para pegar a localização e enviar para o servidor
function getCurrentPositionAndPostToServer() {
    // A geolocalização só funciona em contextos seguros (HTTPS ou localhost)
    navigator.geolocation.getCurrentPosition(
        (position) => {
            const { latitude, longitude } = position.coords;
            
            // Usamos fetch para enviar os dados para a sua API no backend
            fetch('/salvar_localizacao_motorista', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ latitude, longitude }),
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    console.log('SW: Localização enviada com sucesso.');
                } else {
                    console.error('SW: Falha ao enviar localização:', data.message);
                }
            })
            .catch(error => {
                console.error('SW: Erro de rede ao enviar localização:', error);
            });
        },
        (error) => {
            console.error("SW: Erro ao obter geolocalização:", error.message);
        },
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 }
    );
}

// --- Fim da Lógica Corrigida ---


// Instalação: pré-cache de recursos essenciais e skipWaiting para ativar imediatamente
self.addEventListener('install', event => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
      .catch(err => console.error("SW: Falha ao fazer cache dos recursos na instalação:", err))
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
      .catch(() => caches.match(event.request))
  );
});


// Listener do evento 'periodicsync' para rastreamento periódico
self.addEventListener('periodicsync', event => {
  // A tag aqui deve ser a mesma que você usa para registrar o sync
  if (event.tag === 'send-location-periodic-sync') {
    console.log('SW: Sincronização periódica de localização acionada.');
    event.waitUntil(getCurrentPositionAndPostToServer());
  }
});

// Listener do evento de 'sync' para rastreamento em background
self.addEventListener('sync', (event) => {
    if (event.tag === 'send-location-sync') {
        console.log('SW: Sincronização de localização em background acionada.');
        event.waitUntil(getCurrentPositionAndPostToServer());
    }
});