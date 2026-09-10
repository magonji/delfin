/**
 * Writing a transaction, a transfer, or a split — from any page.
 *
 * All of this lived in transactions.html, which was the only place it could be
 * reached from: every other page's quick-add button was a link that navigated
 * there first. Now the button opens the form where you are, so the form had to
 * stop belonging to one page.
 *
 * What moved is the whole of it, unchanged: the two dialogs and the five small
 * ones behind their "Add new…" options, the split editor, the sign button, the
 * deferred balance repair that Save & New depends on, and editing an entry that
 * already exists. Every function keeps its name on `window`, because the markup
 * calls them by name from its own onclick attributes.
 *
 * What the module cannot know is what the page around it should do once
 * something is saved — reload a list, redraw a total, scroll to the new row.
 * That is the host's, and it is passed in:
 *
 *     DelfinTxForm.configure({ apiBase, reloadList, redrawList, ... });
 *     DelfinTxForm.openTransaction();
 *
 * A page that keeps its own copies of the catalogues (Transactions does, for
 * its filters and its list) hands them over with `catalogues`; any other page
 * lets the module fetch its own.
 */
(function (global) {
    'use strict';

    var cfg = {
        apiBase: '',
        // What the page shows, and what it should do when the money changes.
        reloadList: function () { return Promise.resolve(); },
        redrawList: function () {},
        setScrollTarget: function () {},
        formatMoney: function (amount, currency) {
            try {
                return new Intl.NumberFormat(undefined, {
                    style: 'currency', currency: currency, currencyDisplay: 'narrowSymbol',
                }).format(amount);
            } catch (e) {
                return (currency ? currency + ' ' : '') + Number(amount).toFixed(2);
            }
        },
        // Rows of a list this page may not have.
        deleteTransaction: null, deleteSplit: null, deleteTransfer: null,
        duplicateTransaction: null, duplicateSplit: null, duplicateTransfer: null,
        // The page's own catalogues, when it keeps any.
        catalogues: null,
        // Whether a filter is narrowing the page to one account, so a new entry
        // can start there.
        filters: null,
        onSaved: function () {},
    };

    var API_URL = '';

    // The catalogues the form offers. Either the host's, kept in step through
    // `catalogues`, or the module's own on a page that has none.
    var allCategories = [], allPayees = [], allAccounts = [], allAccountsIncludingClosed = [],
        allLocations = [], allProjects = [], accountsMap = new Map(), displayCurrency = null;

    // ---- the form's own state --------------------------------------------
    let splitMode = false;
    let splitLines = [];           // [{ id, amount, parent, category_id, project_id, note }]
    let editingSplitGroup = null;  // set while editing an existing split
    let transactionBatchCount = 0;
    let transferBatchCount = 0;
    let batchAffectedAccounts = new Set();  // Track accounts that need balance recalculation
    let balanceRepairPending = false;
    let batchEarliestDate = null;  // Track earliest date in batch for incremental recalculation
    let amountIsNegative = false;
    let saveInFlight = false;

    // ---- small things it needs wherever it runs --------------------------

    function escapeHtml(value) {
        return String(value == null ? '' : value).replace(/[&<>"']/g,
            function (c) { return ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]; });
    }

    function parseMoneyAmount(value) {
        return Math.round(parseFloat(value) * 100) / 100;
    }

    function formatMoneyWithSymbol(amount, currency) {
        return cfg.formatMoney(amount, currency);
    }

    function openModal(id) {
        var modal = document.getElementById(id);
        // A direct child of <body>, so that a dialog opened over another is its
        // sibling rather than inside the part being made inert.
        if (modal.parentElement !== document.body) document.body.appendChild(modal);
        modal.classList.add('active');
        trapFocus(modal);
    }

    function closeModal(id) {
        var modal = document.getElementById(id);
        modal.classList.remove('active');
        var form = modal.querySelector('form');
        if (form) form.reset();
        releaseFocus(modal);
    }

    function trapFocus(modal) {
        var parent = modal.parentElement;
        if (!parent) return;
        Array.prototype.forEach.call(parent.children, function (child) {
            if (child !== modal && !child.classList.contains('modal')) {
                child.setAttribute('inert', '');
                child.dataset.inertByModal = modal.id;
            }
        });
    }

    function releaseFocus(modal) {
        if (document.querySelector('.modal.active')) return;
        document.querySelectorAll('[data-inert-by-modal]').forEach(function (el) {
            el.removeAttribute('inert');
            delete el.dataset.inertByModal;
        });
    }

    // ---- what the page does, or a sensible nothing -----------------------

    function loadRecentTransactions(force) { return cfg.reloadList(force); }
    function displayCombinedTransactions() { return cfg.redrawList(); }
    /**
     * The Account picker's way out to opening one. It used to be a smaller
     * dialog of its own here, asking for less than the real one does; now it is
     * the real one, and what it opens with comes back to whichever picker asked.
     */
    function openNewAccountModal(field) {
        if (!global.DelfinAccountForm) return;
        DelfinAccountForm.open({
            onCreated: async function (account) {
                await loadAccounts();
                var sel = document.getElementById(field || 'account');
                if (sel && account && account.id) sel.value = String(account.id);
                if (field === 'fromAccount' || field === 'toAccount') checkTransferCurrencies();
            },
        });
    }

    /** A row put straight into the list, before the server has been asked again. */
    function insertOptimistic(kind, row) { if (cfg.onOptimistic) cfg.onOptimistic(kind, row); }
    function setScrollTarget() { return cfg.setScrollTarget.apply(null, arguments); }
    function deleteTransaction(id) { return cfg.deleteTransaction && cfg.deleteTransaction(id); }
    function deleteSplit(g) { return cfg.deleteSplit && cfg.deleteSplit(g); }
    function deleteTransfer(a, b) { return cfg.deleteTransfer && cfg.deleteTransfer(a, b); }
    function duplicateTransaction(id) { return cfg.duplicateTransaction && cfg.duplicateTransaction(id); }
    function duplicateSplit(g) { return cfg.duplicateSplit && cfg.duplicateSplit(g); }
    function duplicateTransfer(a, b) { return cfg.duplicateTransfer && cfg.duplicateTransfer(a, b); }

    async function get(path) {
        var res = await fetch(API_URL + path);
        return res.json();
    }

    // A catalogue reload goes through the host when it keeps its own copy, so
    // its filters and lists see the new payee too; otherwise the module fetches.
    async function reloadCatalogue(name, path, apply) {
        if (cfg['load' + name]) await cfg['load' + name]();
        else apply(await get(path));
        syncCatalogues();
        populateFormSelects();
    }

    async function loadCategories() {
        await reloadCatalogue('Categories', '/categories', function (d) { allCategories = d; });
    }
    async function loadPayees() {
        await reloadCatalogue('Payees', '/payees', function (d) { allPayees = d; });
    }
    async function loadLocations() {
        await reloadCatalogue('Locations', '/locations', function (d) { allLocations = d; });
    }
    async function loadProjects() {
        await reloadCatalogue('Projects', '/projects', function (d) { allProjects = d; });
    }
    async function loadAccounts() {
        await reloadCatalogue('Accounts', '/accounts', function (d) {
            allAccounts = d.filter(function (a) { return a.is_active !== false; });
            allAccountsIncludingClosed = d;
            accountsMap = new Map(d.map(function (a) { return [a.id, a]; }));
        });
    }

    /** Takes the host's copies, when it keeps any, as the ones to offer. */
    function syncCatalogues() {
        if (!cfg.catalogues) return;
        var c = cfg.catalogues() || {};
        if (c.categories) allCategories = c.categories;
        if (c.payees) allPayees = c.payees;
        if (c.accounts) allAccounts = c.accounts;
        if (c.accountsIncludingClosed) allAccountsIncludingClosed = c.accountsIncludingClosed;
        if (c.locations) allLocations = c.locations;
        if (c.projects) allProjects = c.projects;
        if (c.accountsMap) accountsMap = c.accountsMap;
        if (c.displayCurrency !== undefined) displayCurrency = c.displayCurrency;
    }

    var loaded = false;
    async function ensureCatalogues() {
        syncCatalogues();
        if (loaded || (cfg.catalogues && allAccounts.length)) { populateFormSelects(); return; }
        var got = await Promise.all([get('/categories'), get('/payees'), get('/accounts'),
                                     get('/locations'), get('/projects')]);
        if (!allCategories.length) allCategories = got[0];
        if (!allPayees.length) allPayees = got[1];
        if (!allAccounts.length) {
            allAccountsIncludingClosed = got[2];
            allAccounts = got[2].filter(function (a) { return a.is_active !== false; });
            accountsMap = new Map(got[2].map(function (a) { return [a.id, a]; }));
        }
        if (!allLocations.length) allLocations = got[3];
        if (!allProjects.length) allProjects = got[4];
        loaded = true;
        populateFormSelects();
    }

    /**
     * Fills the form's own pickers. The page that hosts a filter bar fills that
     * from its own copy: what belongs to the form is filled here, so a page
     * without filters needs nothing but this.
     */
    function populateFormSelects() {
        var accountOpt = function (a) {
            return '<option value="' + a.id + '" data-currency="' + a.currency + '">' +
                   escapeHtml(a.name) + ' (' + a.currency + ')</option>';
        };
        var addNew = '<option value="__NEW__" style="color:#667eea;font-weight:bold">\u2795 Add new</option>';
        ['account', 'fromAccount', 'toAccount'].forEach(function (id) {
            var el = document.getElementById(id);
            if (el) el.innerHTML = '<option value="">Select...</option>' +
                allAccounts.map(accountOpt).join('') + addNew;
        });

        var parents = Array.from(new Set(allCategories.map(function (c) { return c.parent || c.name; }))).sort();
        var parentSel = document.getElementById('parentCategory');
        if (parentSel) parentSel.innerHTML = '<option value="">Select...</option>' +
            parents.map(function (p) { return '<option value="' + escapeHtml(p) + '">' + escapeHtml(p) + '</option>'; }).join('') +
            '<option value="__NEW__" style="color:#667eea">\u2795 Add new</option>';

        var loc = document.getElementById('location');
        if (loc) loc.innerHTML = '<option value="">Select...</option>' +
            allLocations.map(function (l) { return '<option value="' + l.id + '">' + escapeHtml(l.name) + '</option>'; }).join('') +
            '<option value="__NEW__" style="color:#667eea">\u2795 Add new</option>';

        var proj = document.getElementById('project');
        if (proj) proj.innerHTML = '<option value="">Select...</option>' +
            allProjects.map(function (p) { return '<option value="' + p.id + '">' + escapeHtml(p.name) + '</option>'; }).join('') +
            '<option value="__NEW__" style="color:#667eea">\u2795 Add new</option>';

        var list = document.getElementById('payeeList');
        if (list) list.innerHTML = allPayees.map(function (p) {
            return '<option value="' + escapeHtml(p.name) + '">';
        }).join('');
    }

    // ---- the form, exactly as it was -------------------------------------

        function addSplitLine(amount = null) {
            syncSplitLinesFromDom();
            splitLines.push({ id: null, amount, parent: null, category_id: null, project_id: null, note: '' });
            renderSplitLines();
            document.getElementById(`splitAmount_${splitLines.length - 1}`).focus();
        }

        function applyCategoryPair(categoryId) {
            const category = allCategories.find(c => c.id === categoryId);
            if (!category) return false;
            const parentSelect = document.getElementById('parentCategory');
            const parentName = category.parent || category.name;
            const option = Array.from(parentSelect.options).find(o => o.value === parentName);
            if (!option) return false;
            parentSelect.value = parentName;
            // loadSubcategories only reads what is already in memory, so the
            // subcategory can be set on the next line rather than after a timer.
            loadSubcategories(parentName);
            document.getElementById('category').value = category.parent ? String(categoryId) : '';
            return true;
        }

        function assignSplitRemainder() {
            syncSplitLinesFromDom();
            const total = parseMoneyAmount(document.getElementById('amount').value);
            if (!Number.isFinite(total)) return;
            const left = Math.round((total - splitAssignedTotal()) * 100) / 100;
            if (Math.abs(left) < 0.005) return;
            // An empty line is waiting for exactly this; otherwise start a new one.
            const empty = splitLines.findIndex(l => !Number.isFinite(l.amount));
            if (empty >= 0) {
                splitLines[empty].amount = left;
                renderSplitLines();
            } else {
                addSplitLine(left);
            }
        }

        function checkTransferCurrencies() {
            const f = document.getElementById('fromAccount').selectedOptions[0], t = document.getElementById('toAccount').selectedOptions[0];
            if(f && t && f.dataset.currency !== t.dataset.currency) document.getElementById('toAmountGroup').classList.remove('hidden');
            else document.getElementById('toAmountGroup').classList.add('hidden');
        }

        function clearPayeeCategoryHints() {
            const box = document.getElementById('payeeCategoryHints');
            box.innerHTML = '';
            box.hidden = true;
        }

        function closeTransactionModal() { 
            let balancesReady = Promise.resolve();
            if (transactionBatchCount > 0) {
                balancesReady = recalculateAffectedBalances()
                    .then(() => loadRecentTransactions(true));
            }
            resetTransactionBatchState();
            closeModal('transactionModal');
            exitSplitMode();
            delete document.getElementById('transactionForm').dataset.editingId;
            document.getElementById('transactionForm').querySelector('button[type="submit"]').textContent='Save';
            return balancesReady;
        }

        function closeTransferModal() { 
            let balancesReady = Promise.resolve();
            if (transferBatchCount > 0) {
                balancesReady = recalculateAffectedBalances()
                    .then(() => loadRecentTransactions(true));
            }
            resetTransferBatchState();
            closeModal('transferModal'); 
            delete document.getElementById('transferForm').dataset.editingOutId; 
            document.getElementById('transferForm').querySelector('button[type="submit"]').textContent='Save'; 
            return balancesReady;
        }

        function colorAmountInput(input) {
            const value = parseFloat(input.value);
            input.classList.remove('amount-positive', 'amount-negative');
            if (!isNaN(value)) {
                if (value < 0) {
                    input.classList.add('amount-negative');
                } else if (value > 0) {
                    input.classList.add('amount-positive');
                }
            }
        }

        function deleteFromModal() {
            if (editingSplitGroup) {
                const groupId = editingSplitGroup;
                closeTransactionModal();
                deleteSplit(groupId);
                return;
            }
            const id = document.getElementById('transactionForm').dataset.editingId;
            if (!id) return;
            closeTransactionModal();
            deleteTransaction(+id);
        }

        function deleteFromTransferModal() {
            const f = document.getElementById('transferForm');
            const out = f.dataset.editingOutId, inn = f.dataset.editingInId;
            if (!out || !inn) return;
            closeTransferModal();
            deleteTransfer(+out, +inn);
        }

        function duplicateFromModal() {
            if (editingSplitGroup) {
                const groupId = editingSplitGroup;
                closeTransactionModal();
                duplicateSplit(groupId);
                return;
            }
            const id = document.getElementById('transactionForm').dataset.editingId;
            if (!id) return;
            closeTransactionModal();
            duplicateTransaction(+id);
        }

        function duplicateFromTransferModal() {
            const f = document.getElementById('transferForm');
            const out = f.dataset.editingOutId, inn = f.dataset.editingInId;
            if (!out || !inn) return;
            closeTransferModal();
            duplicateTransfer(+out, +inn);
        }

        async function editSplit(groupId) {
            setScrollTarget(groupId);
            const g = await (await fetch(`${API_URL}/transactions/split/${groupId}`)).json();
            openTransactionModal();

            const d = new Date(g.date);
            document.getElementById('date').valueAsDate = d;
            document.getElementById('time').value = d.toTimeString().slice(0, 5);
            document.getElementById('amount').value = g.amount;
            document.getElementById('account').value = g.account_id;
            syncAmountSign();
            document.getElementById('payee').value = g.payee_name || '';
            document.getElementById('location').value = g.location_id || '';

            editingSplitGroup = groupId;
            enterSplitMode(g.lines.map(l => {
                const cat = allCategories.find(c => c.id === l.category_id);
                return {
                    id: l.id,
                    amount: l.amount,
                    parent: cat ? (cat.parent || null) : null,
                    category_id: cat && cat.parent ? l.category_id : null,
                    project_id: l.project_id,
                    note: l.note || '',
                };
            }));

            const f = document.getElementById('transactionForm');
            f.querySelector('button[type="submit"]').textContent = 'Update';
            setModalMode('transactionModal', true);
            document.querySelectorAll('#transactionModal [data-new-only]').forEach(b => b.style.display = 'none');
        }

        async function editTransaction(id) {
            setScrollTarget(id);
            const t = await (await fetch(`${API_URL}/transactions/${id}`)).json();
            openTransactionModal();
            const d = new Date(t.date); document.getElementById('date').valueAsDate = d; document.getElementById('time').value = d.toTimeString().slice(0,5);
            document.getElementById('amount').value = t.amount; document.getElementById('account').value = t.account_id; document.getElementById('note').value = t.note||'';
            syncAmountSign();
            if(t.category_id) { 
                const c = allCategories.find(x=>x.id===t.category_id); 
                if(c?.parent) { document.getElementById('parentCategory').value = c.parent; loadSubcategories(c.parent); setTimeout(()=>document.getElementById('category').value=t.category_id, 100); }
            }
            if(t.payee_id) {
                const payee = allPayees.find(p => p.id === t.payee_id);
                if(payee) document.getElementById('payee').value = payee.name;
            }
            if(t.location_id) document.getElementById('location').value = t.location_id;
            if(t.project_id) document.getElementById('project').value = t.project_id;
            const f = document.getElementById('transactionForm'); f.dataset.editingId = id; f.querySelector('button[type="submit"]').textContent = 'Update';
            setModalMode('transactionModal', true);
        }

        async function editTransfer(outId, inId) {
            setScrollTarget(null, `${outId}_${inId}`);
            const o = await (await fetch(`${API_URL}/transactions/${outId}`)).json();
            const i = await (await fetch(`${API_URL}/transactions/${inId}`)).json();
            openTransferModal();
            const d = new Date(o.date); document.getElementById('transferDate').valueAsDate = d; document.getElementById('transferTime').value = d.toTimeString().slice(0,5);
            document.getElementById('fromAccount').value = o.account_id; document.getElementById('toAccount').value = i.account_id;
            document.getElementById('fromAmount').value = Math.abs(o.amount); 
            if(o.currency !== i.currency) document.getElementById('toAmount').value = i.amount;
            document.getElementById('transferNote').value = o.note || ''; checkTransferCurrencies();
            const f = document.getElementById('transferForm'); f.dataset.editingOutId = outId; f.dataset.editingInId = inId; f.querySelector('button[type="submit"]').textContent = 'Update';
            setModalMode('transferModal', true);
        }

        function enterSplitMode(lines) {
            splitMode = true;
            splitLines = lines && lines.length ? lines : [
                { id: null, amount: parseMoneyAmount(document.getElementById('amount').value) || null,
                  parent: null, category_id: null, project_id: null, note: '' },
                { id: null, amount: null, parent: null, category_id: null, project_id: null, note: '' },
            ];
            document.getElementById('singleCategoryRow').style.display = 'none';
            clearPayeeCategoryHints();
            document.getElementById('singleTailFields').style.display = 'none';
            document.getElementById('splitEditor').style.display = '';
            document.getElementById('splitToggleBtn').textContent = 'Back to a single line';
            // While editing an existing split there is no "back": removing
            // lines until one is left is what turns it into a plain
            // transaction again, and the server does that on save.
            document.getElementById('splitToggleRow').style.display = editingSplitGroup ? 'none' : '';
            // "Save & New" keeps a form open for the next entry; a split is a
            // deliberate, one-off piece of data entry, so it does not apply.
            document.querySelectorAll('#transactionModal [data-new-only]').forEach(b => b.style.display = 'none');
            renderSplitLines();
        }

        function exitSplitMode() {
            splitMode = false;
            editingSplitGroup = null;
            splitLines = [];
            document.getElementById('splitLines').innerHTML = '';
            document.getElementById('singleCategoryRow').style.display = '';
            document.getElementById('singleTailFields').style.display = '';
            document.getElementById('splitEditor').style.display = 'none';
            document.getElementById('splitToggleRow').style.display = '';
            document.getElementById('splitToggleBtn').textContent = 'Split into several lines';
        }

        async function guardedSave(save) {
            if (saveInFlight) return false;
            saveInFlight = true;
            try {
                return await save();
            } catch (error) {
                console.error('Save failed:', error);
                alert('Could not reach the server, so nothing was saved. '
                    + 'Check your connection and try again — what you typed is still here.');
                return false;
            } finally {
                saveInFlight = false;
            }
        }

        async function handlePayeeBlur() {
            const payeeName = document.getElementById('payee').value.trim();
            if (!payeeName) { clearPayeeCategoryHints(); return; }
            
            if (document.getElementById('transactionForm').dataset.editingId) return;
            
            const payee = allPayees.find(p => 
                p.name.toLowerCase() === payeeName.toLowerCase()
            );
            
            if (!payee) { clearPayeeCategoryHints(); return; }
            
            try {
                // The most-used pair still fills itself in, and still only into an
                // empty field: a category you chose yourself is not a guess to be
                // overruled.
                let applied = null;
                if (payee.most_common_category_id && !document.getElementById('parentCategory').value) {
                    if (applyCategoryPair(payee.most_common_category_id)) {
                        applied = payee.most_common_category_id;
                    }
                }
                showPayeeCategoryHints(payee, applied);
                
                if (payee.most_common_location_id) {
                    const locationSelect = document.getElementById('location');
                    if (!locationSelect.value) {
                        locationSelect.value = payee.most_common_location_id;
                    }
                }
                
                if (payee.most_common_project_id) {
                    const projectSelect = document.getElementById('project');
                    if (!projectSelect.value) {
                        projectSelect.value = payee.most_common_project_id;
                    }
                }
                
            } catch (error) {
                console.error('Error auto-filling from payee:', error);
            }
        }

        async function handleTransactionSubmit(e) {
            e.preventDefault();
            await guardedSave(() => saveTransaction(false));  // false = no es "save and new", debe cerrar y recalcular
        }

        async function handleTransferSubmit(e) {
            e.preventDefault();
            await guardedSave(() => saveTransfer(false));  // false = no es "save and new"
        }

        function loadSubcategories(parent) {
            const sub = allCategories.filter(c => c.parent === parent).sort((a,b)=>a.name.localeCompare(b.name));
            const el = document.getElementById('category');
            el.disabled = false;
            el.innerHTML = '<option value="">Select...</option>' + sub.map(c => `<option value="${c.id}">${c.name}</option>`).join('') + '<option value="__NEW__" style="color:#667eea">➕ Add new</option>';
        }

        function onSplitLineInput() {
            syncSplitLinesFromDom();
            updateSplitTally();
        }

        function onSplitParentChange(index) {
            syncSplitLinesFromDom();
            splitLines[index].category_id = null;   // the old subcategory belongs to the old parent
            renderSplitLines();
        }

        function openTransactionModal() {
            openModal('transactionModal');
            setModalMode('transactionModal', false);
            exitSplitMode();
            clearPayeeCategoryHints();
            const now = new Date();
            const dateStr = now.toISOString().split('T')[0];
            const timeStr = now.toTimeString().split(' ')[0].substring(0,5);
            document.getElementById('date').value = dateStr;
            document.getElementById('time').value = timeStr;
            
            // Reset amount colour and sign
            syncAmountSign();
            
            // If filtering by account, preselect that account
            var narrowed = cfg.filters && cfg.filters();
            if (narrowed && narrowed.active && narrowed.accountId) {
                document.getElementById('account').value = narrowed.accountId;
            }
        }

        function openTransferModal() {
            openModal('transferModal');
            setModalMode('transferModal', false);
            const now = new Date();
            const dateStr = now.toISOString().split('T')[0];
            const timeStr = now.toTimeString().split(' ')[0].substring(0,5);
            document.getElementById('transferDate').value = dateStr;
            document.getElementById('transferTime').value = timeStr;
        }

        async function recalculateAffectedBalances(attempt = 0) {
            if (batchAffectedAccounts.size === 0) return;

            const accountIds = Array.from(batchAffectedAccounts);
            const since = batchEarliestDate;
            batchAffectedAccounts.clear();
            batchEarliestDate = null;

            const body = { account_ids: accountIds };
            if (since) body.since = since;

            try {
                const res = await fetch(`${API_URL}/admin/recalculate-balances-for-accounts`, {
                    method: 'POST',
                    body: JSON.stringify(body),
                    headers: { 'Content-Type': 'application/json' }
                });
                // A 500 here is as damaging as no connection at all, and this
                // never used to look at the answer.
                if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
                balanceRepairPending = false;
                showConnectionNotice('');
            } catch (error) {
                console.error('Error recalculating balances:', error);
                accountIds.forEach(id => batchAffectedAccounts.add(id));
                if (since && (!batchEarliestDate || since < batchEarliestDate)) batchEarliestDate = since;
                balanceRepairPending = true;

                if (attempt < 2) {
                    await new Promise(r => setTimeout(r, 1200 * (attempt + 1)));
                    return recalculateAffectedBalances(attempt + 1);
                }
                showConnectionNotice('Your entries were saved, but their balances could not be brought '
                    + 'up to date, so some figures below will be blank or stale. They will be put right '
                    + 'as soon as the connection is back.');
            }
        }

        function removeSplitLine(index) {
            syncSplitLinesFromDom();
            splitLines.splice(index, 1);
            if (!splitLines.length) splitLines.push({ id: null, amount: null, parent: null, category_id: null, project_id: null, note: '' });
            renderSplitLines();
        }

        function renderSplitLines() {
            document.getElementById('splitLines').innerHTML =
                splitLines.map((line, i) => splitLineHtml(i, line)).join('');
            updateSplitTally();
        }

        function resetTransactionBatchState() {
            transactionBatchCount = 0;
            // Left behind, these accumulate across every save the modal ever makes
            // and widen the next run's recalculation to accounts it never touched.
            // Unless a repair is still owed, in which case this is that work.
            if (!balanceRepairPending) {
                batchAffectedAccounts.clear();
                batchEarliestDate = null;
            }
            updateTransactionBatchIndicator();
        }

        function resetTransactionFormForNew() {
            // Keep: date, account
            // Clear: time, amount, payee, category, location, project, note
            const now = new Date();
            document.getElementById('time').value = now.toTimeString().split(' ')[0].substring(0,5);
            document.getElementById('amount').value = '';
            colorAmountInput(document.getElementById('amount'));
            updateAmountSignToggle();
            document.getElementById('payee').value = '';
            clearPayeeCategoryHints();
            document.getElementById('parentCategory').value = '';
            document.getElementById('category').value = '';
            document.getElementById('category').disabled = true;
            document.getElementById('location').value = '';
            document.getElementById('project').value = '';
            document.getElementById('note').value = '';
            
            // Focus on amount for quick entry
            document.getElementById('amount').focus();
        }

        function resetTransferBatchState() {
            transferBatchCount = 0;
            if (!balanceRepairPending) {
                batchAffectedAccounts.clear();
                batchEarliestDate = null;
            }
            updateTransferBatchIndicator();
        }

        function resetTransferFormForNew() {
            // Keep: date, fromAccount, toAccount
            // Clear: time, amounts, note
            const now = new Date();
            document.getElementById('transferTime').value = now.toTimeString().split(' ')[0].substring(0,5);
            document.getElementById('fromAmount').value = '';
            document.getElementById('toAmount').value = '';
            document.getElementById('transferNote').value = '';
            
            // Focus on amount for quick entry
            document.getElementById('fromAmount').focus();
        }


        async function saveNewCategory(){ 
            const name = document.getElementById('newCategoryName').value.trim();
            if (!name) { alert('Please enter a category name'); return; }
            await fetch(`${API_URL}/categories`, {
                method: 'POST',
                body: JSON.stringify({ name: name, parent: null }),
                headers: {'Content-Type': 'application/json'}
            });
            closeModal('newCategoryModal');
            document.getElementById('newCategoryName').value = '';
            await loadCategories();
            // Auto-select the newly created parent category (option value is its name)
            document.getElementById('parentCategory').value = name;
            loadSubcategories(name);
        }

        async function saveNewLocation(){ const n=document.getElementById('newLocationName').value; const created=await (await fetch(`${API_URL}/locations`,{method:'POST',body:JSON.stringify({name:n}),headers:{'Content-Type':'application/json'}})).json(); closeModal('newLocationModal'); await loadLocations(); if(created && created.id) document.getElementById('location').value=created.id; }

        async function saveNewProject(){ const n=document.getElementById('newProjectName').value; const created=await (await fetch(`${API_URL}/projects`,{method:'POST',body:JSON.stringify({name:n}),headers:{'Content-Type':'application/json'}})).json(); closeModal('newProjectModal'); await loadProjects(); if(created && created.id) document.getElementById('project').value=created.id; }

        async function saveNewSubcategory(){ 
            const name = document.getElementById('newSubcategoryName').value.trim();
            const parent = document.getElementById('newSubcategoryParent').value;
            if (!name) { alert('Please enter a subcategory name'); return; }
            if (!parent) { alert('Parent category is required'); return; }
            const response = await fetch(`${API_URL}/categories`, {
                method: 'POST',
                body: JSON.stringify({ name: name, parent: parent }),
                headers: {'Content-Type': 'application/json'}
            });
            const newCategory = await response.json();
            closeModal('newSubcategoryModal');
            document.getElementById('newSubcategoryName').value = '';
            await loadCategories();
            loadSubcategories(parent);
            document.getElementById('category').value = newCategory.id;
        }

        async function saveSplit(payeeId, payeeName) {
            syncSplitLinesFromDom();

            // Saving an existing split with one line left dissolves it back
            // into an ordinary transaction; creating one always needs two.
            if (splitLines.length < 2 && !editingSplitGroup) {
                alert('A split needs at least two lines. Use "Back to a single line" instead.');
                return false;
            }
            if (splitLines.some(l => !Number.isFinite(l.amount) || l.amount === 0)) {
                alert('Every line of a split needs an amount.');
                return false;
            }

            const currency = splitCurrency();
            const total = parseMoneyAmount(document.getElementById('amount').value);
            const left = Math.round((total - splitAssignedTotal()) * 100) / 100;
            if (Math.abs(left) >= 0.005) {
                alert(`The lines add up to ${formatMoneyWithSymbol(splitAssignedTotal(), currency)}, `
                    + `but the transaction is ${formatMoneyWithSymbol(total, currency)}. `
                    + `${formatMoneyWithSymbol(left, currency)} is still unassigned.`);
                return false;
            }

            const accountId = parseInt(document.getElementById('account').value);
            const locationId = document.getElementById('location').value;
            const payload = {
                date: `${document.getElementById('date').value}T${document.getElementById('time').value}:00`,
                account_id: accountId,
                currency,
                payee_id: payeeId,
                location_id: locationId ? parseInt(locationId) : null,
                lines: splitLines.map(l => ({
                    id: l.id || null,
                    amount: l.amount,
                    category_id: l.category_id,
                    project_id: l.project_id,
                    note: l.note ? l.note : null,
                })),
            };

            const editing = editingSplitGroup;
            const res = await fetch(
                `${API_URL}/transactions/split${editing ? '/' + editing : ''}`,
                { method: editing ? 'PUT' : 'POST', body: JSON.stringify(payload),
                  headers: { 'Content-Type': 'application/json' } });

            if (!res.ok) {
                alert('Error saving the split transaction');
                return false;
            }

            closeTransactionModal();
            try {
                await loadRecentTransactions(true);
                loadPayees();
            } catch (e) { console.error(e); }
            return true;
        }

        async function saveTransaction(saveAndNew = false) {
            const form = document.getElementById('transactionForm');
            const isEdit = form.dataset.editingId;

            // Validate required fields
            if (!document.getElementById('amount').value || !document.getElementById('account').value) {
                alert('Please fill in Amount and Account');
                return false;
            }

            const payeeName = document.getElementById('payee').value.trim();
            let payeeId = null;
            if (payeeName) {
                const exist = allPayees.find(p => p.name.toLowerCase() === payeeName.toLowerCase());
                if(exist) payeeId = exist.id;
                else payeeId = (await (await fetch(`${API_URL}/payees`, { method:'POST', body:JSON.stringify({name:payeeName}), headers:{'Content-Type':'application/json'} })).json()).id;
            }

            if (splitMode) return await saveSplit(payeeId, payeeName);

            const accountId = parseInt(document.getElementById('account').value);
            const data = {
                date: `${document.getElementById('date').value}T${document.getElementById('time').value}:00`,
                amount: parseMoneyAmount(document.getElementById('amount').value),
                currency: document.getElementById('account').selectedOptions[0].dataset.currency,
                account_id: accountId,
                category_id: document.getElementById('category').value ? parseInt(document.getElementById('category').value) : null,
                payee_id: payeeId,
                location_id: document.getElementById('location').value ? parseInt(document.getElementById('location').value) : null,
                project_id: document.getElementById('project').value ? parseInt(document.getElementById('project').value) : null,
                note: document.getElementById('note').value
            };

            // Track affected account and earliest date for later recalculation (batch mode)
            batchAffectedAccounts.add(accountId);
            const txDate = data.date;
            if (!batchEarliestDate || txDate < batchEarliestDate) batchEarliestDate = txDate;

            // Skip recalculation only in batch mode (Save & New); otherwise let backend recalculate incrementally
            const skipRecalc = saveAndNew && !isEdit;
            const url = `${API_URL}/transactions${isEdit ? '/'+isEdit : ''}?skip_recalculation=${skipRecalc}`;

            const res = await fetch(url, {
                method: isEdit ? 'PUT' : 'POST',
                body: JSON.stringify(data),
                headers: {'Content-Type': 'application/json'}
            });

            if (!res.ok) {
                alert('Error saving transaction');
                return false;
            }

            const savedTx = await res.json();

            if (saveAndNew && !isEdit) {
                // Increment batch counter and update UI
                transactionBatchCount++;
                updateTransactionBatchIndicator();
                resetTransactionFormForNew();
                loadPayees(); // background
                return true;
            } else {
                // Closes at once — the save reads as instant — while the promise
                // marks when a deferred run's balances are final.
                const balancesReady = closeTransactionModal();

                // Optimistic UI: insert transaction into list without full reload
                if (!isEdit) {
                    const account = accountsMap.get(accountId);
                    const optimisticTx = {
                        ...savedTx,
                        account_name: account ? account.name : '',
                        category_name: allCategories.find(c => c.id === data.category_id)?.name || null,
                        payee_name: payeeName || null,
                        location_name: allLocations.find(l => l.id === data.location_id)?.name || null,
                        project_name: null
                    };
                    insertOptimistic('transaction', optimisticTx);
                }

                // Background: refresh display once the balances are settled
                (async () => {
                    try {
                        await balancesReady;
                        await loadRecentTransactions(true);
                        loadPayees();
                    } catch(e) { console.error(e); }
                })();
                return true;
            }
        }

        async function saveTransactionAndNew() {
            await guardedSave(() => saveTransaction(true));
        }

        async function saveTransfer(saveAndNew = false) {
            const form = document.getElementById('transferForm');
            const isEdit = form.dataset.editingOutId;

            // Validate required fields
            if (!document.getElementById('fromAmount').value || !document.getElementById('fromAccount').value || !document.getElementById('toAccount').value) {
                alert('Please fill in all required fields');
                return false;
            }

            if(isEdit) {
                await fetch(`${API_URL}/transactions/${form.dataset.editingOutId}`, {method:'DELETE'});
                await fetch(`${API_URL}/transactions/${form.dataset.editingInId}`, {method:'DELETE'});
            }

            const fromAccountId = parseInt(document.getElementById('fromAccount').value);
            const toAccountId = parseInt(document.getElementById('toAccount').value);

            const data = {
                date: `${document.getElementById('transferDate').value}T${document.getElementById('transferTime').value}:00`,
                from_account_id: fromAccountId,
                to_account_id: toAccountId,
                from_amount: parseMoneyAmount(document.getElementById('fromAmount').value),
                to_amount: document.getElementById('toAmount').value ? parseMoneyAmount(document.getElementById('toAmount').value) : null,
                note: document.getElementById('transferNote').value
            };

            // Track affected accounts and earliest date for later recalculation (batch mode)
            batchAffectedAccounts.add(fromAccountId);
            batchAffectedAccounts.add(toAccountId);
            const txDate = data.date;
            if (!batchEarliestDate || txDate < batchEarliestDate) batchEarliestDate = txDate;

            // Skip recalculation only in batch mode (Save & New); otherwise let backend recalculate incrementally
            const skipRecalc = saveAndNew && !isEdit;
            const url = `${API_URL}/transactions/transfers?skip_recalculation=${skipRecalc}`;

            const res = await fetch(url, {
                method: 'POST',
                body: JSON.stringify(data),
                headers: {'Content-Type': 'application/json'}
            });

            if (!res.ok) {
                alert('Error saving transfer');
                return false;
            }

            const savedTransfer = await res.json();

            if (saveAndNew && !isEdit) {
                // Increment batch counter and update UI
                transferBatchCount++;
                updateTransferBatchIndicator();
                resetTransferFormForNew();
                return true;
            } else {
                // Closes at once; the promise marks when a deferred run's
                // balances are final.
                const balancesReady = closeTransferModal();

                // Optimistic UI: insert transfer into list
                if (!isEdit) {
                    const fromAcc = accountsMap.get(fromAccountId);
                    const toAcc = accountsMap.get(toAccountId);
                    insertOptimistic('transfer', {
                        transfer_out_id: savedTransfer.transfer_out?.id,
                        transfer_in_id: savedTransfer.transfer_in?.id,
                        date: data.date,
                        from_account_name: fromAcc ? fromAcc.name : '',
                        from_amount: Math.abs(data.from_amount),
                        from_currency: fromAcc ? fromAcc.currency : '',
                        to_account_name: toAcc ? toAcc.name : '',
                        to_amount: data.to_amount || data.from_amount,
                        to_currency: toAcc ? toAcc.currency : '',
                        note: data.note,
                        from_balance: null,
                        to_balance: null
                    });
                    displayCombinedTransactions();
                }

                // Background: refresh display once the balances are settled
                (async () => {
                    try {
                        await balancesReady;
                        await loadRecentTransactions(true);
                    } catch(e) { console.error(e); }
                })();
                return true;
            }
        }

        async function saveTransferAndNew() {
            await guardedSave(() => saveTransfer(true));
        }

        function setModalMode(modalId, isEdit) {
            const m = document.getElementById(modalId);
            m.querySelectorAll('[data-edit-only]').forEach(b => b.style.display = isEdit ? '' : 'none');
            m.querySelectorAll('[data-new-only]').forEach(b => b.style.display = isEdit ? 'none' : '');
        }

        function showConnectionNotice(message) {
            const el = document.getElementById('errorMessage');
            if (!el) return;
            el.textContent = message || '';
            el.style.display = message ? 'block' : 'none';
        }

        function showPayeeCategoryHints(payee, appliedId) {
            const box = document.getElementById('payeeCategoryHints');
            const options = (payee.top_categories || [])
                .filter(c => c.category_id && allCategories.some(x => x.id === c.category_id));
            if (options.length < 2) { clearPayeeCategoryHints(); return; }

            box.innerHTML = '<span class="hint-lead">Usually</span>' + options.map(c => {
                const label = c.parent ? `${escapeHtml(c.parent)} › ${escapeHtml(c.name)}`
                                       : escapeHtml(c.name || '');
                return `<button type="button" data-category-id="${c.category_id}"
                    class="${c.category_id === appliedId ? 'is-on' : ''}"
                    title="${c.count} transaction${c.count === 1 ? '' : 's'} filed here">${label}<span class="hint-count">${c.count}</span></button>`;
            }).join('');

            box.querySelectorAll('button').forEach(btn => {
                btn.onclick = () => {
                    const id = Number(btn.dataset.categoryId);
                    if (!applyCategoryPair(id)) return;
                    box.querySelectorAll('button').forEach(b => b.classList.toggle('is-on', b === btn));
                };
            });
            box.hidden = false;
        }

        function splitAssignedTotal() {
            return Math.round(splitLines.reduce((sum, l) => sum + (Number.isFinite(l.amount) ? l.amount : 0), 0) * 100) / 100;
        }

        function splitCurrency() {
            const opt = document.getElementById('account').selectedOptions[0];
            return (opt && opt.dataset.currency) || displayCurrency;
        }

        function splitLineHtml(index, line) {
            const parents = [...new Set(allCategories.map(c => c.parent || c.name))].sort();
            const parentOpts = parents.map(p =>
                `<option value="${escapeHtml(p)}" ${p === line.parent ? 'selected' : ''}>${escapeHtml(p)}</option>`).join('');
            const subs = allCategories.filter(c => c.parent === line.parent)
                                      .sort((a, b) => a.name.localeCompare(b.name));
            const subOpts = subs.map(c =>
                `<option value="${c.id}" ${c.id === line.category_id ? 'selected' : ''}>${escapeHtml(c.name)}</option>`).join('');
            const projOpts = allProjects.map(p =>
                `<option value="${p.id}" ${p.id === line.project_id ? 'selected' : ''}>${escapeHtml(p.name)}</option>`).join('');

            return `
            <div class="split-line-card">
                <div class="split-line-head">
                    <span>Line ${index + 1}</span>
                    <button type="button" class="split-line-remove" onclick="removeSplitLine(${index})"
                            title="Remove this line" aria-label="Remove line ${index + 1}">&times;</button>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Amount</label>
                        <input type="number" step="0.01" inputmode="decimal" id="splitAmount_${index}"
                               value="${line.amount ?? ''}" oninput="onSplitLineInput()">
                    </div>
                    <div class="form-group">
                        <label>Category</label>
                        <select id="splitParent_${index}" onchange="onSplitParentChange(${index})">
                            <option value="">Select...</option>${parentOpts}
                        </select>
                    </div>
                </div>
                <div class="form-row">
                    <div class="form-group">
                        <label>Sub</label>
                        <select id="splitCat_${index}" ${line.parent ? '' : 'disabled'} onchange="onSplitLineInput()">
                            <option value="">${line.parent ? 'Select...' : 'Select parent...'}</option>${subOpts}
                        </select>
                    </div>
                    <div class="form-group">
                        <label>Project</label>
                        <select id="splitProj_${index}" onchange="onSplitLineInput()">
                            <option value="">Select...</option>${projOpts}
                        </select>
                    </div>
                </div>
                <div class="form-group">
                    <label>Note</label>
                    <input type="text" id="splitNote_${index}" value="${escapeHtml(line.note || '')}" oninput="onSplitLineInput()">
                </div>
            </div>`;
        }

        function splitLinesFromSingleForm() {
            const amount = parseMoneyAmount(document.getElementById('amount').value);
            const catId = document.getElementById('category').value;
            const projId = document.getElementById('project').value;
            return [
                {
                    id: null,
                    amount: Number.isFinite(amount) ? amount : null,
                    parent: document.getElementById('parentCategory').value || null,
                    category_id: catId ? parseInt(catId) : null,
                    project_id: projId ? parseInt(projId) : null,
                    note: document.getElementById('note').value || '',
                },
                { id: null, amount: null, parent: null, category_id: null, project_id: null, note: '' },
            ];
        }

        function syncAmountSign() {
            const input = document.getElementById('amount');
            const value = parseFloat(input.value);
            amountIsNegative = !isNaN(value) && value < 0;
            updateAmountSignToggle();
            colorAmountInput(input);
        }

        function syncSplitLinesFromDom() {
            splitLines = splitLines.map((line, i) => {
                const amountEl = document.getElementById(`splitAmount_${i}`);
                if (!amountEl) return line;
                const raw = amountEl.value;
                const cat = document.getElementById(`splitCat_${i}`).value;
                const proj = document.getElementById(`splitProj_${i}`).value;
                return {
                    ...line,
                    amount: raw === '' ? null : parseMoneyAmount(raw),
                    parent: document.getElementById(`splitParent_${i}`).value || null,
                    category_id: cat ? parseInt(cat) : null,
                    project_id: proj ? parseInt(proj) : null,
                    note: document.getElementById(`splitNote_${i}`).value,
                };
            });
        }

        function toggleAmountSign() {
            const input = document.getElementById('amount');
            const value = parseFloat(input.value);
            amountIsNegative = !amountIsNegative;
            if (!isNaN(value) && value !== 0) input.value = String(-value);
            updateAmountSignToggle();
            colorAmountInput(input);
            if (splitMode) updateSplitTally();
            input.focus();
        }

        function toggleSplitMode() {
            if (splitMode) {
                // Going back to one line keeps whatever the first line said.
                syncSplitLinesFromDom();
                const first = splitLines[0] || {};
                exitSplitMode();
                setModalMode('transactionModal', !!document.getElementById('transactionForm').dataset.editingId);
                if (first.parent) {
                    document.getElementById('parentCategory').value = first.parent;
                    loadSubcategories(first.parent);
                    if (first.category_id) document.getElementById('category').value = first.category_id;
                }
                document.getElementById('project').value = first.project_id || '';
                document.getElementById('note').value = first.note || '';
            } else {
                // Splitting a transaction already being edited turns that row
                // into the split's first line — the backend accepts its id as
                // the group, so nothing is deleted and recreated.
                const editingId = document.getElementById('transactionForm').dataset.editingId;
                const lines = splitLinesFromSingleForm();
                if (editingId) {
                    lines[0].id = parseInt(editingId);
                    editingSplitGroup = parseInt(editingId);
                }
                enterSplitMode(lines);
            }
        }

        function updateAmountSignToggle() {
            const btn = document.getElementById('amountSignToggle');
            if (!btn) return;
            btn.textContent = amountIsNegative ? '−' : '+';
            btn.classList.toggle('is-negative', amountIsNegative);
            btn.classList.toggle('is-positive', !amountIsNegative);
        }

        function updateSplitTally() {
            const total = parseMoneyAmount(document.getElementById('amount').value);
            const assigned = splitAssignedTotal();
            const currency = splitCurrency();
            const hasTotal = Number.isFinite(total);
            const left = hasTotal ? Math.round((total - assigned) * 100) / 100 : 0;

            document.getElementById('splitTotal').textContent =
                hasTotal ? formatMoneyWithSymbol(total, currency) : '—';
            document.getElementById('splitAssigned').textContent = formatMoneyWithSymbol(assigned, currency);

            const leftEl = document.getElementById('splitLeft');
            leftEl.textContent = hasTotal ? formatMoneyWithSymbol(left, currency) : '—';
            leftEl.classList.toggle('is-off', hasTotal && Math.abs(left) >= 0.005);
            leftEl.classList.toggle('is-done', hasTotal && Math.abs(left) < 0.005);
            document.getElementById('splitRestBtn').disabled = !hasTotal || Math.abs(left) < 0.005;
        }

        function updateTransactionBatchIndicator() {
            const indicator = document.getElementById('transactionBatchIndicator');
            const countEl = document.getElementById('transactionBatchCount');
            countEl.textContent = transactionBatchCount;
            if (transactionBatchCount > 0) {
                indicator.classList.add('active');
            } else {
                indicator.classList.remove('active');
            }
        }

        function updateTransferBatchIndicator() {
            const indicator = document.getElementById('transferBatchIndicator');
            const countEl = document.getElementById('transferBatchCount');
            countEl.textContent = transferBatchCount;
            if (transferBatchCount > 0) {
                indicator.classList.add('active');
            } else {
                indicator.classList.remove('active');
            }
        }


        function onAmountInput(input) {
            const raw = input.value.trim();
            const value = parseFloat(raw);
            if (amountIsNegative && /^\d*\.?\d+$/.test(raw)) {
                // Sign was set before typing: put the minus back in front.
                input.value = '-' + raw;
            } else if (!isNaN(value) && value !== 0) {
                amountIsNegative = value < 0;
            }
            updateAmountSignToggle();
            colorAmountInput(input);
            if (splitMode) updateSplitTally();
        }

    // ---- mounting --------------------------------------------------------

    /**
     * What the form looks like, wherever it opens. Taken from the page it came
     * from and tied to a class of its own rather than to where it sits, because
     * a dialog is moved to the end of <body> as it opens -- see openModal.
     *
     * The shared field styling is forms.css; this is what only this form has:
     * the sign button, the split editor, the batch notice.
     */
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
      .dlf-tx {
        --bg:#FAF3E9; --paper:#FFFDFA; --panel:#F3E9DA; --card:#E4D5C1;
        --border:#D8C6B0; --hair:#EFE4D3; --field:#E4D5C1; --chip:#F1E7D6;
        --muted:#8A7D6C; --text:#23201C; --ink:#23201C; --accent:#B0402E;
        --red:#B0402E; --green:#3C7A57; --yellow:#C6893F; --ink-blue:#3C5A6E;
        --btn-primary:#2B2621; --danger:#B0402E;
      }
      .dlf-tx .cat-hints .hint-lead { font-family: 'IBM Plex Sans', sans-serif; font-size: 11px; text-transform: uppercase; letter-spacing: .8px; color: var(--muted); margin-right: 2px; }
      .dlf-tx .cat-hints button { font-family: 'IBM Plex Sans', sans-serif; font-size: 12px; padding: 5px 11px; border-radius: 999px; cursor: pointer; border: 1px solid var(--field); background: var(--paper); color: var(--text); }
      .dlf-tx .cat-hints button:hover { border-color: var(--btn-primary); color: var(--ink); }
      .dlf-tx .cat-hints button .hint-count { color: var(--muted); margin-left: 5px; }
      .dlf-tx .cat-hints button.is-on { background: var(--field); border-color: var(--btn-primary); color: var(--ink); font-weight: 600; }
      .dlf-tx .cat-hints button.is-on .hint-count { color: var(--text); }
      .dlf-tx .split-caret::before { content: ''; display: inline-block; width: 0; height: 0; border-left: 4px solid currentColor; border-top: 3.5px solid transparent; border-bottom: 3.5px solid transparent; transition: transform 0.15s; transform-origin: 25% 50%; }
      .dlf-tx .split-lines { display: none; grid-column: 1 / -1; order: 98; width: 100%; margin-top: 9px; padding-top: 8px; border-top: 1px dashed var(--border); }
      .dlf-tx .split-line { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 14px; padding: 3px 0 3px 22px; font-size: 12.5px; color: #6E6250; }
      .dlf-tx .split-line .sl-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
      .dlf-tx .split-line .sl-extra { color: var(--muted); }
      .dlf-tx .split-line .sl-amt { font-variant-numeric: tabular-nums; text-align: right; }
      .dlf-tx .split-line-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; font-size: 10.5px; letter-spacing: 1.5px; text-transform: uppercase; color: var(--muted); font-family: 'IBM Plex Sans', sans-serif; font-weight: 700; }
      .dlf-tx .split-line-remove { background: none; border: none; cursor: pointer; color: var(--muted); font-size: 18px; line-height: 1; padding: 0 2px; min-height: 0; }
      .dlf-tx .split-line-remove:hover { color: var(--red); }
      .dlf-tx .split-tally { display: flex; flex-wrap: wrap; gap: 6px 18px; align-items: baseline; padding: 10px 0 4px; font-size: 12.5px; font-variant-numeric: tabular-nums; border-top: 1px solid var(--hair); }
      .dlf-tx .split-tally .st-label { color: var(--muted); font-size: 11px; letter-spacing: .5px; text-transform: uppercase; }
      .dlf-tx .split-tally .st-left.is-off { color: var(--red); font-weight: 600; }
      .dlf-tx .split-tally .st-left.is-done { color: var(--green); font-weight: 600; }
      .dlf-tx .split-actions { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 18px; }
      .dlf-tx .split-actions button { background: none; border: 1px solid var(--border); border-radius: 8px; padding: 7px 14px; font-size: 12.5px; cursor: pointer; color: var(--text); font-family: 'IBM Plex Mono', monospace; }
      .dlf-tx .split-actions button:hover { border-color: var(--accent); color: var(--accent); }
      .dlf-tx .amount-negative { color: var(--red); }
      .dlf-tx .amount-positive { color: var(--green); }
      .modal.dlf-tx input.amount-positive { color: var(--green); }
      .dlf-tx .amount-field { display: flex; align-items: stretch; gap: 8px; }
      .dlf-tx .amount-field input { flex: 1 1 auto; min-width: 0; }
      .dlf-tx .sign-toggle { flex: 0 0 auto; width: 44px; height: 34px; align-self: center; padding: 0; border: 1px solid var(--field); border-radius: 8px; background: #fff; color: var(--muted); font-family: 'IBM Plex Sans', sans-serif; font-size: 18px; font-weight: 600; line-height: 1; cursor: pointer; transition: color 0.2s, border-color 0.2s, background 0.2s; }
      .dlf-tx #splitToggleBtn { border: none; background: none; padding: 8px 0; color: var(--red); font-family: 'IBM Plex Sans', sans-serif; font-size: 12.5px; font-weight: 700; }
      .dlf-tx #splitToggleBtn::before { content: '\\21B3\\00A0'; }
      .dlf-tx .sign-toggle.is-negative { color: var(--red); border-color: var(--red); background: rgba(176, 64, 46, 0.08); }
      .dlf-tx .sign-toggle.is-positive { color: var(--green); border-color: var(--green); background: rgba(60, 122, 87, 0.08); }
      .modal.dlf-tx.active { display: flex; }
      .dlf-tx .modal-content { background-color: var(--paper); padding: 0; border-radius: 16px; width: 90%; max-width: 520px; max-height: 90vh; overflow-y: auto; box-shadow: 0 24px 60px rgba(40,28,14,.35); border: 1px solid var(--hair); animation: slideUp 0.3s ease; }
      .dlf-tx .modal-header { background: transparent; padding: 18px 24px; border-bottom: 1px solid var(--hair); display: flex; justify-content: space-between; align-items: center; gap: 12px; }
      .dlf-tx .modal-header h3 { margin: 0; color: var(--ink); font-family: 'Playfair Display', serif; font-size: 21px; font-weight: 700; padding-left: 12px; border-left: 3px solid var(--accent); }
      .dlf-tx .modal-close { background: var(--field); border: none; border-radius: 8px; width: 32px; height: 32px; font-size: 18px; line-height: 1; color: #3A342C; cursor: pointer; flex: 0 0 auto; }
      .dlf-tx .modal-close:hover { background: var(--card); color: var(--ink); }
      .dlf-tx .modal-body { padding: 22px 24px; overflow: hidden; }
      .dlf-tx .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
      .modal.dlf-tx input, .modal.dlf-tx select, .modal.dlf-tx textarea { width: 100%; max-width: 100%; box-sizing: border-box; padding: 11px 12px; border: 1px solid var(--field); border-radius: 9px; background: #fff; color: var(--ink); font-family: 'IBM Plex Sans', sans-serif; font-size: 14px; transition: border-color 0.2s; -webkit-appearance: none; appearance: none; }
      .modal.dlf-tx select { background: #fff url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath d='M0 0l5 6 5-6z' fill='%238A7D6C'/%3E%3C/svg%3E") no-repeat right 12px center; padding-right: 28px; cursor: pointer; }
      .modal.dlf-tx input:focus, .modal.dlf-tx select:focus, .modal.dlf-tx textarea:focus { outline: none; border-color: var(--accent); }
      .dlf-tx .modal-footer { display: flex; gap: 12px; margin-top: 24px; }
      .dlf-tx .modal-footer button, .dlf-tx .modal-footer-batch button { flex: 1; padding: 0 20px; height: 46px; border-radius: 9px; border: 1px solid var(--field); font-weight: 500; font-size: 14px; cursor: pointer; font-family: 'Playfair Display', serif; -webkit-appearance: none; appearance: none; }
      .dlf-tx .btn-save-new { background: var(--field) !important; color: #3A342C !important; border-color: var(--field) !important; }
      .dlf-tx .btn-save { background: var(--btn-primary) !important; color: #F6EFE3 !important; border-color: var(--btn-primary) !important; }
      .dlf-tx .btn-cancel { background: #fff; color: #3A342C; }
      .dlf-tx .btn-dup { background: var(--field) !important; color: #3A342C !important; border-color: var(--field) !important; }
      .dlf-tx .btn-del { background: #FBEDE9 !important; color: var(--red) !important; border-color: #E7C3B8 !important; }
      .dlf-tx .batch-indicator.active { display: flex; }
      .dlf-tx .batch-indicator .count { font-weight: 700; }
      .dlf-tx .modal-footer-batch .btn-save-new { grid-column: 1 / -1; }

      /* Rules whose selector followed a comment, and which the first pass
         therefore read as part of it. */
      .dlf-tx .split-line-card { border: 1px solid var(--hair); border-radius: 10px; padding: 14px 14px 0; margin-bottom: 12px; background: #FBF4EA; }
      .modal.dlf-tx input.amount-negative { color: var(--red); }
      .dlf-tx #splitToggleRow { justify-content: flex-end; padding: 10px 12px 0; }
      .dlf-tx .modal-content { background-color: var(--paper); padding: 0; border-radius: 16px; width: 90%; max-width: 520px; max-height: 90vh; overflow-y: auto; box-shadow: 0 24px 60px rgba(40,28,14,.35); border: 1px solid var(--hair); animation: slideUp 0.3s ease; }
      .dlf-tx .modal-header { background: transparent; padding: 18px 24px; border-bottom: 1px solid var(--hair); display: flex; justify-content: space-between; align-items: center; gap: 12px; }
      .dlf-tx .modal-header h3 { margin: 0; color: var(--ink); font-family: 'Playfair Display', serif; font-size: 21px; font-weight: 700; padding-left: 12px; border-left: 3px solid var(--accent); }
      .dlf-tx .modal-close { background: var(--field); border: none; border-radius: 8px; width: 32px; height: 32px; font-size: 18px; line-height: 1; color: #3A342C; cursor: pointer; flex: 0 0 auto; }
      .dlf-tx .modal-close:hover { background: var(--card); color: var(--ink); }
      .dlf-tx .modal-body { padding: 22px 24px; overflow: hidden; }
      .dlf-tx .form-group { margin-bottom: 18px; }
      .dlf-tx .modal-footer { display: flex; gap: 12px; margin-top: 24px; }
      .dlf-tx .modal-footer button, .dlf-tx .modal-footer-batch button { flex: 1; padding: 0 20px; height: 46px; border-radius: 9px; border: 1px solid var(--field); font-weight: 500; font-size: 14px; cursor: pointer; font-family: 'Playfair Display', serif; -webkit-appearance: none; appearance: none; }
      .dlf-tx .modal-footer-batch { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-top: 20px; }
      .dlf-tx .modal-footer-batch .btn-save-new { grid-column: 1 / -1; }
      .dlf-tx .cat-hints { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin: -6px 0 14px; }
      .dlf-tx .split-caret { display: inline-block; border: none; background: none; cursor: pointer; padding: 0 4px 0 0; margin: 0; color: inherit; font: inherit; line-height: 1; }
      .modal.dlf-tx { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background-color: rgba(35, 32, 28, 0.4); backdrop-filter: blur(3px); align-items: center; justify-content: center; }
      .modal.dlf-tx input, .modal.dlf-tx select, .modal.dlf-tx textarea { width: 100%; max-width: 100%; box-sizing: border-box; padding: 11px 12px; border: 1px solid var(--field); border-radius: 9px; background: #fff; color: var(--ink); font-family: 'IBM Plex Sans', sans-serif; font-size: 14px; transition: border-color 0.2s; -webkit-appearance: none; appearance: none; }
      .modal.dlf-tx input:focus, .modal.dlf-tx select:focus, .modal.dlf-tx textarea:focus { outline: none; border-color: var(--accent); }
      .dlf-tx .modal-footer button, .dlf-tx .modal-footer-batch button { flex: 1; padding: 0 20px; height: 46px; border-radius: 9px; border: 1px solid var(--field); font-weight: 500; font-size: 14px; cursor: pointer; font-family: 'Playfair Display', serif; -webkit-appearance: none; appearance: none; }
      .dlf-tx .batch-indicator { background: var(--yellow); color: var(--text); padding: 8px 15px; border-radius: 6px; font-size: 13px; display: none; align-items: center; gap: 8px; margin-bottom: 15px; }
    `;

    var MARKUP = `        <div id="newCategoryModal" class="modal dlf-tx">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>New Parent Category</h3>
                    <button class="modal-close" onclick="closeModal('newCategoryModal')">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>Name</label>
                        <input type="text" id="newCategoryName">
                    </div>
                    <div class="modal-footer">
                        <button class="btn-save" onclick="saveNewCategory()">Save</button>
                        <button class="btn-cancel" onclick="closeModal('newCategoryModal')">Cancel</button>
                    </div>
                </div>
            </div>
        </div>

        <div id="newSubcategoryModal" class="modal dlf-tx">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>New Subcategory</h3>
                    <button class="modal-close" onclick="closeModal('newSubcategoryModal')">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>Name</label>
                        <input type="text" id="newSubcategoryName">
                    </div>
                    <div class="form-group">
                        <label>Parent</label>
                        <input id="newSubcategoryParent" readonly style="background:#f0f0f0">
                    </div>
                    <div class="modal-footer">
                        <button class="btn-save" onclick="saveNewSubcategory()">Save</button>
                        <button class="btn-cancel" onclick="closeModal('newSubcategoryModal')">Cancel</button>
                    </div>
                </div>
            </div>
        </div>

        <div id="newLocationModal" class="modal dlf-tx">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>New Location</h3>
                    <button class="modal-close" onclick="closeModal('newLocationModal')">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>Name</label>
                        <input type="text" id="newLocationName">
                    </div>
                    <div class="modal-footer">
                        <button class="btn-save" onclick="saveNewLocation()">Save</button>
                        <button class="btn-cancel" onclick="closeModal('newLocationModal')">Cancel</button>
                    </div>
                </div>
            </div>
        </div>

        <div id="newProjectModal" class="modal dlf-tx">
            <div class="modal-content">
                <div class="modal-header">
                    <h3>New Project</h3>
                    <button class="modal-close" onclick="closeModal('newProjectModal')">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="form-group">
                        <label>Name</label>
                        <input type="text" id="newProjectName">
                    </div>
                    <div class="modal-footer">
                        <button class="btn-save" onclick="saveNewProject()">Save</button>
                        <button class="btn-cancel" onclick="closeModal('newProjectModal')">Cancel</button>
                    </div>
                </div>
            </div>
        </div>

    <div id="transactionModal" class="modal dlf-tx">
        <div class="modal-content">
            <div class="modal-header"><h3>New Transaction</h3><button class="modal-close" onclick="closeTransactionModal()">&times;</button></div>
            <div class="modal-body">
                <div id="transactionBatchIndicator" class="batch-indicator">
                    ⏳ <span class="count" id="transactionBatchCount">0</span> transaction(s) pending · balances will update on save/cancel
                </div>
                <form id="transactionForm">
                    <div class="form-row">
                        <div class="form-group"><label>Date</label><input type="date" id="date" required></div>
                        <div class="form-group"><label>Time</label><input type="time" id="time" required></div>
                    </div>
                    <div class="form-row">
                        <div class="form-group"><label>Amount</label><div class="amount-field"><input type="number" id="amount" step="0.01" inputmode="decimal" required oninput="onAmountInput(this)" onblur="onAmountInput(this)"><button type="button" id="amountSignToggle" class="sign-toggle is-positive" onclick="toggleAmountSign()" title="Switch between expense (−) and income (+)" aria-label="Switch between expense and income">+</button></div></div>
                        <div class="form-group">
                            <label>Payee</label>
                            <input type="text" id="payee" list="payeeList" placeholder="Who?">
                            <datalist id="payeeList"></datalist>
                        </div>
                    </div>
                    <div class="form-row" id="singleCategoryRow">
                        <div class="form-group"><label>Category</label><select id="parentCategory"><option value="">Select...</option></select></div>
                        <div class="form-group"><label>Sub</label><select id="category" disabled><option value="">Select parent...</option></select></div>
                    </div>
                    <div id="payeeCategoryHints" class="cat-hints" hidden></div>
                    <div class="form-row">
                        <div class="form-group"><label>Account</label><select id="account" required><option value="">Select...</option></select></div>
                        <div class="form-group"><label>Location</label><select id="location"><option value="">Select...</option></select></div>
                    </div>
                    <div id="singleTailFields">
                        <div class="form-group"><label>Project</label><select id="project"><option value="">Select...</option></select></div>
                        <div class="form-group"><label>Note</label><textarea id="note" rows="2"></textarea></div>
                    </div>

                    <!-- Split editor: the amount above is the whole purchase; these lines carve it up -->
                    <div id="splitEditor" style="display:none;">
                        <div id="splitLines"></div>
                        <div class="split-tally">
                            <span><span class="st-label">Total</span> <span id="splitTotal">—</span></span>
                            <span><span class="st-label">On lines</span> <span id="splitAssigned">—</span></span>
                            <span><span class="st-label">Left</span> <span id="splitLeft" class="st-left">—</span></span>
                        </div>
                        <div class="split-actions">
                            <button type="button" onclick="addSplitLine()">+ Add line</button>
                            <button type="button" id="splitRestBtn" onclick="assignSplitRemainder()">Put the rest on a new line</button>
                        </div>
                    </div>

                    <div class="split-actions" id="splitToggleRow">
                        <button type="button" id="splitToggleBtn" onclick="toggleSplitMode()">Split into several lines</button>
                    </div>

                    <div class="modal-footer-batch">
                        <button type="submit" class="btn-save">Save</button>
                        <button type="button" class="btn-cancel" onclick="closeTransactionModal()">Cancel</button>
                        <button type="button" class="btn-save-new" data-new-only onclick="saveTransactionAndNew()">Save & New</button>
                        <button type="button" class="btn-dup" data-edit-only style="display:none;" onclick="duplicateFromModal()">Duplicate</button>
                        <button type="button" class="btn-del" data-edit-only style="display:none;" onclick="deleteFromModal()">Delete</button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <div id="transferModal" class="modal dlf-tx">
        <div class="modal-content">
            <div class="modal-header"><h3>New Transfer</h3><button class="modal-close" onclick="closeTransferModal()">&times;</button></div>
            <div class="modal-body">
                <div id="transferBatchIndicator" class="batch-indicator">
                    ⏳ <span class="count" id="transferBatchCount">0</span> transfer(s) pending · balances will update on save/cancel
                </div>
                <form id="transferForm">
                    <div class="form-row">
                        <div class="form-group"><label>Date</label><input type="date" id="transferDate" required></div>
                        <div class="form-group"><label>Time</label><input type="time" id="transferTime" required></div>
                    </div>
                    <div class="form-row">
                        <div class="form-group"><label>From Account</label><select id="fromAccount" required><option value="">Select...</option></select></div>
                        <div class="form-group"><label>To Account</label><select id="toAccount" required><option value="">Select...</option></select></div>
                    </div>
                    <div class="form-row">
                        <div class="form-group"><label>Amount Sent</label><input type="number" id="fromAmount" step="0.01" inputmode="decimal" required></div>
                        <div class="form-group hidden" id="toAmountGroup">
                            <label>Amount Received</label>
                            <input type="number" id="toAmount" step="0.01" inputmode="decimal" placeholder="Different currency?">
                        </div>
                    </div>
                    <div class="form-group"><label>Note</label><textarea id="transferNote" rows="2"></textarea></div>
                    <div class="modal-footer-batch">
                        <button type="submit" class="btn-save">Save</button>
                        <button type="button" class="btn-cancel" onclick="closeTransferModal()">Cancel</button>
                        <button type="button" class="btn-save-new" data-new-only onclick="saveTransferAndNew()">Save & New</button>
                        <button type="button" class="btn-dup" data-edit-only style="display:none;" onclick="duplicateFromTransferModal()">Duplicate</button>
                        <button type="button" class="btn-del" data-edit-only style="display:none;" onclick="deleteFromTransferModal()">Delete</button>
                    </div>
                </form>
            </div>
        </div>
    </div>
`;

    function mount() {
        if (document.getElementById('transactionModal')) return;
        var style = document.createElement('style');
        style.textContent = STYLE;
        document.head.appendChild(style);
        var host = document.createElement('div');
        host.id = 'dlfTxForms';
        host.innerHTML = MARKUP;
        document.body.appendChild(host);
        var on = function (id, ev, fn) {
            var el = document.getElementById(id);
            if (el) el.addEventListener(ev, fn);
        };
        on('transactionForm', 'submit', handleTransactionSubmit);
        on('transferForm', 'submit', handleTransferSubmit);
        on('payee', 'blur', handlePayeeBlur);
        // "Add new" is the last option of every picker: it opens the dialog that
        // makes one and puts the result back in the picker that asked.
        on('parentCategory', 'change', function (e) {
            if (e.target.value === '__NEW__') { openModal('newCategoryModal'); e.target.value = ''; }
            else loadSubcategories(e.target.value);
        });
        on('category', 'change', function (e) {
            if (e.target.value !== '__NEW__') return;
            document.getElementById('newSubcategoryParent').value =
                document.getElementById('parentCategory').value;
            openModal('newSubcategoryModal');
            e.target.value = '';
        });
        on('location', 'change', function (e) {
            if (e.target.value === '__NEW__') { openModal('newLocationModal'); e.target.value = ''; }
        });
        on('project', 'change', function (e) {
            if (e.target.value === '__NEW__') { openModal('newProjectModal'); e.target.value = ''; }
        });
        on('account', 'change', function (e) {
            if (e.target.value === '__NEW__') { e.target.value = ''; openNewAccountModal('account'); }
        });
        on('fromAccount', 'change', function (e) {
            if (e.target.value === '__NEW__') { e.target.value = ''; openNewAccountModal('fromAccount'); }
            checkTransferCurrencies();
        });
        on('toAccount', 'change', function (e) {
            if (e.target.value === '__NEW__') { e.target.value = ''; openNewAccountModal('toAccount'); }
            checkTransferCurrencies();
        });
        // A click on the backdrop closes the dialog it fell on -- through the
        // door, not the window. An open dialog puts the page behind it out of
        // reach (`inert`, so a stray tab or click cannot land there), and only
        // closing properly hands it back. Merely dropping the `active` class
        // left the page unreachable: the quick-add button in the corner stopped
        // answering, and nothing said why.
        window.addEventListener('click', function (e) {
            if (!e.target.classList || !e.target.classList.contains('modal')) return;
            if (e.target.id === 'transactionModal') closeTransactionModal();
            else if (e.target.id === 'transferModal') closeTransferModal();
            else closeModal(e.target.id);
        });
    }

    // Every name the markup calls by name.
    global.addSplitLine = addSplitLine;
    global.applyCategoryPair = applyCategoryPair;
    global.assignSplitRemainder = assignSplitRemainder;
    global.checkTransferCurrencies = checkTransferCurrencies;
    global.clearPayeeCategoryHints = clearPayeeCategoryHints;
    global.closeTransactionModal = closeTransactionModal;
    global.closeTransferModal = closeTransferModal;
    global.colorAmountInput = colorAmountInput;
    global.deleteFromModal = deleteFromModal;
    global.deleteFromTransferModal = deleteFromTransferModal;
    global.duplicateFromModal = duplicateFromModal;
    global.duplicateFromTransferModal = duplicateFromTransferModal;
    global.editSplit = editSplit;
    global.editTransaction = editTransaction;
    global.editTransfer = editTransfer;
    global.enterSplitMode = enterSplitMode;
    global.exitSplitMode = exitSplitMode;
    global.guardedSave = guardedSave;
    global.handlePayeeBlur = handlePayeeBlur;
    global.handleTransactionSubmit = handleTransactionSubmit;
    global.handleTransferSubmit = handleTransferSubmit;
    global.loadSubcategories = loadSubcategories;
    global.onSplitLineInput = onSplitLineInput;
    global.onSplitParentChange = onSplitParentChange;
    global.openTransactionModal = openTransactionModal;
    global.openTransferModal = openTransferModal;
    global.recalculateAffectedBalances = recalculateAffectedBalances;
    global.removeSplitLine = removeSplitLine;
    global.renderSplitLines = renderSplitLines;
    global.resetTransactionBatchState = resetTransactionBatchState;
    global.resetTransactionFormForNew = resetTransactionFormForNew;
    global.resetTransferBatchState = resetTransferBatchState;
    global.resetTransferFormForNew = resetTransferFormForNew;
    global.saveNewCategory = saveNewCategory;
    global.saveNewLocation = saveNewLocation;
    global.saveNewProject = saveNewProject;
    global.saveNewSubcategory = saveNewSubcategory;
    global.saveSplit = saveSplit;
    global.saveTransaction = saveTransaction;
    global.saveTransactionAndNew = saveTransactionAndNew;
    global.saveTransfer = saveTransfer;
    global.saveTransferAndNew = saveTransferAndNew;
    global.setModalMode = setModalMode;
    global.onAmountInput = onAmountInput;
    global.openNewAccountModal = openNewAccountModal;
    global.showConnectionNotice = showConnectionNotice;
    global.showPayeeCategoryHints = showPayeeCategoryHints;
    global.splitAssignedTotal = splitAssignedTotal;
    global.splitCurrency = splitCurrency;
    global.splitLineHtml = splitLineHtml;
    global.splitLinesFromSingleForm = splitLinesFromSingleForm;
    global.syncAmountSign = syncAmountSign;
    global.syncSplitLinesFromDom = syncSplitLinesFromDom;
    global.toggleAmountSign = toggleAmountSign;
    global.toggleSplitMode = toggleSplitMode;
    global.updateAmountSignToggle = updateAmountSignToggle;
    global.updateSplitTally = updateSplitTally;
    global.updateTransactionBatchIndicator = updateTransactionBatchIndicator;
    global.updateTransferBatchIndicator = updateTransferBatchIndicator;

    global.DelfinTxForm = {
        configure: function (options) {
            Object.keys(options || {}).forEach(function (k) {
                if (options[k] !== undefined) cfg[k] = options[k];
            });
            API_URL = cfg.apiBase;
            mount();
        },
        mount: mount,
        refresh: function () { syncCatalogues(); populateFormSelects(); },
        openTransaction: async function () { mount(); await ensureCatalogues(); openTransactionModal(); },
        openTransfer: async function () { mount(); await ensureCatalogues(); openTransferModal(); },
        editTransaction: async function (id) { mount(); await ensureCatalogues(); return editTransaction(id); },
        editTransfer: async function () { mount(); await ensureCatalogues(); return editTransfer.apply(null, arguments); },
        editSplit: async function (g) { mount(); await ensureCatalogues(); return editSplit(g); },
        // A deferred run of Save & New leaves balances to repair; the page asks
        // when the connection comes back.
        pendingBalances: function () { return batchAffectedAccounts.size; },
        flushBalances: function () { return recalculateAffectedBalances(); },
    };
})(window);
