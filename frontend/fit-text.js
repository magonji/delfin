/**
 * A figure that is too long for its cell gets smaller rather than cut off.
 *
 * The headline numbers sit in rows of equal cells, and on a phone a cell is a
 * quarter or a third of the screen: "$138,225.25" in the size it has on a wide
 * one does not fit. Each one watched here starts at its own size from the
 * stylesheet (or its own style attribute) and steps down only as far as it has to, so a short figure beside
 * a long one keeps its full size.
 *
 * A page says which ones on the tag that loads it --
 *     <script src="/app/fit-text.js" data-fit=".kpi-val, .stat-val"></script>
 * -- and it takes care of itself from there: a figure is fitted
 * again when its text changes, when its cell changes width (turning the phone,
 * or a section that was hidden appearing), and once the web fonts are in,
 * since the width of a number depends on which font it is drawn in.
 *
 * The figure has to be a block that cannot grow with its text -- white-space:
 * nowrap and overflow: hidden on it, and min-width: 0 on a grid or flex cell
 * that holds it -- or there is nothing to measure against. Its ellipsis stays
 * as the last resort, below the smallest size.
 */
(function (global) {
    'use strict';

    var MIN = 11;   // px; smaller than this a figure stops being a headline

    /**
     * Measured to the fraction of a pixel: scrollWidth and clientWidth are
     * whole numbers, so a figure over by less than one pixel came out
     * "fitting" and was cut to an ellipsis anyway.
     */
    function overflows(el, text) {
        return text.getBoundingClientRect().width > el.getBoundingClientRect().width;
    }

    function fit(el) {
        // Back to its own size before measuring: the stylesheet's, or one set
        // on the element itself, kept from before the first fitting.
        if (el.dataset.fitFrom === undefined) el.dataset.fitFrom = el.style.fontSize;
        el.style.fontSize = el.dataset.fitFrom;
        el.style.lineHeight = '';
        var box = el.getBoundingClientRect();
        if (!box.width) return;   // hidden: fitted when it shows
        var text = document.createRange();
        text.selectNodeContents(el);
        if (!overflows(el, text)) return;
        // The line keeps the height it had at full size, so a smaller figure
        // does not pull the line under it out of step with its neighbour's.
        el.style.lineHeight = box.height + 'px';
        var size = parseFloat(getComputedStyle(el).fontSize) || 22;
        while (size > MIN && overflows(el, text)) {
            size -= 1;
            el.style.fontSize = size + 'px';
        }
    }

    function watch(selector) {
        var els = Array.prototype.slice.call(document.querySelectorAll(selector));
        if (!els.length) return;
        function fitAll() { els.forEach(fit); }

        // Only the text is watched, not the attributes, so the font size set
        // here does not set it off again.
        var changed = new MutationObserver(function (records) {
            var seen = [];
            records.forEach(function (r) {
                var el = els.find(function (e) { return e.contains(r.target); });
                if (el && seen.indexOf(el) < 0) seen.push(el);
            });
            seen.forEach(fit);
        });
        // A block's width is its cell's, whatever its font size, so fitting
        // never resizes it and this cannot loop.
        var resized = global.ResizeObserver ? new ResizeObserver(function (entries) {
            entries.forEach(function (e) { fit(e.target); });
        }) : null;
        els.forEach(function (el) {
            changed.observe(el, { childList: true, characterData: true, subtree: true });
            if (resized) resized.observe(el);
        });
        if (!resized) global.addEventListener('resize', fitAll);
        if (document.fonts && document.fonts.ready) document.fonts.ready.then(fitAll);
        fitAll();
    }

    var asked = document.currentScript && document.currentScript.getAttribute('data-fit');
    if (asked) {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', function () { watch(asked); });
        } else {
            watch(asked);
        }
    }

    global.DelfinFit = { watch: watch, fit: fit };
})(window);
