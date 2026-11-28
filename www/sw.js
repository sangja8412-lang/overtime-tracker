const CACHE = 'ot-local-v6';
const ASSETS = ['./', './index.html', './manifest.webmanifest', './office-capybara.webp'];

self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(ASSETS)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
  event.waitUntil(Promise.all(['ot-local-v1', 'ot-local-v2', 'ot-local-v3', 'ot-local-v4', 'ot-local-v5'].map(key => caches.delete(key))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', event => {
  if (event.request.mode !== 'navigate') {
    if (new URL(event.request.url).pathname.endsWith('/office-capybara.webp')) {
      event.respondWith(caches.open(CACHE).then(async cache => (await cache.match(event.request)) || fetch(event.request)));
    }
    return;
  }
  event.respondWith(fetch(event.request).catch(async () => {
    const cache = await caches.open(CACHE);
    return cache.match('./index.html');
  }));
});
