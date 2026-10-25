const OFFLINE_CACHE = 'stationnet-offline-v1';
const OFFLINE_URL = '/static/offline.html';

self.addEventListener('install', event => {
  event.waitUntil(caches.open(OFFLINE_CACHE)
    .then(cache => cache.add(OFFLINE_URL))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(
    keys.filter(key => key.startsWith('stationnet-offline-') && key !== OFFLINE_CACHE)
      .map(key => caches.delete(key))
  )).then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate') return;
  event.respondWith(fetch(event.request).catch(async () => {
    const cache = await caches.open(OFFLINE_CACHE);
    return (await cache.match(OFFLINE_URL)) || Response.error();
  }));
});
