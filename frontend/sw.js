// Gale Service Worker — tile caching + offline support
const CACHE = 'gale-tiles-v1';
const R2 = 'pub-9975b1cfcbf9480f9e1333c7e208a6ce.r2.dev';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('fetch', (event) => {
  if (!event.request.url.includes(R2)) return;

  // Strip cache-bust query params for consistent cache keys
  const cleanUrl = event.request.url.split('?')[0];
  const cacheReq = new Request(cleanUrl);

  if (cleanUrl.endsWith('metadata.json')) {
    // Network-first for metadata (need freshness info)
    event.respondWith(
      fetch(event.request)
        .then(resp => {
          if (resp.ok) caches.open(CACHE).then(c => c.put(cacheReq, resp.clone()));
          return resp;
        })
        .catch(() => caches.open(CACHE).then(c => c.match(cacheReq)))
    );
    return;
  }

  // Cache-first for tile PNGs (instant offline, zero network when cached)
  event.respondWith(
    caches.open(CACHE).then(async c => {
      const cached = await c.match(cacheReq);
      if (cached) return cached;
      const resp = await fetch(event.request);
      if (resp.ok) c.put(cacheReq, resp.clone());
      return resp;
    })
  );
});

// Main thread signals when a new HRRR run arrives → purge stale tiles
self.addEventListener('message', (e) => {
  if (e.data === 'clear-tiles') caches.delete(CACHE);
});
