/**
 * Opening an account, from wherever you happen to be.
 *
 * It used to live in Tools, with a smaller copy of it behind the Account picker
 * of the transaction dialog. Now that the quick-add button offers it on every
 * page, one of them had to be the only one — a second copy is a second set of
 * rules about what an account needs, and they drift.
 *
 * A liability can be opened on agreed terms, in which case the loan form takes
 * over the arithmetic and the account comes with the contract. That form is
 * `loan-form.js`, which this one borrows and hands back.
 *
 *     DelfinAccountForm.configure({ apiBase, onSaved, onError });
 *     DelfinAccountForm.open();
 */
(function (global) {
    'use strict';

    var cfg = {
        apiBase: '',
        onSaved: function () {},
        onSuccess: function (message) {},
        onError: function (message) { alert(message); },
    };

    // A limit is something a lender grants, so it is offered on the kinds of
    // account that borrow. What can carry a repayment schedule is narrower: a
    // card is revolving credit, with nothing agreed to amortise.
    var BORROWING_TYPES = ['CREDIT_CARD', 'LIABILITY'];
    var SCHEDULABLE_TYPES = ['LIABILITY', 'LOAN'];

    var currencies = null;

    function q(sel) { return document.querySelector(sel); }
    function el(id) { return document.getElementById(id); }

    var STYLE = `
      /**
       * The dialogs carry the app's colours with them.
       *
       * They open on five pages, and the pages do not agree on the vocabulary:
       * the dashboard calls --ink a blue (#3C5A6E, what the others call
       * --ink-blue) and never defines --accent, --field or --panel. A
       * dialog written against those names therefore came out with blue text,
       * a blue rule where the red one belongs, and colourless buttons. Naming
       * them again here, on the dialog itself, settles what they mean inside it
       * whatever the page around it believes.
       */
      #dlfAccountModal {
        --bg:#FAF3E9; --paper:#FFFDFA; --panel:#F3E9DA; --card:#E4D5C1;
        --border:#D8C6B0; --hair:#EFE4D3; --field:#E4D5C1; --chip:#F1E7D6;
        --muted:#8A7D6C; --text:#23201C; --ink:#23201C; --accent:#B0402E;
        --red:#B0402E; --green:#3C7A57; --yellow:#C6893F; --ink-blue:#3C5A6E;
        --btn-primary:#2B2621; --danger:#B0402E;
      }
      #dlfAccountModal { display:none; position:fixed; inset:0; z-index:1100;
        background:rgba(35,32,28,.4); backdrop-filter:blur(3px);
        align-items:center; justify-content:center; padding:18px; }
      #dlfAccountModal.dlf-open { display:flex; }
      #dlfAccountModal .modal-content { background:var(--paper); border-radius:16px; width:100%;
        max-width:460px; max-height:90vh; overflow-y:auto; border:1px solid var(--hair);
        box-shadow:0 24px 60px rgba(40,28,14,.35); font-family:'IBM Plex Sans',sans-serif;
        position:static; transform:none; padding:0; }
      #dlfAccountModal .modal-header { display:flex; justify-content:space-between; align-items:center;
        gap:12px; padding:18px 22px; border-bottom:1px solid var(--hair); }
      #dlfAccountModal .modal-header h3 { margin:0; font-family:'Playfair Display',serif; font-size:21px;
        font-weight:700; padding-left:12px; border-left:3px solid var(--accent); color:var(--ink); }
      #dlfAccountModal .dlf-x { background:var(--field); border:none; border-radius:8px; width:32px;
        height:32px; font-size:18px; line-height:1; cursor:pointer; color:#3A342C; flex:0 0 auto; }
      #dlfAccountModal .dlf-foot { display:flex; gap:10px; padding:16px 22px; }
      #dlfAccountModal .dlf-foot button { flex:1; height:46px; border-radius:10px; cursor:pointer;
        font-family:'Playfair Display',serif; font-size:14px; font-weight:500;
        border:1px solid var(--field); background:#fff; color:#3A342C; }
      /* Beaten by the rule above it on specificity when it named only the class. */
      #dlfAccountModal .dlf-foot button.dlf-primary { background:#2B2621; color:#F6EFE3;
        border-color:#2B2621; }
      #dlfAccountModal .dlf-error { color:var(--red); font-size:12.5px; padding:10px 22px 0; display:none; }`;

    var MARKUP = `
      <div id="dlfAccountModal" class="modal">
        <div class="modal-content">
          <div class="modal-header">
            <h3>Add Account</h3>
            <button class="dlf-x" onclick="DelfinAccountForm.close()">&times;</button>
          </div>
          <div class="dlf-error" id="dlfAccError"></div>
          <div class="form-group"><label>Name</label>
            <input type="text" id="dlfAccName" placeholder="Current account, Visa, Cash…"></div>
          <div class="form-group" id="dlfAccCurrencyGroup"><label>Currency</label>
            <select id="dlfAccCurrency"></select></div>
          <div class="form-group"><label>Type</label><select id="dlfAccType"></select></div>
          <div class="form-group" id="dlfAccOpeningGroup">
            <label>Opening balance</label>
            <input type="number" id="dlfAccOpening" step="0.01" inputmode="decimal" placeholder="What is in it now">
            <div class="form-hint">What the account holds before anything Delfin knows about. Leave it empty for zero.</div>
          </div>
          <div class="form-group" id="dlfAccLimitGroup" style="display: none;">
            <label>Credit limit</label>
            <input type="number" id="dlfAccLimit" step="0.01" min="0" inputmode="decimal" placeholder="Leave empty if there is none">
          </div>
          <div class="form-group" id="dlfAccTermsGroup" style="display: none;">
            <label>Repayment</label>
            <select id="dlfAccTerms" onchange="DelfinAccountForm.onTermsChange()">
              <option value="none">no schedule — just what is owed</option>
              <option value="terms">on agreed terms</option>
            </select>
            <div class="form-hint" id="dlfAccTermsHint">Choose the second and the instalments,
              interest and full schedule are worked out here.</div>
          </div>
          <!-- Where the shared loan form sits while this account is a loan. -->
          <div id="dlfAccTermsHost"></div>
          <div class="dlf-foot">
            <button class="dlf-primary" onclick="DelfinAccountForm.create()">Create</button>
            <button onclick="DelfinAccountForm.close()">Cancel</button>
          </div>
        </div>
      </div>`;

    function mount() {
        if (el('dlfAccountModal')) return;
        var style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);
        document.body.insertAdjacentHTML('beforeend', MARKUP);
        el('dlfAccountModal').addEventListener('click', function (e) {
            if (e.target.id === 'dlfAccountModal') close();
        });
    }

    async function loadCurrencies() {
        if (currencies) return currencies;
        try {
            var res = await fetch(cfg.apiBase + '/api/currencies');
            currencies = (await res.json()).currencies || [];
        } catch (e) {
            currencies = [{ code: 'GBP', name: 'Pound Sterling' },
                          { code: 'EUR', name: 'Euro' },
                          { code: 'USD', name: 'US Dollar' }];
        }
        return currencies;
    }

    function showError(message) {
        var box = el('dlfAccError');
        box.textContent = message;
        box.style.display = message ? 'block' : 'none';
    }

    /**
     * A loan is an account with a contract attached, so the terms are asked for
     * here rather than on a second page. Choosing them hides what the contract
     * supersedes: the amount borrowed is the opening balance, and the currency
     * follows the account the money was paid into.
     */
    async function onTermsChange() {
        var wanted = el('dlfAccTerms').value === 'terms';
        el('dlfAccCurrencyGroup').style.display = wanted ? 'none' : '';
        el('dlfAccOpeningGroup').style.display = wanted ? 'none' : '';
        el('dlfAccTermsHint').style.display = wanted ? 'none' : 'block';
        var kind = DelfinAccountTypes.canonical(el('dlfAccType').value);
        el('dlfAccLimitGroup').style.display =
            (!wanted && BORROWING_TYPES.indexOf(kind) !== -1) ? '' : 'none';
        if (wanted) {
            await DelfinLoanForm.attach(el('dlfAccTermsHost'));
        } else {
            DelfinLoanForm.detach();
        }
    }

    async function open() {
        mount();
        showError('');
        el('dlfAccName').value = '';
        el('dlfAccOpening').value = '';
        el('dlfAccLimit').value = '';
        el('dlfAccType').innerHTML =
            '<option value="">Select...</option>' + DelfinAccountTypes.options();
        el('dlfAccLimitGroup').style.display = 'none';
        el('dlfAccTermsGroup').style.display = 'none';
        el('dlfAccTerms').value = 'none';
        DelfinLoanForm.detach();
        el('dlfAccCurrencyGroup').style.display = '';
        el('dlfAccOpeningGroup').style.display = '';
        el('dlfAccType').onchange = function (e) {
            var kind = DelfinAccountTypes.canonical(e.target.value);
            el('dlfAccLimitGroup').style.display =
                BORROWING_TYPES.indexOf(kind) !== -1 ? '' : 'none';
            var lending = SCHEDULABLE_TYPES.indexOf(kind) !== -1;
            el('dlfAccTermsGroup').style.display = lending ? '' : 'none';
            if (!lending) {
                el('dlfAccTerms').value = 'none';
                onTermsChange();
            }
        };

        var list = await loadCurrencies();
        var sel = el('dlfAccCurrency');
        sel.innerHTML = list.map(function (c) {
            return '<option value="' + c.code + '">' + c.code + ' — ' + c.name + '</option>';
        }).join('');

        el('dlfAccountModal').classList.add('dlf-open');
    }

    function close() {
        // The loan form is on loan: it goes back before this dialog disappears.
        if (global.DelfinLoanForm) DelfinLoanForm.detach();
        var m = el('dlfAccountModal');
        if (m) m.classList.remove('dlf-open');
    }

    async function create() {
        var name = el('dlfAccName').value.trim();
        var type = el('dlfAccType').value;
        if (!name) return showError('The account needs a name');
        if (!type) return showError('Choose what kind of account this is');
        showError('');

        // With terms it is the loan that is created, and the account comes with
        // it — along with the drawdown into whichever account received the money.
        if (el('dlfAccTerms').value === 'terms') {
            var saved = await DelfinLoanForm.submit({ name: name });
            if (saved) close();
            return;
        }

        var opening = el('dlfAccOpening').value.trim();
        var limit = el('dlfAccLimit').value.trim();
        var limitApplies = BORROWING_TYPES.indexOf(DelfinAccountTypes.canonical(type)) !== -1;
        try {
            var res = await fetch(cfg.apiBase + '/accounts', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    name: name,
                    type: type,
                    currency: el('dlfAccCurrency').value,
                    initial_balance: opening ? parseFloat(opening) : 0,
                    credit_limit: (limitApplies && limit) ? parseFloat(limit) : null,
                }),
            });
            if (!res.ok) throw new Error((await res.json()).detail || 'Create failed');
            // An account changes what every page shows, not just this one.
            if (global.DelfinCache) DelfinCache.markDirty(null);
            close();
            cfg.onSuccess('Added "' + name + '"');
            await cfg.onSaved();
        } catch (e) {
            showError('Error creating the account: ' + e.message);
        }
    }

    global.DelfinAccountForm = {
        configure: function (options) {
            Object.keys(options || {}).forEach(function (k) {
                if (options[k] !== undefined) cfg[k] = options[k];
            });
        },
        open: open,
        close: close,
        create: create,
        onTermsChange: onTermsChange,
    };
})(window);
