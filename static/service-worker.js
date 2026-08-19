// Service Worker for offline functionality
// v3: robust Bootstrap modal/backdrop recovery for ALL campaign modals.
const CACHE_NAME = 'rtm-v3';
const URLS_TO_CACHE = [
    '/',
    '/static/manifest.json',
    '/offline.html'
];

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

// Bootstrap modals are rendered inside campaign cards. Moving every modal
// to <body> prevents card overflow/transform rules from trapping the modal
// backdrop. The cleanup also handles Allocate/Edit/Pause/Stop/Delete.
async function prepareHtmlResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('text/html')) return response;

    try {
        const html = await response.text();
        const fixScript = `
<script>
(function () {
    'use strict';

    function moveCampaignModalsToBody() {
        document.querySelectorAll('.modal').forEach(function (modal) {
            if (modal.parentElement !== document.body) {
                document.body.appendChild(modal);
            }
        });
    }

    function clearModalState() {
        var loading = document.getElementById('loadingOverlay');
        if (loading) {
            loading.classList.remove('show');
            loading.setAttribute('aria-hidden', 'true');
            loading.style.display = 'none';
        }

        document.querySelectorAll('.modal-backdrop').forEach(function (backdrop) {
            backdrop.remove();
        });

        document.querySelectorAll('.modal.show').forEach(function (modal) {
            modal.classList.remove('show');
            modal.style.display = 'none';
            modal.setAttribute('aria-hidden', 'true');
        });

        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('padding-right');
        document.body.style.removeProperty('overflow');
    }

    document.addEventListener('DOMContentLoaded', function () {
        moveCampaignModalsToBody();
        clearModalState();

        // Catch Bootstrap's shown/hidden lifecycle for every modal,
        // including Stop and Delete.
        document.addEventListener('shown.bs.modal', function (event) {
            if (event.target && event.target.classList.contains('modal')) {
                document.body.classList.add('modal-open');
            }
        });

        document.addEventListener('hidden.bs.modal', function () {
            setTimeout(function () {
                if (!document.querySelector('.modal.show')) {
                    document.querySelectorAll('.modal-backdrop').forEach(function (backdrop) {
                        backdrop.remove();
                    });
                    document.body.classList.remove('modal-open');
                    document.body.style.removeProperty('padding-right');
                    document.body.style.removeProperty('overflow');
                }
            }, 50);
        });
    });

    window.addEventListener('pageshow', function () {
        moveCampaignModalsToBody();
        clearModalState();
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
