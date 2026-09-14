/**
 * The bar along the bottom of a phone.
 *
 * Five pages behind a hamburger is two taps and a guess for every move: open the
 * menu, find the word, tap it. On a phone the thumb is at the bottom of the
 * screen and the menu is at the top corner. So the five become a row of tabs
 * where the thumb already is, the way they are in an app, and the hamburger --
 * which on a phone held nothing but those five and two actions -- goes away.
 *
 * The two actions it held, refreshing and logging out, come back as the icons
 * they already are in the header on a wide screen: they were only hidden there
 * because the burger had taken them in.
 *
 * It installs itself, so a page only has to load it. Which page is showing is
 * read from the address rather than passed in, so there is nothing to keep in
 * step when a page is added.
 *
 * Its own colours, like the dialogs: the five pages do not agree on the names
 * (the dashboard calls --ink a blue and has no --accent at all), and a bar that
 * borrowed them would come out a different colour on every page.
 */
(function (global) {
    'use strict';

    var BREAKPOINT = 760;   // where each page already swaps its nav for a burger

    var PAGES = [
        {
            file: 'index.html', label: 'Dashboard',
            icon: '<path d="M4 20V13"/><path d="M10 20V5"/><path d="M16 20v-9"/><path d="M3 20h18"/>',
        },
        {
            file: 'transactions.html', label: 'Transactions',
            icon: '<path d="M7 20V5"/><path d="M4 8l3-3 3 3"/><path d="M17 4v15"/><path d="M20 16l-3 3-3-3"/>',
        },
        {
            file: 'budget.html', label: 'Budget',
            // A dial, not a pie and not a clock: a budget is how much of the
            // month's allowance has gone, which is a needle against an arc. The
            // circle with two lines out of the middle that would have said "pie"
            // says "half past five" instead at this size.
            icon: '<path d="M3.6 17.2a8.4 8.4 0 0 1 16.8 0"/><path d="M12 17.2l4.4-4.9"/>'
                + '<circle cx="12" cy="17.2" r="1.1"/>',
        },
        {
            file: 'loans.html', label: 'Loans',
            icon: '<rect x="2.5" y="6" width="19" height="12" rx="2.5"/><circle cx="12" cy="12" r="2.6"/>'
                + '<path d="M6 12h.01"/><path d="M18 12h.01"/>',
        },
        {
            file: 'tools.html', label: 'Tools',
            icon: '<circle cx="12" cy="12" r="3.2"/><path d="M12 2.5v3"/><path d="M12 18.5v3"/>'
                + '<path d="M2.5 12h3"/><path d="M18.5 12h3"/><path d="M5.2 5.2l2.1 2.1"/>'
                + '<path d="M16.7 16.7l2.1 2.1"/><path d="M18.8 5.2l-2.1 2.1"/><path d="M7.3 16.7l-2.1 2.1"/>',
        },
    ];

    // Tall enough for a thumb (the row itself clears 44px) without eating the
    // page: the icon, a hair of space, and the word.
    var HEIGHT = 62;

    var STYLE = ''
        /* How much of the bottom of the screen the bar is taking, published so a
           page that sizes something against the height of the window -- the
           ledger's own scrolling list does -- can subtract it without having to
           know the number. Zero on a wide screen, where there is no bar. */
        + ':root { --dlf-tabbar: 0px; }'
        + '.dlf-tabbar { display: none; }'
        + '@media (max-width: ' + BREAKPOINT + 'px) {'
        + '  :root { --dlf-tabbar: calc(' + HEIGHT + 'px + env(safe-area-inset-bottom)); }'
        + '  .dlf-tabbar { display: flex; position: fixed; left: 0; right: 0; bottom: 0; z-index: 75;'
        + '    background: #FFFDFA; border-top: 1px solid #EFE4D3;'
        + '    padding-bottom: env(safe-area-inset-bottom);'
        + '    box-shadow: 0 -6px 22px rgba(40,28,14,.07); }'
        + '  .dlf-tabbar a { position: relative; flex: 1 1 0; min-width: 0; display: flex;'
        + '    flex-direction: column; align-items: center; justify-content: center; gap: 3px;'
        + '    height: ' + HEIGHT + 'px; padding: 0 2px; text-decoration: none; color: #8A7D6C;'
        + "    font-family: 'IBM Plex Sans', sans-serif; font-size: 10px; font-weight: 600;"
        + '    letter-spacing: .2px; -webkit-tap-highlight-color: transparent; }'
        + '  .dlf-tabbar a:hover { text-decoration: none; }'
        + '  .dlf-tabbar a span { max-width: 100%; overflow: hidden; text-overflow: ellipsis;'
        + '    white-space: nowrap; }'
        + '  .dlf-tabbar svg { width: 22px; height: 22px; display: block; fill: none;'
        + '    stroke: currentColor; stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }'
        /* The page you are on, marked the way the wide screen marks it: the word
           in the text colour, and the red rule that sits under a link up there
           sitting over a tab down here. */
        + '  .dlf-tabbar a.is-on { color: #23201C; }'
        + '  .dlf-tabbar a.is-on::before { content: ""; position: absolute; top: -1px; left: 22%;'
        + '    right: 22%; height: 2px; border-radius: 0 0 2px 2px; background: #B0402E; }'
        + '  .dlf-tabbar a.is-on svg { stroke-width: 1.9; }'
        /* Room for it, so the last line of a page is not left underneath. */
        + '  body { padding-bottom: var(--dlf-tabbar); }'
        /* The burger held these five and the two actions; the five are here now,
           and the two go back to being the icons they already are on a wide
           screen, where they were only hidden because the burger had them. */
        + '  .burger { display: none !important; }'
        + '  .nav-right .tip-wrap { display: inline-flex !important; }'
        + '  .nav-right > #btnLogout { display: flex !important; }'
        + '}';

    function currentFile() {
        var path = global.location.pathname;
        var last = path.slice(path.lastIndexOf('/') + 1);
        return last || 'index.html';
    }

    function install() {
        if (document.querySelector('.dlf-tabbar')) return;

        var style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);

        var here = currentFile();
        var bar = document.createElement('nav');
        bar.className = 'dlf-tabbar';
        bar.setAttribute('aria-label', 'Sections');
        bar.innerHTML = PAGES.map(function (page) {
            var on = page.file === here;
            return '<a href="' + page.file + '"' + (on ? ' class="is-on" aria-current="page"' : '') + '>'
                 + '<svg viewBox="0 0 24 24" aria-hidden="true">' + page.icon + '</svg>'
                 + '<span>' + page.label + '</span></a>';
        }).join('');
        document.body.appendChild(bar);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', install);
    } else {
        install();
    }

    global.DelfinTabBar = { install: install };
})(window);
