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

    function canonical(value) {
        return (value || '').trim().toUpperCase().replace(/ /g, '_');
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
        options: options,
        canonical: canonical
    };
})(window);
