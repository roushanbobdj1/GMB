// Service Worker for offline functionality.
// v7: root-scoped, network-first navigation with a private-data-safe fallback.
// Dynamic/admin pages always come directly from the server.
// This avoids stale Bootstrap backdrops, loading overlays and gray-screen UI bugs.

const CACHE_NAME = 'gmb-sw-v7';
const OFFLINE_URL = '/offline.html';
const URLS_TO_CACHE = [
    '/static/manifest.json',
    '/static/images/icon-192x192.png',
    '/static/images/icon-512x512.png',
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

    // Dynamic HTML always comes from the network. We never cache private
    // user/admin responses; only the standalone offline page is a fallback.
    if (request.mode === 'navigate' || request.destination === 'document') {
        event.respondWith(
            fetch(request).catch(async () => {
                const fallback = await caches.match(OFFLINE_URL);
                return fallback || Response.error();
            })
        );
        return;
    }

    // Do not intercept API, AJAX, images, JS, CSS, uploads, etc.
    // Let the browser/server handle them normally. This keeps the service
    // worker from changing application behavior or returning offline.html
    // for an unrelated failed request.
});
