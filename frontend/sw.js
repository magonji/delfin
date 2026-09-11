const CACHE_NAME = 'delfin-v44';
const STATIC_ASSETS = [
  '/app/index.html',
  '/app/transactions.html',
  '/app/loans.html',
  '/app/budget.html',
  '/app/tools.html',
  '/app/cache.js',
  '/app/forms.css',
  '/app/loan-form.js',
  '/app/account-form.js',
  '/app/transaction-form.js',
  '/app/quick-add.js',
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

// How long the stored copy is served before the network is asked whether it has
// changed. Long enough for the page to have loaded everything it wanted first.
const REVALIDATE_AFTER_MS = 3000;

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

  // HTML and scripts: served from the cache, and replaced behind your back.
  //
  // These used to go to the network first, so that code could never be a version
  // behind. The price turned out to be the whole of the app's speed: a page and
  // its six shared files are two round trips, paid on every single navigation,
  // before a line of Delfin's own code runs. On a page whose figures were all
  // already stored, that was five sixths of the wait.
  //
  // So the stored copy is served at once and the network asked in the background;
  // whatever comes back is what the next load gets. The window in which you can
  // be a version behind is therefore one load long, and only just after an
  // update -- measured, not assumed: publish a change and the load that follows
  // it still shows the old page, the one after that the new one.
  //
  // Bumping CACHE_NAME on release does not shorten that window (also measured),
  // but it does make the change all-or-nothing: installing under a new name
  // fetches every file afresh before it takes over, so you never get a new page
  // paired with yesterday's scripts.
  //
  // Nothing stored yet -- a first visit, or a page reached with a query string --
  // means waiting for the network, which is what it did before anyway.
  if (url.pathname.endsWith('.html') || url.pathname.endsWith('.js')) {
    // Only a good answer replaces a good copy, and only under its plain URL:
    // "transactions.html?account=5" is the same document as the one already
    // stored, and keeping one entry per link would fill the cache with copies.
    //
    // Returns the writing, so that whoever cares can wait for it. Nobody waited
    // before, and a service worker with nothing left to do is stopped where it
    // stands: the new copy was fetched and then thrown away unwritten, so an
    // updated file never arrived however many times the page was opened.
    const store = response => {
      if (!response || !response.ok || url.search) return Promise.resolve();
      const clone = response.clone();
      return caches.open(CACHE_NAME).then(cache => cache.put(event.request, clone));
    };

    event.respondWith((async () => {
      const cached = await caches.match(event.request, { ignoreSearch: true });
      if (!cached) {
        const response = await fetch(event.request);
        // The page has waited long enough; the writing can finish behind it.
        event.waitUntil(store(response).catch(() => {}));
        return response;
      }

      // Once the page has finished asking for its own things.
      //
      // A browser holds six connections open to a server, and these seven
      // checks, which nobody is waiting on, took all of them the moment the
      // page opened -- so the figures it had gone to fetch queued behind them
      // for a whole round trip, and serving the app from the cache had bought
      // back only half of what it should have. Asking for them late costs
      // nothing: what comes back is for the next visit, not this one.
      //
      // Said out loud that it must finish, too: a service worker with no
      // pending work can be stopped where it stands. If it is stopped anyway --
      // the tab closed in the meantime -- the check simply happens next time.
      //
      // Asked for by its address rather than by re-sending the request itself:
      // a navigation cannot be handed back to fetch with options attached, and
      // doing so failed with "Failed to fetch" every time -- so every page's
      // own markup was the one file that never got its update.
      const quiet = new Request(event.request.url, { priority: 'low' });
      event.waitUntil(
        new Promise(done => setTimeout(done, REVALIDATE_AFTER_MS))
          .then(() => fetch(quiet))
          .then(store)
          .catch(() => {})
      );
      return cached;
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
