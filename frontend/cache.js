/**
 * Delfin's client-side cache, and the handful of constants every page shares.
 *
 * The account-type vocabulary lives at the bottom of this file for one reason:
 * this is the only script all five pages already load, and it is precached by
 * the service worker. A file of its own would mean a script tag on each page
 * and another entry in sw.js, for twenty lines.
 *
 * One implementation shared by every page, so the rules are the same wherever
 * you look. Three things it gets right that the per-page versions did not:
 *
 *  - **Invalidation is per page.** A single global flag was consumed by whichever
 *    page happened to load first, leaving the others showing figures from before
 *    the edit. Each page now has its own flag and clears only that one.
 *  - **Only a change marks anything stale.** Merely filtering a list used to
 *    throw the whole dashboard cache away and make the next visit rebuild it.
 *  - **One namespace.** Everything lives under `cache_`, so a single sweep
 *    really does clear everything — loans included.
 *
 * The expiry is a backstop, not the mechanism: a save marks the affected pages
 * stale straight away, so these can be generous.
 */
(function (global) {
    'use strict';

    var PREFIX = 'cache_';
    var DIRTY = 'cache_dirty:';

    // Pages that keep a cache and therefore need telling when data changes.
    var PAGES = ['index', 'loans'];

    // Seconds. Named by how quickly the underlying figure goes out of date.
    var TTL = {
        volatile: 6 * 60 * 60,        // balances, totals, month progress
        historical: 3 * 24 * 60 * 60, // series and yearly aggregates already closed
        catalog: 30 * 24 * 60 * 60    // accounts, categories, the list of months
    };

    function isCacheKey(k) {
        return k.indexOf(PREFIX) === 0 && k.indexOf(DIRTY) !== 0;
    }

    function get(key) {
        var raw = global.localStorage.getItem(key);
        if (!raw) return null;
        try {
            var parsed = JSON.parse(raw);
            if (Date.now() > parsed.expiry) {
                global.localStorage.removeItem(key);
                return null;
            }
            return parsed.data;
        } catch (e) {
            global.localStorage.removeItem(key);
            return null;
        }
    }

    function set(key, data, kind) {
        var ttl = TTL[kind] || TTL.volatile;
        try {
            global.localStorage.setItem(key, JSON.stringify({
                data: data, expiry: Date.now() + ttl * 1000
            }));
        } catch (e) {
            // Out of quota: drop the cached data and carry on uncached rather
            // than letting a storage error break the page.
            clear();
        }
    }

    function clear() {
        Object.keys(global.localStorage).filter(isCacheKey)
            .forEach(function (k) { global.localStorage.removeItem(k); });
    }

    /**
     * Record that stored data has changed. `source` is the page that made the
     * change and is skipped — it has just refreshed itself.
     */
    function markDirty(source) {
        PAGES.forEach(function (page) {
            if (page !== source) global.localStorage.setItem(DIRTY + page, '1');
        });
    }

    /**
     * Called by a page as it loads. If something changed since it was last here,
     * the cache is dropped and its own flag cleared — leaving every other page's
     * flag standing, so they refresh on their next visit too.
     */
    function consumeDirty(page) {
        var flag = DIRTY + page;
        if (!global.localStorage.getItem(flag)) return false;
        clear();
        global.localStorage.removeItem(flag);
        return true;
    }

    // Keys written by the older per-page caches. Removed once so a browser that
    // has been running Delfin for a while does not keep them for ever.
    function dropLegacyKeys() {
        ['dirty_data', 'loans_summary', 'loans_details'].forEach(function (k) {
            global.localStorage.removeItem(k);
        });
    }
    dropLegacyKeys();

    global.DelfinCache = {
        get: get,
        set: set,
        clear: clear,
        markDirty: markDirty,
        consumeDirty: consumeDirty,
        TTL: TTL
    };
})(window);

/**
 * What kind of thing an account is.
 *
 * The vocabulary is Financisto's, minus PayPal, so a database that came from
 * there keeps its typing and goes back out again unchanged — anything Financisto
 * does not recognise is exported as CASH, so inventing our own words here would
 * quietly flatten the lot on the way out.
 *
 * Stored upper-case and canonical, shown in sentence case. Two of these carry
 * weight beyond the label: LIABILITY and CREDIT_CARD are what the budget reads
 * to tell paying a debt off from putting money by, and LIABILITY is what the
 * loans page and the dashboard's "without loans" figure look for.
 */
