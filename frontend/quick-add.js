/**
 * The one button in the corner of every page.
 *
 * There used to be two of them, side by side, and on four of the five pages
 * they were links: pressing one took you to the Transactions page and opened
 * the dialog there, so writing down a coffee from the dashboard cost a page
 * load and lost your place. Now there is one button, it opens what it offers
 * where you are, and it offers three things instead of two.
 *
 * Closed, it is a single dark circle. Under the pointer — or under a thumb, on
 * a screen that cannot hover — it splits upwards into the three: the one that
 * was there stays where it was, and the other two rise out from behind it, each
 * in its own colour so the three can be told apart without reading.
 *
 *     DelfinQuickAdd.install();     // once the forms have been configured
 */
(function (global) {
    'use strict';

    var STYLE = `
      .qa-root { position:fixed; right:28px; bottom:28px; z-index:70;
        display:flex; flex-direction:column-reverse; align-items:flex-end; gap:12px; }
      .qa-item { display:flex; align-items:center; gap:10px; }
      /* The two above the main one are folded into it until they are wanted. */
      .qa-root .qa-extra { opacity:0; pointer-events:none;
        transform:translateY(22px) scale(.55);
        transition:opacity .18s ease, transform .22s cubic-bezier(.34,1.4,.64,1); }
      .qa-root.qa-open .qa-extra { opacity:1; pointer-events:auto; transform:none; }
      /* Out one after the other, so the eye follows them up rather than being
         handed three buttons at once. */
      .qa-root.qa-open .qa-item:nth-child(2) .qa-extra { transition-delay:.03s; }
      .qa-root.qa-open .qa-item:nth-child(3) .qa-extra { transition-delay:.08s; }

      .qa-btn { border:none; cursor:pointer; display:flex; align-items:center; justify-content:center;
        border-radius:50%; color:#F6EFE3; transition:transform .18s ease, box-shadow .18s ease, filter .18s ease; }
      .qa-btn:hover { transform:translateY(-2px) scale(1.06); }
      .qa-btn:active { transform:translateY(0) scale(.97); }
      .qa-btn svg { width:22px; height:22px; fill:none; stroke:currentColor;
        stroke-width:1.9; stroke-linecap:round; stroke-linejoin:round; }
      .qa-main { width:60px; height:60px; background:#2B2621;
        box-shadow:0 12px 30px rgba(40,28,14,.4); }
      .qa-main svg { width:26px; height:26px; stroke-width:2; transition:transform .22s ease; }
      .qa-root.qa-open .qa-main svg { transform:rotate(90deg); }
      /* Narrower than the main one, and nudged in by half the difference so the
         three share a centre line rather than only a right edge. */
      .qa-extra { width:50px; height:50px; margin-right:5px; box-shadow:0 8px 20px rgba(40,28,14,.3); }
      .qa-transfer { background:#3C5A6E; }
      .qa-account { background:#3C7A57; }

      /* The name of each, on its left, where it does not cover the page. */
      .qa-label { font-family:'IBM Plex Sans',sans-serif; font-size:12.5px; font-weight:600;
        letter-spacing:.2px; color:#3A342C; background:var(--paper,#FFFDFA);
        border:1px solid var(--hair,#EFE4D3); border-radius:8px; padding:6px 10px;
        box-shadow:0 6px 16px rgba(40,28,14,.14); white-space:nowrap;
        opacity:0; transform:translateX(6px); transition:opacity .16s ease, transform .16s ease; }
      .qa-root.qa-open .qa-label { opacity:1; transform:none; }
      .qa-root.qa-open .qa-item:nth-child(1) .qa-label { transition-delay:.06s; }

      @media (max-width:600px) {
        .qa-root { right:18px; bottom:calc(18px + env(safe-area-inset-bottom)); }
        .qa-main { width:58px; height:58px; }
        .qa-extra { width:50px; height:50px; }
      }
      @media (prefers-reduced-motion: reduce) {
        .qa-root .qa-extra, .qa-label, .qa-main svg { transition:none; }
      }`;

    var ICONS = {
        // A plus that turns into a cross as the fan opens: same mark, rotated.
        transaction: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14"/><path d="M5 12h14"/></svg>',
        transfer: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 8h14"/><path d="M13 4l4 4-4 4"/>'
                + '<path d="M21 16H7"/><path d="M11 12l-4 4 4 4"/></svg>',
        account: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 10l9-6 9 6"/>'
               + '<path d="M5 10v9"/><path d="M10 10v9"/><path d="M14 10v9"/><path d="M19 10v9"/>'
               + '<path d="M3 20h18"/></svg>',
    };

    var root = null;
    var closeTimer = null;
    // A screen that cannot hover has to be told what to do with a tap instead.
    var canHover = global.matchMedia && global.matchMedia('(hover: hover)').matches;

    function open() {
        clearTimeout(closeTimer);
        root.classList.add('qa-open');
        root.querySelector('.qa-main').setAttribute('aria-expanded', 'true');
    }

    function close() {
        clearTimeout(closeTimer);
        closeTimer = setTimeout(function () {
            root.classList.remove('qa-open');
            root.querySelector('.qa-main').setAttribute('aria-expanded', 'false');
        }, canHover ? 160 : 0);
    }

    function isOpen() { return root.classList.contains('qa-open'); }

    function run(action) {
        root.classList.remove('qa-open');
        if (action === 'transfer') return global.DelfinTxForm.openTransfer();
        if (action === 'account') return global.DelfinAccountForm.open();
        return global.DelfinTxForm.openTransaction();
    }

    function install(options) {
        if (document.querySelector('.qa-root')) return;
        options = options || {};

        var style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);

        root = document.createElement('div');
        root.className = 'qa-root';
        root.innerHTML =
            '<div class="qa-item">' +
              '<span class="qa-label">New transaction</span>' +
              '<button type="button" class="qa-btn qa-main" aria-haspopup="true" aria-expanded="false"' +
              ' title="New transaction">' + ICONS.transaction + '</button>' +
            '</div>' +
            '<div class="qa-item">' +
              '<span class="qa-label">New transfer</span>' +
              '<button type="button" class="qa-btn qa-extra qa-transfer" title="New transfer"' +
              ' data-action="transfer">' + ICONS.transfer + '</button>' +
            '</div>' +
            '<div class="qa-item">' +
              '<span class="qa-label">New account</span>' +
              '<button type="button" class="qa-btn qa-extra qa-account" title="New account"' +
              ' data-action="account">' + ICONS.account + '</button>' +
            '</div>';
        document.body.appendChild(root);

        if (canHover) {
            root.addEventListener('mouseenter', open);
            root.addEventListener('mouseleave', close);
        }
        root.addEventListener('focusin', open);

        root.querySelector('.qa-main').addEventListener('click', function () {
            // With a pointer the fan is already open and the button means what it
            // says. With a thumb the first tap is what opens it.
            if (!canHover && !isOpen()) { open(); return; }
            run('transaction');
        });
        root.querySelectorAll('.qa-extra').forEach(function (btn) {
            btn.addEventListener('click', function () { run(btn.dataset.action); });
        });

        // Anywhere else, and it folds back up.
        document.addEventListener('click', function (e) {
            if (isOpen() && !root.contains(e.target)) close();
        });
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && isOpen()) close();
        });
    }

    global.DelfinQuickAdd = { install: install, open: open, close: close };
})(window);
