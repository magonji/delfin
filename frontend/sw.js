const CACHE_NAME = 'delfin-v39';
const STATIC_ASSETS = [
  '/app/index.html',
  '/app/transactions.html',
  '/app/loans.html',
  '/app/budget.html',
  '/app/tools.html',
  '/app/cache.js',
  '/app/manifest.json',
  '/app/icons/icon-180.png',
  '/app/icons/icon-192.png',
  '/app/icons/icon-512.png'
];

// Install: cache static assets
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(STATIC_ASSETS))
      .then(() => self.skipWaiting())
  );
});

// Activate: clean old caches and take control immediately
self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// How long a slow network is given before the cached copy is served instead.
// Long enough not to prefer stale code on an ordinary mobile connection, short
// enough that a stalled one never leaves the app staring at nothing.
const NETWORK_PATIENCE_MS = 4000;

// Fetch handler
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);

  // API calls: always go to network, never cache
  if (!url.pathname.startsWith('/app/') && !url.hostname.includes('fonts.googleapis.com') && !url.hostname.includes('fonts.gstatic.com')) {
    return;
  }

  // Google Fonts: cache-first (they're versioned/immutable)
  if (url.hostname.includes('fonts.googleapis.com') || url.hostname.includes('fonts.gstatic.com')) {
    event.respondWith(
      caches.match(event.request).then(cached => {
        if (cached) return cached;
        return fetch(event.request).then(response => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
          return response;
        });
      })
    );
    return;
  }

  // HTML and scripts: network-first (always get latest when online, cache fallback
  // for offline). Code must not be served a version behind, which is what the
  // stale-while-revalidate branch below would do.
  //
  // Network-first, though, is not network-forever. A connection that is up but
  // barely moving is worse than no connection at all: `fetch` neither resolves
  // nor rejects, so the page hangs on a blank screen instead of taking the
  // perfectly good copy sitting in the cache. Once a cached copy exists the
  // network gets a few seconds to beat it, and then it is served anyway.
  if (url.pathname.endsWith('.html') || url.pathname.endsWith('.js')) {
    event.respondWith((async () => {
      const cached = await caches.match(event.request);
      const network = fetch(event.request).then(response => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        return response;
      });
      // Nothing cached to fall back on, so waiting is the only option.
      if (!cached) return network.catch(() => caches.match(event.request));
      return Promise.race([
        network.catch(() => cached),
        new Promise(resolve => setTimeout(() => resolve(cached), NETWORK_PATIENCE_MS))
      ]);
    })());
    return;
  }

  // Other static assets (icons, manifest): stale-while-revalidate
  event.respondWith(
    caches.match(event.request).then(cached => {
      const fetchPromise = fetch(event.request).then(response => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
        return response;
      }).catch(() => cached);

      return cached || fetchPromise;
    })
  );
});
