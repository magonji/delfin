"""
The read-only reports behind two of the Tools page's tools: Exchange Rates (what
rates Delfin holds and where they fall short) and Data Health (what in the
ledger is inconsistent, and which of it can be put right automatically).

Nothing here writes, except the explicit fixes at the bottom, each of which the
page only runs when asked to.
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, or_, and_, select
from sqlalchemy.orm import Session

from backend.models import (
    Account, BudgetItem, Category, ExchangeRate, Loan, Location, Payee, Project,
    RecurringExpense, Transaction,
)
from backend.helpers import get_base_currency


# get_rate_for_date looks back this many days for a rate before it gives up and
# the caller falls back to 1:1, so a hole any wider than this is a real one.
RATE_LOOKBACK_DAYS = 7
# The ECB publishes on working days: a rate four days old is only a long weekend.
STALE_AFTER_DAYS = 4
# How many example rows a Data Health check sends back.
SAMPLE_SIZE = 50


def _d(value) -> Optional[date]:
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


# =============================================================================
# EXCHANGE RATES
# =============================================================================

def rates_overview(db: Session) -> dict:
    """
    One row per currency Delfin needs a rate for: how far its stored rates reach,
    the latest one against the display currency, and anything that would make a
    conversion fall back to 1:1 -- no rates at all, transactions dated before
    the first rate, or a hole in the history wider than the lookup bridges.
    """
    from backend.update_exchange_rates import get_currencies_in_use

    base = get_base_currency(db)
    today = date.today()

    needed = set(get_currencies_in_use(db))
    needed |= {c for (c,) in db.query(Transaction.currency).distinct() if c}
    needed |= {c for (c,) in db.query(Account.currency).distinct() if c}
    needed.add(base)

    first_tx = {c: _d(d) for c, d in db.query(
        Transaction.currency, func.min(Transaction.date)
    ).group_by(Transaction.currency).all() if c}
    tx_counts = dict(db.query(Transaction.currency, func.count(Transaction.id))
                     .group_by(Transaction.currency).all())

    rows_by_currency: Dict[str, List[Tuple[date, float]]] = {}
    for currency, d, rate in db.query(ExchangeRate.currency, ExchangeRate.date, ExchangeRate.rate) \
            .filter(ExchangeRate.currency.in_(needed)) \
            .order_by(ExchangeRate.currency, ExchangeRate.date).all():
        rows_by_currency.setdefault(currency, []).append((_d(d), rate))

    def latest(currency):
        if currency == 'GBP':
            return 1.0
        rows = rows_by_currency.get(currency)
        return rows[-1][1] if rows else None

    base_rate = latest(base)

    currencies = []
    for code in sorted(needed, key=lambda c: (c != base, c)):
        entry = {
            "currency": code,
            "is_base": code == base,
            "transactions": tx_counts.get(code, 0),
            "first_needed": first_tx.get(code).isoformat() if first_tx.get(code) else None,
            "first_rate": None, "last_rate": None, "rates_stored": 0,
            "largest_gap_days": 0, "largest_gap_from": None,
            "uncovered_transactions": 0,
            "per_base": None,   # 1 base = per_base units of this currency
            "issues": [],
        }

        if code == 'GBP':
            # The internal reference: never stored, always 1.
            entry["status"] = "ok"
        else:
            rows = rows_by_currency.get(code, [])
            entry["rates_stored"] = len(rows)
            if not rows:
                entry["issues"].append("No rates stored: amounts in this currency are counted 1:1.")
            else:
                first, last = rows[0][0], rows[-1][0]
                entry["first_rate"] = first.isoformat()
                entry["last_rate"] = last.isoformat()

                gap, gap_from = 0, None
                for (a, _), (b, _) in zip(rows, rows[1:]):
                    if (b - a).days > gap:
                        gap, gap_from = (b - a).days, a
                entry["largest_gap_days"] = gap
                entry["largest_gap_from"] = gap_from.isoformat() if gap_from else None

                if first_tx.get(code) and first_tx[code] < first:
                    uncovered = db.query(func.count(Transaction.id)).filter(
                        Transaction.currency == code,
                        Transaction.date < datetime.combine(first, datetime.min.time()),
                    ).scalar() or 0
                    entry["uncovered_transactions"] = uncovered
                    if uncovered:
                        entry["issues"].append(
                            f"{uncovered} transaction{'s' if uncovered != 1 else ''} dated before "
                            f"the first rate ({first.isoformat()}).")
                if gap > RATE_LOOKBACK_DAYS:
                    entry["issues"].append(
                        f"A {gap}-day hole in the history from {gap_from.isoformat()}.")
                if (today - last).days > STALE_AFTER_DAYS:
                    entry["issues"].append(f"Latest rate is from {last.isoformat()}.")

            entry["status"] = "ok" if not entry["issues"] else (
                "missing" if not rows else "incomplete")

        own = latest(code)
        if own is not None and base_rate:
            entry["per_base"] = own / base_rate
        currencies.append(entry)

    last_update = db.query(func.max(ExchangeRate.date)).scalar()
    return {
        "base_currency": base,
        "last_update": _d(last_update).isoformat() if last_update else None,
        "currencies": currencies,
    }


def _rate_on_or_before(db: Session, currency: str, on: date) -> Tuple[Optional[float], Optional[date]]:
    """The rate the ledger would use for ``currency`` on ``on``, and its own date."""
    if currency == 'GBP':
        return 1.0, on
    row = db.query(ExchangeRate.rate, ExchangeRate.date).filter(
        ExchangeRate.currency == currency,
        ExchangeRate.date <= datetime.combine(on, datetime.max.time()),
    ).order_by(ExchangeRate.date.desc()).first()
    if not row:
        return None, None
    return row[0], _d(row[1])


def convert(db: Session, amount: float, from_currency: str, to_currency: str, on: date) -> dict:
    """
    Convert ``amount`` the way the ledger would on ``on``: through sterling, with
    the latest rate published on or before that day for each side. Says which
    day each rate actually comes from, since on a weekend or holiday it is not
    the day asked for.
    """
    r_from, d_from = _rate_on_or_before(db, from_currency, on)
    r_to, d_to = _rate_on_or_before(db, to_currency, on)
    missing = [c for c, r in ((from_currency, r_from), (to_currency, r_to)) if r is None]
    if missing:
        return {"ok": False, "missing": missing}

    rate = r_to / r_from
    rate_dates = [d for c, d in ((from_currency, d_from), (to_currency, d_to)) if c != 'GBP']
    rate_date = min(rate_dates) if rate_dates else on
    return {
        "ok": True,
        "amount": amount,
        "from": from_currency,
        "to": to_currency,
        "date": on.isoformat(),
        "rate": rate,
        "result": amount * rate,
        "rate_date": rate_date.isoformat(),
        "too_old": (on - rate_date).days > RATE_LOOKBACK_DAYS,
    }


# =============================================================================
# DATA HEALTH
# =============================================================================

def _tx_items(db: Session, query) -> List[dict]:
    """Example rows for a check, newest first, with the names a person reads."""
    accounts = {a.id: a.name for a in db.query(Account.id, Account.name)}
    payees = {}
    rows = query.order_by(Transaction.date.desc(), Transaction.id.desc()).limit(SAMPLE_SIZE).all()
    payee_ids = {t.payee_id for t in rows if t.payee_id}
    if payee_ids:
        payees = dict(db.query(Payee.id, Payee.name).filter(Payee.id.in_(payee_ids)).all())
    return [{
        "id": t.id,
        "date": _d(t.date).isoformat() if t.date else None,
        "account": accounts.get(t.account_id),
        "amount": t.amount,
        "currency": t.currency,
        "payee": payees.get(t.payee_id),
        "note": t.note,
    } for t in rows]


def _check(id_, title, severity, count, description, items=None, fix=None) -> dict:
    return {"id": id_, "title": title, "severity": severity, "count": count,
            "description": description, "items": items or [], "fix": fix if count else None}


def _missing_link_filter(column, table_id):
    """Rows whose ``column`` names an id that ``table_id``'s table does not have."""
    return and_(column.isnot(None), ~column.in_(select(table_id)))


