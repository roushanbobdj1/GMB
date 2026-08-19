// Service Worker for offline functionality
// v4: never cache dynamic HTML pages and never inject modal scripts.
// Admin/campaign pages must always come from the server so Bootstrap
// modals and action buttons cannot get stuck behind a stale backdrop.
const CACHE_NAME = 'rtm-v4';
const URLS_TO_CACHE = [
    '/static/manifest.json',
    '/offline.html'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            console.log('✅ Caching static app assets');
            return cache.addAll(URLS_TO_CACHE).catch(error => {
                console.log('Cache error:', error);
            });
        })
    );
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => {
            return Promise.all(
                cacheNames.map(cacheName => {
                    if (cacheName !== CACHE_NAME) {
                        console.log('Deleting old cache:', cacheName);
                        return caches.delete(cacheName);
                    }
                    return null;
                })
            );
        }).then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') return;

    const requestUrl = new URL(event.request.url);

    // NEVER cache HTML/navigation requests. This is important for the admin
    // campaign page because modal/action state is dynamic and must not be
    // served from a stale service-worker response.
    if (event.request.mode === 'navigate' ||
        event.request.destination === 'document' ||
        requestUrl.pathname.startsWith('/admin/')) {
        event.respondWith(fetch(event.request));
        return;
    }

    // Static assets can use cache-first, with a network fallback.
    event.respondWith(
        caches.match(event.request).then(cached => {
            if (cached) return cached;

            return fetch(event.request).then(response => {
                if (!response || response.status !== 200 || response.type !== 'basic') {
                    return response;
                }

                const responseClone = response.clone();
                caches.open(CACHE_NAME).then(cache => {
                    cache.put(event.request, responseClone);
                });

                return response;
            });
        }).catch(() => caches.match('/offline.html'))
    );
});
