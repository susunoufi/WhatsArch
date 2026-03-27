const CACHE_VERSION = 'whatsarch-v2';
const URLS_TO_CACHE = ['/', '/app'];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_VERSION).then(cache => cache.addAll(URLS_TO_CACHE))
    );
});

self.addEventListener('activate', event => {
    // Delete old caches that don't match the current version
    event.waitUntil(
        caches.keys().then(cacheNames =>
            Promise.all(
                cacheNames
                    .filter(name => name !== CACHE_VERSION)
                    .map(name => caches.delete(name))
            )
        )
    );
});

self.addEventListener('fetch', event => {
    // Network-first strategy: try network, fall back to cache
    event.respondWith(
        fetch(event.request)
            .then(response => {
                // Cache successful responses
                if (response.ok && event.request.method === 'GET') {
                    const clone = response.clone();
                    caches.open(CACHE_VERSION).then(cache => cache.put(event.request, clone));
                }
                return response;
            })
            .catch(() => caches.match(event.request))
    );
});