def unused_ids(db: Session, kind: str) -> List[int]:
    """Payees, locations or projects nothing points at, and so safe to delete."""
    if kind == "payees":
        used = {pid for (pid,) in db.query(Transaction.payee_id).filter(Transaction.payee_id.isnot(None)).distinct()}
        used |= {pid for (pid,) in db.query(BudgetItem.payee_id).filter(BudgetItem.payee_id.isnot(None))}
        used |= {pid for (pid,) in db.query(RecurringExpense.payee_id).filter(RecurringExpense.payee_id.isnot(None))}
        used |= {pid for (pid,) in db.query(Loan.lender_payee_id).filter(Loan.lender_payee_id.isnot(None))}
        # A payee an import rule fills in is waiting for its first statement.
        from backend import rules_store
        ruled = set(rules_store.get_rules().values())
        return [pid for pid, name in db.query(Payee.id, Payee.name).order_by(Payee.name)
                if pid not in used and name not in ruled]
    if kind == "locations":
        used = {i for (i,) in db.query(Transaction.location_id).filter(Transaction.location_id.isnot(None)).distinct()}
        return [i for (i,) in db.query(Location.id).order_by(Location.name) if i not in used]
    if kind == "projects":
        used = {i for (i,) in db.query(Transaction.project_id).filter(Transaction.project_id.isnot(None)).distinct()}
        return [i for (i,) in db.query(Project.id).order_by(Project.name) if i not in used]
    raise ValueError(f"Unknown kind: {kind}")


