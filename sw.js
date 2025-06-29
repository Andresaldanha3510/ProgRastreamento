const CACHE_NAME = 'trackgo-motorista-v1';
// Lista de arquivos para fazer cache inicial.
const urlsToCache = [
  '/motorista_dashboard',
  '/static/caminhaoandando.gif',
  'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css',
  'https://cdn.jsdelivr.net/npm/toastify-js/src/toastify.min.css',
  'https://cdn.jsdelivr.net/npm/toastify-js'
];

// Evento de instalação: abre o cache e armazena os arquivos principais.
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => {
        console.log('Cache aberto');
        return cache.addAll(urlsToCache);
      })
  );
});

// Evento de fetch: serve arquivos do cache primeiro, se disponíveis.
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        // Se encontrar no cache, retorna. Senão, busca na rede.
        return response || fetch(event.request);
      })
  );
});

// Evento de ativação: limpa caches antigos.
self.addEventListener('activate', event => {
  const cacheWhitelist = [CACHE_NAME];
  event.waitUntil(
    caches.keys().then(cacheNames => {
      return Promise.all(
        cacheNames.map(cacheName => {
          if (cacheWhitelist.indexOf(cacheName) === -1) {
            return caches.delete(cacheName);
          }
        })
      );
    })
  );
});

// *** SINCRONIZAÇÃO PERIÓDICA EM SEGUNDO PLANO ***
self.addEventListener('periodicsync', (event) => {
  if (event.tag === 'get-location-updates') {
    event.waitUntil(requestAndSendLocation());
  }
});

function requestAndSendLocation() {
    return new Promise((resolve, reject) => {
        if ('geolocation' in self.navigator) {
             self.navigator.geolocation.getCurrentPosition(async (position) => {
                const { latitude, longitude } = position.coords;
                try {
                    const response = await fetch('/atualizar_localizacao', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ latitude: latitude, longitude: longitude })
                    });
                    const result = await response.json();
                    console.log('Localização em segundo plano enviada:', result);
                    resolve();
                } catch (error) {
                    console.error('Falha ao enviar localização em background:', error);
                    reject(error);
                }
            }, (error) => {
                console.error('Erro ao obter geolocalização em background:', error);
                reject(error);
            });
        } else {
            console.log('Geolocalização não disponível no service worker.');
            reject('Geolocation not available');
        }
    });
}