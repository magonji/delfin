/**
 * The loan terms form, in one place because two pages need it.
 *
 * It used to live inside loans.html, which was fine while opening a loan and
 * correcting one both happened there. Opening an account now happens under
 * Tools, and a loan is an account with a contract attached — so the same form
 * has to be reachable from both, and copying five hundred lines into the second
 * page was never going to stay in step with the first.
 *
 * It brings its own markup and its own styling, namespaced under the modal's id.
 * The two pages show dialogs differently — one toggles a class, the other sets a
 * display — and depending on either would have made the module work in one place
 * and not the other. What it does not bring is money formatting: the host knows
 * how it wants figures written, and passes that in.
 *
 *     DelfinLoanForm.configure({ apiBase, formatMoney, onSaved });
 *     DelfinLoanForm.open();                       // a new loan
 *     DelfinLoanForm.open({ accountId: 12 });      // terms for an account that exists
 *     DelfinLoanForm.open({ editingId: 3, currency: 'EUR' });   // correct them
 */
(function (global) {
    'use strict';

    var cfg = {
        apiBase: '',
        defaultCurrency: 'GBP',
        // How the host writes money and rates. Plain fallbacks so the module is
        // usable without being told.
        formatMoney: function (amount, currency) {
            return (currency ? currency + ' ' : '') +
                Math.abs(amount).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
        },
        formatApr: function (r) { return parseFloat((r || 0).toFixed(2)).toString(); },
        formatRate: function (r) { return parseFloat((r || 0).toFixed(3)).toString(); },
        // Called after a successful save or delete, so the host can refresh.
        onSaved: function () {},
        onSuccess: function (message) {},
        // Lets a host that disambiguates currency symbols learn the codes in play.
        noteCurrencies: function (codes) {},
    };

    var accounts = [];        // accounts the money can be paid into
    var loanAccounts = [];    // debt accounts with no agreed terms yet
    var editingId = null;
    var boundCurrency = null;   // the loan's own currency, when it is already settled
    var boundAccount = null;    // the account the terms belong to, when the host names it
    var attached = false;       // true while the fields sit inside a host's modal
    var quoteTimer = null;
    var quoteToken = 0;

    function q(sel) { return document.querySelector(sel); }
    function esc(v) {
        return String(v == null ? '' : v)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;')
            .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    }

    var STYLE = `
      #dlfLoanModal { display:none; position:fixed; z-index:1200; inset:0;
        background-color:rgba(35,32,28,.4); backdrop-filter:blur(3px);
        align-items:center; justify-content:center; padding:18px; }
      #dlfLoanModal.dlf-open { display:flex; }
      .dlf-content { background-color:var(--paper); border-radius:16px; width:100%;
        max-width:460px; max-height:90vh; overflow-y:auto; border:1px solid var(--hair);
        box-shadow:0 24px 60px rgba(40,28,14,.35); font-family:'IBM Plex Sans',sans-serif; }
      .dlf-head { display:flex; justify-content:space-between; align-items:center;
        gap:12px; padding:18px 22px; border-bottom:1px solid var(--hair); }
      .dlf-head h3 { margin:0; font-family:'Playfair Display',serif; font-size:21px;
        font-weight:700; padding-left:12px; border-left:3px solid var(--accent); color:var(--ink); }
      .dlf-x { background:var(--field); border:none; border-radius:8px; width:32px;
        height:32px; font-size:18px; line-height:1; cursor:pointer; color:#3A342C; flex:0 0 auto; }
      .dlf-body { padding:0; }
      .dlf-foot { display:flex; justify-content:flex-end; gap:8px; padding:16px 22px;
        border-top:1px solid var(--hair); }
      .dlf-foot .dlf-danger { margin-right:auto; }
      /* The same shape as every other form in the app: a label on the left, its
         value on the right, one field to a line, ruled between. See forms.css --
         it is written out here as well because this module carries its own
         styling and is loaded by a page that has none. */
      .dlf-g { display:flex; align-items:center; gap:10px; min-height:52px; margin:0;
        padding:2px 12px; background:#fff; border-bottom:1px solid var(--hair); }
      #dlfLoanForm label { flex:0 0 84px; margin:0; font-weight:700; font-size:10px;
        line-height:1.25; letter-spacing:1px; text-transform:uppercase; color:var(--muted); }
      #dlfLoanForm input, #dlfLoanForm select { flex:1 1 auto; min-width:0; width:auto;
        padding:11px 0; min-height:46px; border:none; border-radius:0; background:none;
        font-size:15px; font-family:'IBM Plex Sans',sans-serif; color:var(--ink);
        -webkit-appearance:none; appearance:none; box-sizing:border-box; }
      #dlfLoanForm input:focus, #dlfLoanForm select:focus { outline:none; }
      .dlf-g:focus-within { box-shadow:inset 2px 0 0 var(--accent); }
      /* What a field means, when the label cannot carry it: under the field it
         belongs to, not between the rows. */
      .dlf-hint { margin:0; padding:8px 12px 10px; background:#fff;
        border-bottom:1px solid var(--hair); font-size:11.5px; color:var(--muted); line-height:1.45; }
      .dlf-row { display:flex; align-items:center; gap:10px; flex:1 1 auto; min-width:0; }
      .dlf-row input { flex:0 0 78px; }
      .dlf-error { color:var(--red); font-size:12.5px; margin-bottom:12px; display:none; }
      .dlf-quote { background:var(--panel); border:1px solid var(--hair);
        border-radius:10px; padding:12px 14px; margin-bottom:16px; }
      .dlf-quote-label { font-size:10px; font-weight:600; letter-spacing:1.5px;
        text-transform:uppercase; color:var(--muted); }
      .dlf-quote-value { font-family:'IBM Plex Mono',monospace; font-size:19px;
        font-weight:600; margin-top:4px; color:var(--ink); }
      .dlf-quote-sub { font-family:'IBM Plex Mono',monospace; font-size:11px;
        color:var(--muted); margin-top:4px; }
      .dlf-btn { padding:0 18px; height:44px; border-radius:9px;
        border:1px solid var(--field); cursor:pointer; font-family:'Playfair Display',serif;
        font-weight:500; font-size:14px; background:#fff; color:#3A342C; }
      .dlf-primary { background:#2B2621; color:#F6EFE3; border-color:#2B2621; }
      .dlf-secondary { background:var(--panel); border-color:var(--panel); }
      .dlf-danger { background:#FBEDE9; color:var(--red); border-color:#E7C3B8; }
      .dlf-btn:disabled { opacity:.55; cursor:default; }
      @media (max-width:600px) {
        #dlfLoanForm input, #dlfLoanForm select, .dlf-btn { min-height:44px; font-size:16px; }
      }`;

    var FORM_MARKUP = `<div id="dlfLoanForm">
            <div class="dlf-error" id="loanError"></div>
            <div class="dlf-hint" id="loanEditHint" style="display:none;">
              Editing the terms leaves the account and its movements exactly as they are. If the amount borrowed was wrong, correct the drawdown from the Transactions page too.
            </div>

            <div class="dlf-g" id="loanModeGroup">
              <label>This loan</label>
              <select id="loanMode" onchange="onLoanModeChange()">
                <option value="new">is new — open an account for it</option>
                <option value="existing">already exists — add its terms</option>
              </select>
            </div>
            <div class="dlf-g" id="loanExistingGroup" style="display:none;">
              <label>Which account</label>
              <select id="loanExistingAccount" onchange="onExistingAccountChange()"></select>
            </div>
            <div class="dlf-hint" id="loanExistingHint" style="display:none;">
              Its movements stay exactly as they are — the terms only replace the estimate with the real schedule.
            </div>

            <div class="dlf-g" id="loanNameGroup">
              <label>Name</label>
              <input type="text" id="loanName" placeholder="e.g., Flat mortgage, Car loan" oninput="quoteLoan()">
            </div>
            <div class="dlf-g">
              <label>Borrowed</label>
              <input type="number" id="loanPrincipal" step="0.01" min="0" inputmode="decimal" oninput="quoteLoan()">
            </div>
            <div class="dlf-g">
              <label>Annual rate (%)</label>
              <input type="number" id="loanRate" step="0.01" min="0" value="0" inputmode="decimal" oninput="quoteLoan()">
            </div>
            <div class="dlf-g">
              <label>Arrangement fee</label>
              <input type="number" id="loanFee" step="0.01" min="0" value="0" inputmode="decimal" oninput="onLoanFeeChange()">
            </div>
            <div class="dlf-g" id="loanFeeTreatmentGroup" style="display:none;">
              <label>The fee was</label>
              <select id="loanFeeTreatment" onchange="quoteLoan()">
                <option value="upfront">paid at the outset, out of the money received</option>
                <option value="capitalised">added to the loan and repaid with it</option>
              </select>
            </div>
            <div class="dlf-hint" id="loanFeeHint" style="display:none;">
              Any product, arrangement or broker fee. It is not interest, so it leaves the rate above alone — but it does raise what the loan really costs, which is what the effective rate below shows.
            </div>
            <div class="dlf-g">
              <label>Standing fee</label>
              <div class="dlf-row">
                <input type="number" id="loanRecurringFee" step="0.01" min="0" value="0" inputmode="decimal" oninput="onLoanFeeChange()">
                <select id="loanRecurringFeeMonths" onchange="quoteLoan()">
                  <option value="1" selected>monthly</option>
                  <option value="3">quarterly</option>
                  <option value="6">every six months</option>
                  <option value="12">annually</option>
                </select>
              </div>
            </div>
            <div class="dlf-hint" id="loanRecurringFeeHint" style="display:none;">
              An administration or account fee charged for as long as the loan runs. It counts towards the effective rate too, and needn't share the instalments' rhythm.
            </div>
            <div class="dlf-g">
              <label>Early repayment (%)</label>
              <input type="number" id="loanEarlyFee" step="0.01" min="0" value="0" inputmode="decimal" oninput="onLoanFeeChange()">
            </div>
            <div class="dlf-hint" id="loanEarlyFeeHint" style="display:none;">
              Charged only if the loan is settled before the end of its term, so it stays out of the schedule and out of the effective rate. It prices one thing: clearing the loan today.
            </div>
            <div class="dlf-g">
              <label>Opening date</label>
              <input type="date" id="loanOpenDate" onchange="quoteLoan()">
            </div>
            <div class="dlf-hint">Interest starts here, on the day the money is drawn down.</div>
            <div class="dlf-g">
              <label>First payment</label>
              <input type="date" id="loanFirstPayment" onchange="quoteLoan()">
            </div>
            <div class="dlf-hint">
              Optional. Leave it empty and the first instalment falls one period after the opening date, on the day chosen below. Set it when it doesn't — the first instalment then carries only the interest that has actually accrued, which is more or less than a full one.
            </div>
            <div class="dlf-g">
              <label>Duration</label>
              <div class="dlf-row">
                <input type="number" id="loanTermCount" min="1" step="1" value="25" inputmode="numeric" oninput="quoteLoan()">
                <select id="loanTermUnit" onchange="quoteLoan()">
                  <option value="year" selected>years</option>
                  <option value="month">months</option>
                </select>
              </div>
            </div>
            <div class="dlf-g">
              <label>Repayment</label>
              <select id="loanRepaymentType" onchange="quoteLoan()">
                <option value="french">constant instalment (capital and interest)</option>
                <option value="interest_only">interest only, capital at the end</option>
                <option value="constant_principal">constant capital, falling instalment</option>
              </select>
            </div>
            <div class="dlf-g">
              <label>Interest</label>
              <select id="loanInterestFrequency" onchange="onLoanInterestFrequencyChange()">
                <option value="day">daily</option>
                <option value="1" selected>monthly</option>
                <option value="3">quarterly</option>
                <option value="6">every six months</option>
                <option value="12">annually</option>
              </select>
            </div>
            <div class="dlf-hint" id="loanDailyHint" style="display:none;">
              Interest follows the real days of each period, so a February instalment carries less than a March one and a leap year costs a day more. The instalment itself stays level — the difference lands in the final payment.
            </div>
            <div class="dlf-g">
              <label>Instalments</label>
              <select id="loanPaymentMonths" onchange="quoteLoan()">
                <option value="1" selected>monthly</option>
                <option value="3">quarterly</option>
                <option value="6">every six months</option>
                <option value="12">annually</option>
              </select>
            </div>
            <div class="dlf-g">
              <label>Falls on</label>
              <select id="loanDayRule" onchange="onLoanDayRuleChange()">
                <option value="exact">a fixed day of the month</option>
                <option value="working_from_start">a working day counted from the 1st</option>
                <option value="working_from_end">a working day counted from the end</option>
              </select>
            </div>
            <div class="dlf-g" id="loanDayOfMonthGroup">
              <label>Day of the month</label>
              <select id="loanDayOfMonth" onchange="quoteLoan()"></select>
            </div>
            <div class="dlf-g" id="loanDayOrdinalGroup" style="display:none;">
              <label id="loanDayOrdinalLabel">Which working day</label>
              <select id="loanDayOrdinal" onchange="quoteLoan()">
                <option value="1">1st</option>
                <option value="2">2nd</option>
                <option value="3">3rd</option>
                <option value="4">4th</option>
                <option value="5">5th</option>
              </select>
            </div>
            <div class="dlf-hint" id="loanDayHint" style="display:none;">
              Working means Monday to Friday — bank holidays aren't known, so a real payment may shift by a day.
            </div>
            <div class="dlf-g">
              <label>Lender</label>
              <input type="text" id="loanLender" list="loanLenderList" placeholder="e.g., Santander" autocomplete="off">
              <datalist id="loanLenderList"></datalist>
            </div>
            <div class="dlf-g" id="loanDestinationGroup">
              <label>Paid into</label>
              <select id="loanDestination" onchange="quoteLoan()"></select>
            </div>
            <div class="dlf-hint" id="loanDestinationHint">
              The drawdown is booked as a transfer from the new loan account into this one, dated the opening date.
            </div>

            <div class="dlf-quote" id="loanQuote" style="display:none;">
              <div class="dlf-quote-label" id="loanQuoteLabel">Instalment</div>
              <div class="dlf-quote-value" id="loanQuoteValue">—</div>
              <div class="dlf-quote-sub" id="loanQuoteSub"></div>
            </div>
</div>`;

    var MARKUP = `
      <div id="dlfLoanModal">
        <div class="dlf-content">
          <div class="dlf-head">
            <h3 id="loanModalTitle">Add loan</h3>
            <button class="dlf-x" onclick="closeLoanModal()">&times;</button>
          </div>
          <div class="dlf-body" id="dlfLoanHome"></div>
          <div class="dlf-foot">
            <button class="dlf-btn dlf-danger" id="loanDeleteBtn" onclick="deleteLoanTerms()" style="display:none;">Delete terms</button>
            <button class="dlf-btn dlf-secondary" onclick="closeLoanModal()">Cancel</button>
            <button class="dlf-btn dlf-primary" id="loanSaveBtn" onclick="saveLoan()">Save</button>
          </div>
        </div>
      </div>`;

    function mount() {
        if (document.getElementById('dlfLoanModal')) return;
        var style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);
        document.body.insertAdjacentHTML('beforeend', MARKUP);
        q('#dlfLoanHome').insertAdjacentHTML('beforeend', FORM_MARKUP);
        // A click on the backdrop itself — never on the form sitting on it — closes.
        document.getElementById('dlfLoanModal').addEventListener('click', function (e) {
            if (e.target.id === 'dlfLoanModal') global.closeLoanModal();
        });
    }

    /**
     * There is one set of fields, and it goes where it is needed: its own dialog
     * on the Loans page, or inside the Add account dialog under Tools. One set,
     * because the ids the handlers are written against can only exist once.
     */
    function moveTo(host) {
        mount();
        host.appendChild(q('#dlfLoanForm'));
    }

    /** Hides what the host is already asking for, so nothing is asked twice. */
    function applyChrome() {
        var editing = editingId !== null;
        // Attached, the host owns the name — an account's name is the loan's name.
        q('#loanNameGroup').style.display = attached ? 'none' : '';
        q('#loanModeGroup').style.display = (attached || editing) ? 'none' : '';
    }

    async function loanRequest(path, method, body) {
        var res = await fetch(cfg.apiBase + path, {
            method: method,
            headers: { 'Content-Type': 'application/json' },
            body: body ? JSON.stringify(body) : undefined,
        });
        if (!res.ok) {
            var err = await res.json().catch(function () { return {}; });
            // A rejected field comes back as a list of problems, not a sentence.
            var detail = Array.isArray(err.detail)
                ? err.detail.map(function (d) { return d.msg; }).join('; ')
                : err.detail;
            throw new Error(detail || (res.status + ' ' + res.statusText));
        }
        return res.json();
    }

    function readLoanForm() {
        var frequency = q('#loanInterestFrequency').value;
        var openDate = q('#loanOpenDate').value;
        var firstPayment = q('#loanFirstPayment').value;
        var rule = q('#loanDayRule').value;
        return {
            name: q('#loanName').value.trim() || 'Loan',
            principal: parseFloat(q('#loanPrincipal').value) || 0,
            annual_rate: parseFloat(q('#loanRate').value) || 0,
            open_date: openDate ? openDate + 'T00:00:00' : null,
            first_payment_date: firstPayment ? firstPayment + 'T00:00:00' : null,
            term_count: parseInt(q('#loanTermCount').value) || 1,
            term_unit: q('#loanTermUnit').value,
            repayment_type: q('#loanRepaymentType').value,
            interest_unit: frequency === 'day' ? 'day' : 'month',
            interest_months: frequency === 'day' ? 1 : (parseInt(frequency) || 1),
            payment_months: parseInt(q('#loanPaymentMonths').value) || 1,
            day_rule: rule,
            day_ordinal: rule === 'exact' ? null : parseInt(q('#loanDayOrdinal').value) || 1,
            day_of_month: rule === 'exact' ? parseInt(q('#loanDayOfMonth').value) || null : null,
            opening_fee: Math.max(0, parseFloat(q('#loanFee').value) || 0),
            fee_treatment: q('#loanFeeTreatment').value,
            recurring_fee: Math.max(0, parseFloat(q('#loanRecurringFee').value) || 0),
            recurring_fee_months: parseInt(q('#loanRecurringFeeMonths').value) || 1,
            early_repayment_fee_pct: Math.max(0, parseFloat(q('#loanEarlyFee').value) || 0),
            lender_name: q('#loanLender').value.trim() || null,
        };
    }

    function resetLoanForm() {
        ['loanName', 'loanPrincipal', 'loanLender', 'loanFirstPayment']
            .forEach(function (id) { q('#' + id).value = ''; });
        ['loanRate', 'loanFee', 'loanRecurringFee', 'loanEarlyFee']
            .forEach(function (id) { q('#' + id).value = '0'; });
        editingId = null;
        boundCurrency = null;
        global.onLoanFeeChange();
    }

    function showLoanError(msg) {
        var el = q('#loanError');
        el.textContent = msg;
        el.style.display = 'block';
    }

    function currencyForLoan() {
        if (boundCurrency) return boundCurrency;
        var acc;
        if (isExisting()) {
            acc = loanAccounts.find(function (a) { return String(a.id) === q('#loanExistingAccount').value; });
        } else {
            acc = accounts.find(function (a) { return String(a.id) === q('#loanDestination').value; });
        }
        return (acc && acc.currency) || cfg.defaultCurrency;
    }

    /** Whether the terms are being pinned to an account that already exists. */
    function isExisting() {
        if (boundAccount !== null) return true;
        if (attached || editingId !== null) return false;
        return q('#loanMode').value === 'existing';
    }

    /** The account the terms belong to, or null when the loan opens its own. */
    function targetAccountId() {
        if (boundAccount !== null) return boundAccount;
        return isExisting() ? parseInt(q('#loanExistingAccount').value) : null;
    }

    // ---- the handlers the markup calls, which have to be reachable by name ----

    global.onLoanModeChange = function () {
        // Editing settles the account question by having answered it already, and
        // so does a host that opened the form on an account of its own.
        var editing = editingId !== null;
        var picked = !editing && boundAccount === null && !attached
            && q('#loanMode').value === 'existing';
        q('#loanExistingGroup').style.display = picked ? '' : 'none';
        q('#loanExistingHint').style.display = picked ? 'block' : 'none';
        // An account that already carries its drawdown must not be given a second one.
        var wantsDestination = !editing && !isExisting();
        q('#loanDestinationGroup').style.display = wantsDestination ? '' : 'none';
        q('#loanDestinationHint').style.display = wantsDestination ? 'block' : 'none';
        // Whoever chose the account — the picker, or the host that opened the form
        // on one — what is already known about it fills the form in.
        if (isExisting() && q('#loanExistingAccount').value) global.onExistingAccountChange();
        global.quoteLoan();
    };

    global.onExistingAccountChange = function () {
        var wanted = String(targetAccountId() || q('#loanExistingAccount').value);
        var acc = loanAccounts.find(function (a) { return String(a.id) === wanted; });
        if (!acc) return;
        if (!q('#loanName').value) q('#loanName').value = acc.name;
        if (acc.open_date) q('#loanOpenDate').value = acc.open_date.slice(0, 10);
        if (acc.borrowed > 0 && !q('#loanPrincipal').value) q('#loanPrincipal').value = acc.borrowed;
        global.quoteLoan();
    };

    global.onLoanFeeChange = function () {
        var hasFee = (parseFloat(q('#loanFee').value) || 0) > 0;
        q('#loanFeeTreatmentGroup').style.display = hasFee ? '' : 'none';
        q('#loanFeeHint').style.display = hasFee ? 'block' : 'none';
        q('#loanRecurringFeeHint').style.display =
            (parseFloat(q('#loanRecurringFee').value) || 0) > 0 ? 'block' : 'none';
        q('#loanEarlyFeeHint').style.display =
            (parseFloat(q('#loanEarlyFee').value) || 0) > 0 ? 'block' : 'none';
        global.quoteLoan();
    };

    global.onLoanDayRuleChange = function () {
        var rule = q('#loanDayRule').value;
        var working = rule !== 'exact';
        q('#loanDayOfMonthGroup').style.display = working ? 'none' : '';
        q('#loanDayOrdinalGroup').style.display = working ? '' : 'none';
        q('#loanDayHint').style.display = working ? 'block' : 'none';
        q('#loanDayOrdinalLabel').textContent = rule === 'working_from_end'
            ? 'Which working day, counting back' : 'Which working day';
        global.quoteLoan();
    };

    global.onLoanInterestFrequencyChange = function () {
        q('#loanDailyHint').style.display =
            q('#loanInterestFrequency').value === 'day' ? 'block' : 'none';
        global.quoteLoan();
    };

    global.quoteLoan = function () {
        clearTimeout(quoteTimer);
        quoteTimer = setTimeout(runQuote, 250);
    };

    async function runQuote() {
        var box = q('#loanQuote');
        var body = readLoanForm();
        if (!(body.principal > 0) || !body.open_date) {
            box.style.display = 'none';
            return;
        }

        var token = ++quoteToken;
        var sc;
        try {
            sc = (await loanRequest('/loans/preview', 'POST', body)).schedule;
        } catch (e) {
            box.style.display = 'none';
            return;
        }
        if (token !== quoteToken) return;  // a later keystroke has overtaken this
        if (!sc || !sc.payments_total) {
            box.style.display = 'none';
            return;
        }

        var cur = currencyForLoan();
        var money = function (n) { return cfg.formatMoney(n, cur); };
        var every = { 1: 'monthly', 3: 'quarterly', 6: 'every six months', 12: 'annually' };
        box.style.display = 'block';
        q('#loanQuoteLabel').textContent =
            body.repayment_type === 'constant_principal' ? 'First instalment' : 'Instalment';
        q('#loanQuoteValue').textContent =
            money(sc.instalment) + ' ' + (every[body.payment_months] || '');

        var bits = [sc.payments_total + ' payment' + (sc.payments_total === 1 ? '' : 's')];
        if (sc.recurring_fee > 0) bits.push('plus ' + money(sc.recurring_fee) + ' standing fee');
        if (body.repayment_type === 'interest_only') {
            bits.push('then ' + money(sc.financed_principal) + ' at the end');
        } else {
            bits.push(money(sc.total_interest) + ' interest in total');
        }
        if (sc.effective_rate != null) bits.push(cfg.formatApr(sc.effective_rate) + '% effective');
        if (sc.first_period_is_stub) {
            bits.push('first period ' + sc.first_period_days + ' days'
                + ' (' + money(sc.first_period_interest) + ' interest,'
                + ' ' + (sc.first_period_days > sc.period_days ? 'more' : 'less') + ' than a full one)');
        }
        // The early repayment charge prices leaving, not staying: it belongs
        // nowhere near the rate above.
        if (sc.early_repayment_fee_pct > 0) {
            bits.push(cfg.formatRate(sc.early_repayment_fee_pct) + '% to settle early');
        }
        q('#loanQuoteSub').textContent = bits.join(' · ');
    }

    global.closeLoanModal = function () {
        var m = document.getElementById('dlfLoanModal');
        if (m) m.classList.remove('dlf-open');
        editingId = null;
        boundCurrency = null;
        boundAccount = null;
    };

    /**
     * Writes the terms, whether the form is standing on its own or sitting inside
     * a host's dialog.
     *
     * @param {object} [overrides] - `name` when the host is the one that asked for it
     * @returns {Promise<boolean>} - false if something was wrong, with the reason shown
     */
    async function submit(overrides) {
        overrides = overrides || {};
        var editing = editingId !== null;
        var existing = !editing && isExisting();
        var name = (overrides.name != null ? overrides.name : q('#loanName').value).trim();
        var principal = parseFloat(q('#loanPrincipal').value);
        var openDate = q('#loanOpenDate').value;
        var firstPayment = q('#loanFirstPayment').value;
        var destination = q('#loanDestination').value;

        if (!name) { showLoanError('The loan needs a name.'); return false; }
        if (!(principal > 0)) { showLoanError('Enter the amount borrowed.'); return false; }
        if (!openDate) { showLoanError('Enter the opening date.'); return false; }
        if (firstPayment && firstPayment <= openDate) {
            showLoanError('The first payment must come after the opening date.');
            return false;
        }
        if (existing && !targetAccountId()) { showLoanError('Choose the account.'); return false; }
        if (!editing && !existing && !destination) {
            showLoanError('Choose the account the money was paid into.');
            return false;
        }

        // Exactly the terms the quote was computed from, so what was on screen
        // is what gets saved.
        var body = readLoanForm();
        body.name = name;
        // Which account this is belongs to opening a loan, not to correcting one.
        if (!editing) {
            body.account_id = existing ? targetAccountId() : null;
            body.disbursement_account_id = existing ? null : parseInt(destination);
        }

        var btn = q('#loanSaveBtn');
        if (btn) btn.disabled = true;
        try {
            await loanRequest(editing ? '/loans/' + editingId : '/loans', editing ? 'PUT' : 'POST', body);
            global.closeLoanModal();
            resetLoanForm();
            // The drawdown moves a balance, so every page that caches one is stale.
            if (global.DelfinCache) { DelfinCache.clear(); DelfinCache.markDirty('loans'); }
            await cfg.onSaved();
            cfg.onSuccess(editing ? name + ' updated.' : name + ' added.');
            return true;
        } catch (e) {
            showLoanError(e.message);
            return false;
        } finally {
            if (btn) btn.disabled = false;
        }
    }

    global.saveLoan = function () { return submit(); };

    global.deleteLoanTerms = async function () {
        if (editingId === null) return false;
        var name = q('#loanName').value.trim();
        var ok = confirm(
            'Forget the terms of "' + name + '"?\n\n'
            + 'The account and its transactions are kept — only the agreed rate, '
            + 'term and fees are removed, so the loan goes back to being estimated '
            + 'from its movements.'
        );
        if (!ok) return false;

        var id = editingId;
        var btn = q('#loanDeleteBtn');
        if (btn) btn.disabled = true;
        try {
            await loanRequest('/loans/' + id, 'DELETE');
            global.closeLoanModal();
            resetLoanForm();
            if (global.DelfinCache) { DelfinCache.clear(); DelfinCache.markDirty('loans'); }
            await cfg.onSaved();
            cfg.onSuccess('Terms for ' + name + ' removed.');
            return true;
        } catch (e) {
            showLoanError(e.message);
            return false;
        } finally {
            if (btn) btn.disabled = false;
        }
    };

    /**
     * Pours a saved contract back into the form. Blank first payment stays blank,
     * because a derived one must not become a declared one just by being reopened.
     */
    function fillTerms(t) {
        q('#loanName').value = t.name || '';
        q('#loanPrincipal').value = t.principal;
        q('#loanRate').value = t.annual_rate || 0;
        q('#loanOpenDate').value = (t.open_date || '').slice(0, 10);
        q('#loanFirstPayment').value = (t.first_payment_date || '').slice(0, 10);
        q('#loanTermCount').value = t.term_count || 1;
        q('#loanTermUnit').value = t.term_unit || 'year';
        q('#loanRepaymentType').value = t.repayment_type || 'french';
        q('#loanInterestFrequency').value =
            t.interest_unit === 'day' ? 'day' : String(t.interest_months || 1);
        q('#loanPaymentMonths').value = String(t.payment_months || 1);
        q('#loanDayRule').value = t.day_rule || 'exact';
        q('#loanDayOrdinal').value = String(t.day_ordinal || 1);
        q('#loanDayOfMonth').value = String(t.day_of_month || 1);
        q('#loanFee').value = t.opening_fee || 0;
        q('#loanFeeTreatment').value = t.fee_treatment || 'upfront';
        q('#loanRecurringFee').value = t.recurring_fee || 0;
        q('#loanRecurringFeeMonths').value = String(t.recurring_fee_months || 1);
        q('#loanEarlyFee').value = t.early_repayment_fee_pct || 0;
        q('#loanLender').value = t.lender_name || '';
    }

    // ---- the module's own surface ----

    function configure(options) {
        Object.keys(options || {}).forEach(function (k) {
            if (options[k] !== undefined) cfg[k] = options[k];
        });
    }

    /**
     * Fills the pickers and puts the form in the state the caller asked for. The
     * lists are fetched here rather than handed in, so a host only has to say
     * which loan this is.
     *
     * @param {object} options - editingId, terms, accountId, currency
     */
    async function prepare(options) {
        editingId = options.editingId != null ? options.editingId : null;
        boundAccount = options.accountId != null ? options.accountId : null;
        boundCurrency = options.currency || null;
        var editing = editingId !== null;

        q('#loanError').style.display = 'none';
        q('#loanEditHint').style.display = editing ? 'block' : 'none';
        applyChrome();

        var dayPicker = q('#loanDayOfMonth');
        if (!dayPicker.options.length) {
            dayPicker.innerHTML = Array.from({ length: 31 }, function (_, i) {
                return '<option value="' + (i + 1) + '">' + (i + 1) + '</option>';
            }).join('');
        }
        if (!q('#loanOpenDate').value) {
            var today = new Date();
            q('#loanOpenDate').value = today.toISOString().slice(0, 10);
            dayPicker.value = String(today.getDate());
        }

        try {
            var loaded = await Promise.all([
                loanRequest('/accounts', 'GET'),
                loanRequest('/loans/account-ids', 'GET'),
                loanRequest('/payees', 'GET'),
                loanRequest('/loans/details?include_completed=true', 'GET'),
            ]);
            var all = loaded[0], loanIds = loaded[1], payees = loaded[2], details = loaded[3];

            // Money is paid into an ordinary account, never into another debt.
            var debtIds = new Set(loanIds.loan_account_ids || []);
            accounts = all.filter(function (a) { return !debtIds.has(a.id); });
            cfg.noteCurrencies(accounts.map(function (a) { return a.currency; }));
            q('#loanDestination').innerHTML = accounts.map(function (a) {
                return '<option value="' + a.id + '">' + esc(a.name) + ' (' + esc(a.currency) + ')</option>';
            }).join('');

            q('#loanLenderList').innerHTML = (payees || []).map(function (p) {
                return '<option value="' + esc(p.name) + '"></option>';
            }).join('');

            // Only debt accounts that have no terms yet can be given some.
            loanAccounts = (details.loans || []).concat(details.completed || [])
                .filter(function (d) { return !d.terms && d.account.type !== 'CREDIT_CARD'; })
                .map(function (d) {
                    return {
                        id: d.account.id, name: d.account.name, currency: d.account.currency,
                        open_date: d.open_date, borrowed: d.borrowed,
                    };
                });
        } catch (e) {
            showLoanError('Could not load accounts: ' + e.message);
        }

        var picker = q('#loanExistingAccount');
        picker.innerHTML = loanAccounts.length
            ? loanAccounts.map(function (a) {
                return '<option value="' + a.id + '">' + esc(a.name) + '</option>';
              }).join('')
            : '<option value="">No loan without terms</option>';

        var mode = q('#loanMode');
        mode.querySelector('option[value="existing"]').disabled = !loanAccounts.length;
        if (!loanAccounts.length) mode.value = 'new';

        // Handed an account, the form opens on it rather than asking again.
        if (!editing && boundAccount != null) {
            var wanted = String(boundAccount);
            if (loanAccounts.some(function (a) { return String(a.id) === wanted; })) {
                mode.value = 'existing';
                picker.value = wanted;
            }
        }

        if (editing && options.terms) fillTerms(options.terms);

        global.onLoanModeChange();
        global.onLoanDayRuleChange();
        global.onLoanFeeChange();
        global.onLoanInterestFrequencyChange();
    }

    /**
     * Shows the form in a dialog of its own, which is how the Loans page reaches
     * it. `editingId` plus `terms` corrects an existing contract.
     */
    async function open(options) {
        options = options || {};
        mount();
        attached = false;
        moveTo(q('#dlfLoanHome'));
        document.getElementById('dlfLoanModal').classList.add('dlf-open');
        q('#loanModalTitle').textContent = options.editingId != null ? 'Edit loan' : 'Add loan';
        q('#loanDeleteBtn').style.display = options.editingId != null ? 'inline-block' : 'none';
        await prepare(options);
    }

    /**
     * Lends the fields to a dialog the host already has open — Add account, under
     * Tools, once the account being opened turns out to be a loan.
     *
     * @param {HTMLElement} host - Where the fields go
     * @param {object} [options] - accountId, editingId, terms, currency
     */
    async function attach(host, options) {
        options = options || {};
        mount();
        attached = true;
        moveTo(host);
        await prepare(options);
    }

    /** Takes the fields back, leaving the host's dialog as it was. */
    function detach() {
        if (!attached) return;
        attached = false;
        moveTo(q('#dlfLoanHome'));
        resetLoanForm();
        boundAccount = null;
    }

    global.DelfinLoanForm = {
        configure: configure,
        open: open,
        attach: attach,
        detach: detach,
        submit: submit,
        deleteTerms: function () { return global.deleteLoanTerms(); },
        close: global.closeLoanModal,
        reset: function () { mount(); resetLoanForm(); },
    };
})(window);
