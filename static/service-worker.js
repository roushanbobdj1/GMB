// Service Worker for offline functionality
// v5: never cache admin HTML and actively clean orphaned Bootstrap
// backdrops/loading overlays. Dynamic admin pages always come from server.
const CACHE_NAME = 'rtm-v5';
const URLS_TO_CACHE = [
    '/static/manifest.json',
    '/offline.html'
];

self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME).then(cache =>
            cache.addAll(URLS_TO_CACHE).catch(error => console.log('Cache error:', error))
        )
    );
    self.skipWaiting();
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(cacheNames => Promise.all(
            cacheNames.map(cacheName =>
                cacheName !== CACHE_NAME ? caches.delete(cacheName) : null
            )
        )).then(() => self.clients.claim())
    );
});

// Inject only a tiny runtime cleanup script into HTML responses.
// We DO NOT cache HTML. This prevents the stuck transparent/gray page while
// keeping normal user-triggered Bootstrap modals working.
async function prepareHtmlResponse(response) {
    const contentType = response.headers.get('content-type') || '';
    if (!contentType.includes('text/html')) return response;

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

    function isVisible(element) {
        if (!element) return false;
        var style = window.getComputedStyle(element);
        var rect = element.getBoundingClientRect();
        return style.display !== 'none' &&
               style.visibility !== 'hidden' &&
               parseFloat(style.opacity || '1') > 0 &&
               rect.width > 0 && rect.height > 0;
    }

    function cleanupOrphanedOverlay() {
        moveModalsToBody();

        var visibleModal = Array.from(document.querySelectorAll('.modal.show'))
            .some(isVisible);

        // A backdrop with no genuinely visible modal is always stale.
        if (!visibleModal) {
            document.querySelectorAll('.modal-backdrop').forEach(function (el) {
                el.remove();
            });

            document.querySelectorAll('.modal.show').forEach(function (modal) {
                modal.classList.remove('show');
                modal.style.removeProperty('display');
                modal.setAttribute('aria-hidden', 'true');
            });

            document.body.classList.remove('modal-open');
            document.body.style.removeProperty('padding-right');
            document.body.style.removeProperty('overflow');
        }

        // Also clear a loading overlay that survived a completed page load.
        var loading = document.getElementById('loadingOverlay');
        if (loading && !loading.dataset.keepOpen) {
            loading.classList.remove('show', 'active', 'visible');
            loading.setAttribute('aria-hidden', 'true');
            loading.style.display = 'none';
            loading.style.pointerEvents = 'none';
        }
    }

    document.addEventListener('DOMContentLoaded', function () {
        moveModalsToBody();
        cleanupOrphanedOverlay();

        // Bootstrap can create the backdrop asynchronously.
        document.addEventListener('shown.bs.modal', function () {
            moveModalsToBody();
        });

        document.addEventListener('hidden.bs.modal', function () {
            setTimeout(cleanupOrphanedOverlay, 0);
        });

        // Catch overlays created by AJAX/form actions too.
        var observer = new MutationObserver(function () {
            window.requestAnimationFrame(cleanupOrphanedOverlay);
        });
        observer.observe(document.documentElement, {
            childList: true,
            subtree: true,
            attributes: true,
            attributeFilter: ['class', 'style', 'aria-hidden']
        });
    });

    window.addEventListener('pageshow', cleanupOrphanedOverlay);
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
        console.warn('HTML cleanup injection failed:', error);
        return response;
    }
}

self.addEventListener('fetch', event => {
    if (event.request.method !== 'GET') return;

    const requestUrl = new URL(event.request.url);

    event.respondWith(
        fetch(event.request)
            .then(response => {
                if (!response || response.status !== 200 || response.type !== 'basic') {
                    return response;
                }

                // Never cache navigation/admin HTML. This is the key fix.
                if (event.request.mode === 'navigate' ||
                    event.request.destination === 'document' ||
                    requestUrl.pathname.startsWith('/admin/')) {
                    return prepareHtmlResponse(response);
                }

                return response;
            })
            .catch(() => {
                if (event.request.mode === 'navigate' ||
                    event.request.destination === 'document' ||
                    requestUrl.pathname.startsWith('/admin/')) {
                    return caches.match('/offline.html');
                }
                return caches.match(event.request).then(cached => cached || caches.match('/offline.html'));
            })
    );
});