(function (global) {
    'use strict';

    // Offered in the pickers, in the order most people need them.
    var ORDER = ['BANK', 'SAVINGS', 'CASH', 'DEBIT_CARD', 'CREDIT_CARD',
                 'ELECTRONIC', 'ASSET', 'LIABILITY', 'OTHER'];

    var LABELS = {
        BANK: 'Bank',
        SAVINGS: 'Savings',
        CASH: 'Cash',
        DEBIT_CARD: 'Debit card',
        CREDIT_CARD: 'Credit card',
        ELECTRONIC: 'Electronic',
        ASSET: 'Asset',
        LIABILITY: 'Liability',
        OTHER: 'Other',
        // Not offered any more, but a Financisto import can still bring it in and
        // an account carrying it has to stay readable and editable.
        PAYPAL: 'PayPal'
    };

    /**
     * A mark for each kind, drawn on a 16-unit grid.
     *
     * These are read at about ten pixels, beside the word they illustrate, so
     * every one is a single silhouette with no detail finer than a stroke: at
     * that size an outline collapses into a smudge and hatching disappears.
     * Where two kinds are genuinely the same object — a debit card and a credit
     * card — they are told apart by one large feature, a chip against a magnetic
     * stripe, and not by anything smaller. The word beside them carries the
     * meaning regardless; these are a second glance, not the first.
     *
     * Holes are cut with fill-rule="evenodd" rather than drawn in the background
     * colour, so the shapes stay right on any background.
     */
    var ICONS = {
        // A pediment on columns.
        BANK: 'M8 1 15.5 5.5H.5zM2.5 7h2.2v5H2.5zM6.9 7h2.2v5H6.9zM11.3 7h2.2v5h-2.2zM1 13.5h14V15H1z',
        // Coins piling up. This was a slotted money box, but the slot cut in the
        // lid read as a handle and the whole thing came out a briefcase.
        SAVINGS: 'M8 1.2c3.3 0 6 .8 6 1.8s-2.7 1.8-6 1.8-6-.8-6-1.8 2.7-1.8 6-1.8z' +
                 'M8 6.2c3.3 0 6 .8 6 1.8s-2.7 1.8-6 1.8-6-.8-6-1.8 2.7-1.8 6-1.8z' +
                 'M8 11.2c3.3 0 6 .8 6 1.8s-2.7 1.8-6 1.8-6-.8-6-1.8 2.7-1.8 6-1.8z',
        // A banknote, with the head cut out of the middle.
        CASH: 'M1 4h14v8H1zM8 6.3a1.7 1.7 0 1 0 0 3.4 1.7 1.7 0 0 0 0-3.4z',
        // A card with a chip. The chip is kept small and up in the corner: a large
        // one in the middle of the card made this indistinguishable from the hole
        // in the banknote above at the size these are actually read.
        DEBIT_CARD: 'M1.5 3h13A1.5 1.5 0 0 1 16 4.5v7a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 0 11.5v-7A1.5 1.5 0 0 1 1.5 3zM2.4 5.2h3v2.2h-3z',
        // The same card, banded by a magnetic stripe.
        CREDIT_CARD: 'M1.5 3h13A1.5 1.5 0 0 1 16 4.5v7a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 0 11.5v-7A1.5 1.5 0 0 1 1.5 3zM0 5.4h16v2.2H0z',
        // A bolt: money that only ever existed as a signal.
        ELECTRONIC: 'M9.8 1 3 9h3.7l-.7 6 6.9-8.4H9z',
        // A cut stone.
        ASSET: 'M8 1.4 14.8 6 8 14.6 1.2 6z',
        // An agreement, ruled with the terms you signed up to.
        LIABILITY: 'M3.5 1h6L13 4.5V15h-9.5zM5.5 7.2h5v1.3h-5zM5.5 10h5v1.3h-5z',
        // Nothing to say about it.
        OTHER: 'M2.6 6.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2zM8 6.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2zM13.4 6.4a1.6 1.6 0 1 0 0 3.2 1.6 1.6 0 0 0 0-3.2z'
    };
    // Their own mark is theirs, not ours to draw: an electronic account it is.
    ICONS.PAYPAL = ICONS.ELECTRONIC;

    function canonical(value) {
        return (value || '').trim().toUpperCase().replace(/ /g, '_');
    }

    /**
     * The mark for a type, as inline SVG that takes its size from the text it
     * sits in and its colour from whatever is around it. Empty for anything not
     * in the vocabulary, so an older value degrades to its label alone rather
     * than to a broken image.
     */
    function icon(value) {
        var path = ICONS[canonical(value)];
        if (!path) return '';
        return '<svg class="acct-type-icon" viewBox="0 0 16 16" aria-hidden="true">' +
               '<path fill-rule="evenodd" d="' + path + '"/></svg>';
    }

    /**
     * A value fit to show. Anything outside the vocabulary — an older database,
     * a hand-written API call — is tidied rather than dropped, so nothing ever
     * renders as blank or shouts in capitals.
     */
    function label(value) {
        var key = canonical(value);
        if (!key) return '';
        if (LABELS[key]) return LABELS[key];
        var words = key.toLowerCase().replace(/_/g, ' ');
        return words.charAt(0).toUpperCase() + words.slice(1);
    }

    /** `<option>` markup for a picker, with `selected` on the current value. */
    function options(selected) {
        var current = canonical(selected);
        var html = ORDER.map(function (t) {
            return '<option value="' + t + '"' + (t === current ? ' selected' : '') +
                   '>' + LABELS[t] + '</option>';
        }).join('');
        // An account already carrying a value we no longer offer keeps it, rather
        // than being silently retyped the first time someone edits its name.
        if (current && ORDER.indexOf(current) === -1) {
            html += '<option value="' + current + '" selected>' + label(current) + '</option>';
        }
        return html;
    }

    global.DelfinAccountTypes = {
        ORDER: ORDER,
        label: label,
        icon: icon,
        options: options,
        canonical: canonical
    };
})(window);