def health_report(db: Session) -> List[dict]:
    """Every check, in the order the page shows them: what breaks figures first."""
    checks = []
    T = Transaction

    # ── Rows the app cannot read at all ─────────────────────────────────────
    corrupt = db.query(T).filter(or_(T.amount.is_(None), T.date.is_(None)))
    n = corrupt.count()
    checks.append(_check(
        "corrupt", "Unreadable transactions", "error", n,
        "Transactions with no amount or no date. Nothing can show or count them, "
        "and they can break the pages that try.",
        items=[{"id": t.id, "account": None, "amount": t.amount, "currency": t.currency,
                "date": _d(t.date).isoformat() if t.date else None, "payee": None, "note": t.note}
               for t in corrupt.limit(SAMPLE_SIZE).all()],
        fix={"action": "clean_corrupt", "label": "Delete them", "danger": True}))

    # ── Transactions that belong to no account ──────────────────────────────
    no_account = db.query(T).filter(or_(T.account_id.is_(None),
                                        ~T.account_id.in_(select(Account.id))))
    n = no_account.count()
    checks.append(_check(
        "no_account", "Transactions without an account", "error", n,
        "They count towards no account's balance. Find them in a backup or "
        "re-enter them; they are not removed automatically.",
        items=_tx_items(db, no_account)))

    # ── Links to categories, payees, places or projects that are gone ──────
    broken = db.query(T).filter(or_(
        _missing_link_filter(T.category_id, Category.id),
        _missing_link_filter(T.payee_id, Payee.id),
        _missing_link_filter(T.location_id, Location.id),
        _missing_link_filter(T.project_id, Project.id),
    ))
    n = broken.count()
    checks.append(_check(
        "broken_links", "Links to deleted items", "error", n,
        "Transactions pointing at a category, payee, location or project that no "
        "longer exists. Clearing the link leaves the transaction itself untouched.",
        items=_tx_items(db, broken),
        fix={"action": "clear_broken_links", "label": "Clear the links"}))

    # ── Balances that disagree with the transactions ────────────────────────
    sums = dict(db.query(T.account_id, func.sum(T.amount)).group_by(T.account_id).all())
    last_after = {}
    for account_id, after in db.query(T.account_id, T.account_balance_after) \
            .order_by(T.account_id, T.date, T.id).all():
        last_after[account_id] = after
    unset = db.query(func.count(T.id)).filter(T.account_balance_after.is_(None)).scalar() or 0
    off = []
    for a in db.query(Account).order_by(Account.name).all():
        expected = round((a.initial_balance or 0.0) + (sums.get(a.id) or 0.0), 2)
        stored = round(a.current_balance or 0.0, 2)
        running = last_after.get(a.id)
        if abs(expected - stored) > 0.005 or (running is not None and abs(round(running, 2) - expected) > 0.005):
            off.append({"account": a.name, "currency": a.currency,
                        "expected": expected, "stored": stored})
    n = len(off) + (1 if unset else 0)
    description = ("Account balances that no longer match their opening balance plus "
                   "their transactions. Recalculating rebuilds every running balance.")
    if unset:
        description += f" {unset} transaction{'s have' if unset != 1 else ' has'} no running balance yet."
    checks.append(_check(
        "balances", "Balances out of step", "error", n, description,
        items=[{"balance": o} for o in off],
        fix={"action": "recalculate_balances", "label": "Recalculate balances"}))

    # ── Transfers missing a leg ─────────────────────────────────────────────
    groups = db.query(T.transfer_group_id, func.count(T.id), func.sum(T.amount)) \
        .filter(T.transfer_group_id.isnot(None)).group_by(T.transfer_group_id).all()
    bad_groups = [g for g, count, _ in groups if count != 2]
    # Two legs going the same way are not a transfer either.
    pairs = [g for g, count, _ in groups if count == 2]
    if pairs:
        same_sign = db.query(T.transfer_group_id).filter(T.transfer_group_id.in_(pairs)) \
            .group_by(T.transfer_group_id) \
            .having(func.min(T.amount) * func.max(T.amount) > 0).all()
        bad_groups += [g for (g,) in same_sign]
    n = len(bad_groups)
    checks.append(_check(
        "broken_transfers", "Incomplete transfers", "warning", n,
        "Transfers that are missing their other half, or whose two halves go the "
        "same way. Open the account in Transactions and fix or re-enter them.",
        items=_tx_items(db, db.query(T).filter(T.transfer_group_id.in_(bad_groups))) if bad_groups else []))

    # ── Spending and income with no category ────────────────────────────────
    uncategorised = db.query(T).filter(T.category_id.is_(None), T.transfer_group_id.is_(None),
                                       T.amount.isnot(None), T.amount != 0)
    n = uncategorised.count()
    checks.append(_check(
        "uncategorised", "Uncategorised transactions", "warning", n,
        "They are left out of every category report and of the budget.",
        items=_tx_items(db, uncategorised)))

    # ── Names nothing uses ──────────────────────────────────────────────────
    for kind, model, title in (("payees", Payee, "Unused payees"),
                               ("locations", Location, "Unused locations"),
                               ("projects", Project, "Unused projects")):
        ids = unused_ids(db, kind)
        names = [name for (name,) in db.query(model.name).filter(model.id.in_(ids[:SAMPLE_SIZE])).order_by(model.name)] if ids else []
        checks.append(_check(
            f"unused_{kind}", title, "info", len(ids),
            f"No transaction{', budget item, loan or import rule' if kind == 'payees' else ''} uses them. "
            "They only clutter the pickers.",
            items=[{"name": n} for n in names],
            fix={"action": f"delete_unused_{kind}", "label": "Delete them"}))

    return checks


def clear_broken_links(db: Session) -> int:
    """Set to NULL every link to a category, payee, location or project that is gone."""
    T = Transaction
    changed = 0
    for column, table_id in ((T.category_id, Category.id), (T.payee_id, Payee.id),
                             (T.location_id, Location.id), (T.project_id, Project.id)):
        changed += db.query(T).filter(_missing_link_filter(column, table_id)) \
            .update({column: None}, synchronize_session=False)
    return changed


def delete_unused(db: Session, kind: str) -> int:
    """Delete the payees, locations or projects ``unused_ids`` names."""
    model = {"payees": Payee, "locations": Location, "projects": Project}[kind]
    ids = unused_ids(db, kind)
    if not ids:
        return 0
    if kind == "locations":
        db.query(Payee).filter(Payee.most_common_location_id.in_(ids)) \
            .update({Payee.most_common_location_id: None}, synchronize_session=False)
    elif kind == "projects":
        db.query(Payee).filter(Payee.most_common_project_id.in_(ids)) \
            .update({Payee.most_common_project_id: None}, synchronize_session=False)
    return db.query(model).filter(model.id.in_(ids)).delete(synchronize_session=False)
