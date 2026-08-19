// Service Worker for offline functionality
// v2: also prevents stale Bootstrap modal/loading backdrops from
// leaving the page permanently dimmed after deploys or reloads.
const CACHE_NAME = 'rtm-v2';
const URLS_TO_CACHE = [
    '/',
    '/static/manifest.json',
    '/offline.html'
];

// Install event
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache => {
            console.log('✅ Caching app shell');
            return cache.addAll(URLS_TO_CACHE).catch(error => {
                console.log('Cache error:', error);
            });
        })
    );
    self.skipWaiting();
});

// Activate event
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

// Inject a small client-side safety net into HTML pages.
// The campaign modals are rendered inside cards; Bootstrap modals work
// reliably when moved to <body>, because transformed/overflow-hidden
// ancestors can otherwise clip the modal and leave only the backdrop.
async function prepareHtmlResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('text/html')) {
        return response;
    }

    try {
        const html = await response.text();
        const fixScript = `
<script>
(function () {
    'use strict';

    function moveModalsToBody() {
        document.querySelectorAll('.modal').forEach(function (modal) {
            if (modal.parentElement !== document.body) {
                document.body.appendChild(modal);
            }
        });
    }

    function clearStaleBackdrop() {
        var loading = document.getElementById('loadingOverlay');
        if (loading) {
            loading.classList.remove('show');
            loading.setAttribute('aria-hidden', 'true');
        }

        document.querySelectorAll('.modal-backdrop').forEach(function (backdrop) {
            backdrop.remove();
        });

        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('padding-right');
    }

    document.addEventListener('DOMContentLoaded', function () {
        moveModalsToBody();
        clearStaleBackdrop();
    });

    window.addEventListener('pageshow', function () {
        moveModalsToBody();
        clearStaleBackdrop();
    });

    document.addEventListener('show.bs.modal', function (event) {
        var modal = event.target;
        if (modal && modal.parentElement !== document.body) {
            document.body.appendChild(modal);
        }
    });
})();
</script>`;

        const marker = '</body>';
        const index = html.toLowerCase().lastIndexOf(marker);
        const output = index >= 0
            ? html.slice(0, index) + fixScript + html.slice(index)
            : html + fixScript;

        const headers = new Headers(response.headers);
        headers.delete('content-length');
        headers.delete('content-encoding');

        return new Response(output, {
            status: response.status,
            statusText: response.statusText,
            headers
        });
    } catch (error) {
        console.warn('HTML safety injection failed:', error);
        return response;
    }
}

// Fetch event
self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') return;
    if (event.request.url.startsWith('chrome-extension://')) return;

    event.respondWith(
        fetch(event.request)
            .then(async response => {
                if (!response || response.status !== 200 || response.type !== 'basic') {
                    return response;
                }

                const preparedResponse = await prepareHtmlResponse(response.clone());
                const responseClone = preparedResponse.clone();

                caches.open(CACHE_NAME).then(cache => {
                    cache.put(event.request, responseClone);
                });

                return preparedResponse;
            })
            .catch(() => {
                return caches.match(event.request).then(response => {
                    return response || caches.match('/offline.html');
                });
            })
    );
});
