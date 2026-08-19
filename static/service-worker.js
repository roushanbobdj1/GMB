// Service Worker for offline functionality.
// v6: NEVER modify HTML responses and NEVER inject runtime scripts.
// Dynamic/admin pages always come directly from the server.
// This avoids stale Bootstrap backdrops, loading overlays and gray-screen UI bugs.

const CACHE_NAME = 'gmb-sw-v6';
const OFFLINE_URL = '/offline.html';
const URLS_TO_CACHE = [
    '/static/manifest.json',
    OFFLINE_URL
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(URLS_TO_CACHE))
            .catch(error => console.warn('Service worker cache install failed:', error))
    );

    // Activate the new worker immediately after deployment.
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(cacheNames => Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName);
                    }
                    return Promise.resolve(false);
                })
            ))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const request = event.request;

    // Only handle GET requests. POST/PUT/PATCH/DELETE requests must always
    // go directly to the server and must never be served from cache.
    if (request.method !== 'GET') return;

    const url = new URL(request.url);

    // Navigation/admin HTML must ALWAYS come from the network.
    // We deliberately do not read, rewrite, inject into, or cache HTML.
    if (request.mode === 'navigate' ||
        request.destination === 'document' ||
        url.pathname.startsWith('/admin/')) {
        event.respondWith(
            fetch(request).catch(() => caches.match(OFFLINE_URL))
        );
        return;
    }

    // Do not intercept API, AJAX, images, JS, CSS, uploads, etc.
    // Let the browser/server handle them normally. This keeps the service
    // worker from changing application behavior or returning offline.html
    // for an unrelated failed request.
});
