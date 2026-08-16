"""
Budgeting engine: turns budget definitions into months, and months into an
auditable picture of budgeted-vs-actual.

Two ideas carry the whole design:

**Months are materialised, not recomputed.** A ``BudgetItem`` is a template; the
month the user looks at is a set of ``BudgetMonthLine`` rows written to the
database. Past months are frozen, so raising the rent changes this month and the
ones ahead while March keeps saying what March said.

**Anything longer than a month is prorated.** A £600 bill every six months is
budgeted as £100 every month — a sinking fund — rather than as a £600 spike. The
money set aside for those funds is tracked per savings account instead of per
expense, because that is how people actually move it: one lump sum, not one
transfer per bill.
"""
from __future__ import annotations

import json
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_
from sqlalchemy.orm import Session

from backend.helpers import get_base_currency, get_latest_rates, get_rates_bulk
from backend.models import (
    Account, BudgetItem, BudgetMonthLine, Category, CategoryBucket, Loan, Location,
    Payee, Transaction,
)

# Kakeibo buckets, in the order they occupy the fixed slots of a calendar cell.
BUCKETS = ("essentials", "indulgences", "culture", "unexpected")
UNMAPPED = "unmapped"
KINDS = ("fixed", "income", "planned")
INTERVAL_UNITS = ("once", "day", "week", "month", "year")

# Mean length of a month — converts day/week intervals into a monthly period.
DAYS_PER_MONTH = 30.436875
# How far an actual charge may drift from its budgeted amount and still count as it.
AMOUNT_TOLERANCE = 0.2


# =============================================================================
# CALENDAR ARITHMETIC
# =============================================================================

def current_ym() -> str:
    return date.today().strftime("%Y-%m")


def parse_ym(ym: str) -> Tuple[int, int]:
    """Split "2026-07" into (2026, 7). Raises ValueError on anything else."""
    year, month = map(int, ym.split("-"))
    if not 1 <= month <= 12:
        raise ValueError("month out of range")
    return year, month


def month_bounds(ym: str) -> Tuple[date, date]:
    """First and last day of the month."""
    year, month = parse_ym(ym)
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def ym_of(d: date) -> str:
    return d.strftime("%Y-%m")


def shift_ym(ym: str, months: int) -> str:
    """Month `months` away from `ym` (negative goes back)."""
    year, month = parse_ym(ym)
    total = year * 12 + (month - 1) + months
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def as_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    return value


def period_months(interval_count: Optional[int], interval_unit: Optional[str]) -> Optional[float]:
    """
    Length of one cycle expressed in months. None for one-off items.
    """
    if interval_unit == "once":
        return None
    n = max(1, int(interval_count or 1))
    if interval_unit == "day":
        return n / DAYS_PER_MONTH
    if interval_unit == "week":
        return n * 7 / DAYS_PER_MONTH
    if interval_unit == "year":
        return float(n * 12)
    return float(n)  # months, and the fallback


def month_index(d: date) -> int:
    """Months since year zero — lets two dates be compared by month alone."""
    return d.year * 12 + (d.month - 1)


def working_day(year: int, month: int, ordinal: Optional[int], from_end: bool = False) -> int:
    """
    Day of the month of the nth working day, counting from the start or the end.

    Working means Monday to Friday: the app has no holiday calendar, so a bank
    holiday can still push the real payment by a day. That only shifts where the
    marker sits on the calendar — whether a bill counts as paid is decided by the
    matching transaction anywhere in the month, not by the date.
    """
    days = [d for d in range(1, monthrange(year, month)[1] + 1)
            if date(year, month, d).weekday() < 5]
    if not days:
        return 1
    n = max(1, int(ordinal or 1))
    index = len(days) - n if from_end else n - 1
    return days[max(0, min(index, len(days) - 1))]


def day_of_month_for(item, year: int, month: int, fallback: int) -> int:
    """The day `item` lands on in the given month, honouring its day rule."""
    rule = getattr(item, "day_rule", None) or "exact"
    if rule == "working_from_start":
        return working_day(year, month, getattr(item, "day_ordinal", None), from_end=False)
    if rule == "working_from_end":
        return working_day(year, month, getattr(item, "day_ordinal", None), from_end=True)
    return min(fallback, monthrange(year, month)[1])


def occurrences_in_month(item, start: date, end: date) -> List[date]:
    """
    Dates on which an item lands inside [start, end]. Only meaningful for cycles
    of a month or less — longer ones are prorated instead of enumerated, so a
    fortnightly expense correctly falls two or three times depending on the month.
    """
    first = as_date(item.first_date)
    if not first or first > end:
        return []
    n = max(1, int(item.interval_count or 1))

    if item.interval_unit in ("day", "week"):
        step = n * (7 if item.interval_unit == "week" else 1)
        behind = (start - first).days
        skipped = max(0, -(-behind // step))  # ceil division, clamped at zero
        cursor = first + timedelta(days=skipped * step)
        out = []
        while cursor <= end:
            if cursor >= start:
                out.append(cursor)
            cursor += timedelta(days=step)
        return out

    if item.interval_unit == "month" and n == 1:
        # With a day rule the day of `first_date` no longer means anything — only
        # the month it starts in does.
        if (getattr(item, "day_rule", None) or "exact") != "exact":
            if month_index(start) < month_index(first):
                return []
            return [date(start.year, start.month, day_of_month_for(item, start.year, start.month, first.day))]
        landing = date(start.year, start.month, day_of_month_for(item, start.year, start.month, first.day))
        return [landing] if landing >= first else []

    return []


def charge_days_in_month(item, start: date, end: date) -> List[int]:
    """
    Days a prorated item is actually charged on this month, if any. Every cycle
    counts, not just the first — a six-monthly bill marks the calendar each time
    it comes round.
    """
    first = as_date(item.first_date)
    if not first:
        return []
    n = max(1, int(item.interval_count or 1))

    if item.interval_unit in ("month", "year"):
        step = n * (12 if item.interval_unit == "year" else 1)
        gap = month_index(start) - month_index(first)
        if gap < 0 or gap % step:
            return []
        return [day_of_month_for(item, start.year, start.month, first.day)]

    # Day/week cycles longer than a month drift, so walk them forward.
    step_days = n * (7 if item.interval_unit == "week" else 1)
    cursor = first
    if cursor < start:
        behind = (start - cursor).days
        cursor += timedelta(days=(-(-behind // step_days)) * step_days)
    return [cursor.day] if start <= cursor <= end else []


def line_values_for_month(item: BudgetItem, ym: str) -> Optional[Dict]:
    """
    What `item` contributes to `ym`, or None if it doesn't apply that month.
    """
    if item.starts_ym and ym < item.starts_ym:
        return None
    if item.ends_ym and ym > item.ends_ym:
        return None

    start, end = month_bounds(ym)
    first = as_date(item.first_date)
    amount = float(item.amount or 0)
    cycle = period_months(item.interval_count, item.interval_unit)

    # One-off: only the month it falls in.
    if cycle is None:
        if not first or not (start <= first <= end):
            return None
        return {"amount": amount, "full_amount": amount, "occurrences": 1,
                "is_prorated": 0, "period_months": None, "due_days": [first.day]}

    # Longer than a month: a monthly share accrues from the item's first month,
    # even while the first real charge is still in the future.
    if cycle > 1 + 1e-9:
        return {"amount": amount / cycle, "full_amount": amount, "occurrences": 0,
                "is_prorated": 1, "period_months": cycle,
                "due_days": charge_days_in_month(item, start, end)}

    occurrences = occurrences_in_month(item, start, end)
    if not occurrences:
        return None
    return {"amount": amount * len(occurrences), "full_amount": amount,
            "occurrences": len(occurrences), "is_prorated": 0, "period_months": cycle,
            "due_days": [d.day for d in occurrences]}


# =============================================================================
# MATERIALISATION
# =============================================================================

def _items_for_month(db: Session, ym: str) -> List[BudgetItem]:
    items = db.query(BudgetItem).filter(
        BudgetItem.is_active == 1,
        BudgetItem.starts_ym <= ym,
    ).all()
    return [i for i in items if not i.ends_ym or i.ends_ym >= ym]


def _write_values(line: BudgetMonthLine, item: BudgetItem, vals: Dict) -> None:
    """Copy a template and its computed month values onto a line, preserving status."""
    line.kind = item.kind
    line.name = item.name
    line.amount = vals["amount"]
    line.full_amount = vals["full_amount"]
    line.occurrences = vals["occurrences"]
    line.is_prorated = vals["is_prorated"]
    line.period_months = vals["period_months"]
    line.is_estimated = item.is_estimated or 0
    line.currency = item.currency or "GBP"
    line.payee_id = item.payee_id
    line.set_aside_account_id = item.set_aside_account_id
    line.account_ids = json.dumps([a.account_id for a in item.accounts])
    line.category_ids = json.dumps([c.category_id for c in item.categories])
    line.due_days = json.dumps(vals["due_days"])


def _build_line(item: BudgetItem, ym: str, vals: Dict) -> BudgetMonthLine:
    line = BudgetMonthLine(year_month=ym, item_id=item.id, source="template")
    _write_values(line, item, vals)
    return line


def generate_month(db: Session, ym: str, skip_existing: bool = True) -> None:
    """Write the lines `ym` is missing. Never touches lines that already exist."""
    existing = set()
    if skip_existing:
        existing = {
            row[0] for row in
            db.query(BudgetMonthLine.item_id).filter(BudgetMonthLine.year_month == ym).all()
        }
    for item in _items_for_month(db, ym):
        if item.id in existing:
            continue
        vals = line_values_for_month(item, ym)
        if vals:
            db.add(_build_line(item, ym, vals))


def ensure_month(db: Session, ym: str) -> None:
    """
    Make sure `ym` has its lines, under the rule that keeps history honest:

    - future months are projections, rebuilt from the templates every time;
    - the current month is filled in but never overwritten;
    - past months are frozen — generated only if they were never seen at all.
    """
    cur = current_ym()

    if ym > cur:
        db.query(BudgetMonthLine).filter(
            BudgetMonthLine.year_month == ym,
            BudgetMonthLine.source == "template",
        ).delete(synchronize_session=False)
        generate_month(db, ym, skip_existing=False)
    elif ym == cur:
        generate_month(db, ym, skip_existing=True)
    else:
        seen = db.query(BudgetMonthLine).filter(BudgetMonthLine.year_month == ym).count()
        if seen == 0:
            generate_month(db, ym, skip_existing=False)
        db.query(BudgetMonthLine).filter(
            BudgetMonthLine.year_month == ym,
            BudgetMonthLine.is_frozen == 0,
        ).update({"is_frozen": 1}, synchronize_session=False)

    db.commit()


# =============================================================================
# VERSIONING
#
# An edit is always made from a month. "Rent goes up in October" must leave
# July to September alone, so the definition is not overwritten: it is cut in
# two at October, and each half owns its own stretch of months. Both halves
# share a ``series_id``, which is what makes them the same rent rather than two
# unrelated expenses.
# =============================================================================

def series_of(item: BudgetItem) -> int:
    """The series an item belongs to. Rows written before versioning are their own."""
    return item.series_id or item.id


def series_items(db: Session, item: BudgetItem) -> List[BudgetItem]:
    """Every version of `item`, oldest first."""
    key = series_of(item)
    return db.query(BudgetItem).filter(
        (BudgetItem.series_id == key) | (BudgetItem.id == key)
    ).order_by(BudgetItem.starts_ym).all()


def _clone_item(db: Session, item: BudgetItem, starts_ym: str) -> BudgetItem:
    """A copy of `item` covering months from `starts_ym`, in the same series."""
    from backend.models import BudgetItemAccount, BudgetItemCategory

    clone = BudgetItem(
        kind=item.kind, name=item.name, amount=item.amount, currency=item.currency,
        is_estimated=item.is_estimated, first_date=item.first_date,
        interval_count=item.interval_count, interval_unit=item.interval_unit,
        day_rule=item.day_rule, day_ordinal=item.day_ordinal,
        payee_id=item.payee_id, set_aside_account_id=item.set_aside_account_id,
        starts_ym=starts_ym, ends_ym=item.ends_ym, is_active=item.is_active,
        series_id=series_of(item),
    )
    db.add(clone)
    db.flush()  # need the id before the link rows
    for link in item.accounts:
        db.add(BudgetItemAccount(item_id=clone.id, account_id=link.account_id))
    for link in item.categories:
        db.add(BudgetItemCategory(item_id=clone.id, category_id=link.category_id))
    db.flush()
    db.refresh(clone)
    return clone


def split_item(db: Session, item: BudgetItem, ym: str) -> BudgetItem:
    """
    Make sure a version boundary exists at `ym`, and return the version that
    owns `ym` onwards. If the item already begins at or after `ym` there is no
    earlier history to protect and the item itself is returned unchanged.
    """
    if item.starts_ym >= ym:
        return item
    clone = _clone_item(db, item, ym)
    item.ends_ym = shift_ym(ym, -1)
    db.flush()
    return clone


def _materialised_months(db: Session, from_ym: str) -> List[str]:
    """
    Months at or after `from_ym` that the app has already written lines for,
    always including the month the edit starts in and the current month — those
    two must end up consistent even if nothing had been generated yet.
    """
    rows = db.query(BudgetMonthLine.year_month).filter(
        BudgetMonthLine.year_month >= from_ym
    ).distinct().all()
    months = {row[0] for row in rows} | {from_ym}
    cur = current_ym()
    if cur >= from_ym:
        months.add(cur)
    return sorted(months)


def rematerialise_series(db: Session, item: BudgetItem, from_ym: str) -> None:
    """
    Rewrite every line of `item`'s series from `from_ym` on, so each month picks
    up whichever version now covers it. Manual paid/pending overrides survive,
    since they record what happened rather than what was planned.
    """
    versions = series_items(db, item)
    ids = [v.id for v in versions]
    if not ids:
        return

    old = db.query(BudgetMonthLine).filter(
        BudgetMonthLine.item_id.in_(ids),
        BudgetMonthLine.year_month >= from_ym,
    ).all()
    overrides = {line.year_month: line.paid_override for line in old}
    # A month someone corrected by hand says what actually happened, so a later
    # change to the definition does not get to overwrite it.
    corrected = {line.year_month for line in old if line.source == "manual"}
    # Work out which months to rewrite before the delete, or a month whose only
    # line was this item's would drop out of the set and never come back.
    months = _materialised_months(db, from_ym)

    # Drop the doomed rows from the session first: a bulk delete leaves them in
    # the identity map, and SQLite hands their primary keys straight back to the
    # replacements we are about to insert.
    for line in old:
        if line.source != "manual":
            db.expunge(line)
    db.query(BudgetMonthLine).filter(
        BudgetMonthLine.item_id.in_(ids),
        BudgetMonthLine.year_month >= from_ym,
        BudgetMonthLine.source != "manual",
    ).delete(synchronize_session=False)
    db.flush()

    cur = current_ym()
    for ym in months:
        if ym in corrected:
            continue
        for version in versions:
            if not version.is_active or version.starts_ym > ym:
                continue
            if version.ends_ym and version.ends_ym < ym:
                continue
            vals = line_values_for_month(version, ym)
            if not vals:
                continue
            line = _build_line(version, ym, vals)
            line.paid_override = overrides.get(ym)
            line.is_frozen = 1 if ym < cur else 0
            db.add(line)
            break  # at most one version covers any given month


def prepare_item_edit(db: Session, item: BudgetItem, ym: str, scope: str) -> BudgetItem:
    """
    Cut the series so that `ym` can be given new values, and return the row the
    new values belong on. With scope "forward" that row runs to the end of the
    series; with "month" it is capped at `ym` and a tail carrying the old values
    picks up again the month after.
    """
    target = split_item(db, item, ym)
    if scope == "month":
        if not target.ends_ym or target.ends_ym > ym:
            split_item(db, target, shift_ym(ym, 1))  # the tail keeps the old values
        target.ends_ym = ym
    db.flush()
    return target


def apply_item_change(db: Session, item: BudgetItem, from_ym: Optional[str] = None) -> None:
    """
    Push a definition change into `from_ym` and every month after it, leaving
    earlier months exactly as they were. Defaults to the current month, which is
    what creating an item wants.
    """
    rematerialise_series(db, item, from_ym or current_ym())
    db.commit()


def retire_item(db: Session, item: BudgetItem, from_ym: Optional[str] = None) -> None:
    """
    Stop an item from `from_ym` on without erasing what it did before: it ends
    the month before, and its lines from there on go away. Later versions of the
    same series go too — the whole thing is being stopped, not just this slice.
    """
    start = from_ym or current_ym()
    for version in series_items(db, item):
        if version.starts_ym >= start:
            version.is_active = 0
        elif not version.ends_ym or version.ends_ym >= start:
            version.ends_ym = shift_ym(start, -1)
    db.flush()
    rematerialise_series(db, item, start)
    db.commit()


def retire_item_for_month(db: Session, item: BudgetItem, ym: str) -> None:
    """Drop an item from a single month, leaving it in place before and after."""
    target = split_item(db, item, ym)
    if not target.ends_ym or target.ends_ym > ym:
        split_item(db, target, shift_ym(ym, 1))  # the tail keeps the old values
    target.is_active = 0
    target.ends_ym = ym
    db.flush()
    rematerialise_series(db, item, ym)
    db.commit()


# =============================================================================
# READ MODEL
# =============================================================================

def _loads(raw) -> List[int]:
    try:
        value = json.loads(raw) if raw else []
        return [int(v) for v in value] if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def _transfer_location_ids(db: Session) -> List[int]:
    return [
        row[0] for row in
        db.query(Location.id).filter(Location.name.in_(["Transfer In", "Transfer Out"])).all()
    ]


def normalise_name(name: Optional[str]) -> str:
    """
    Key a category name for matching. Subcategories point at their parent by
    name, and those strings come from imports, so casing and stray spacing
    should not be what breaks the link.
    """
    return " ".join((name or "").split()).casefold()


def _category_children(db: Session) -> Dict[int, List[int]]:
    """category id -> ids of its subcategories (categories are linked by parent name)."""
    cats = db.query(Category).all()
    by_name: Dict[str, List[int]] = {}
    for c in cats:
        if c.parent:
            by_name.setdefault(normalise_name(c.parent), []).append(c.id)
    return {c.id: by_name.get(normalise_name(c.name), []) for c in cats}


def explicit_buckets(db: Session) -> Dict[int, str]:
    """category id -> the bucket set on that category itself."""
    return {
        row.category_id: row.bucket
        for row in db.query(CategoryBucket).all()
        if row.bucket in BUCKETS
    }


def bucket_map(db: Session) -> Dict[int, str]:
    """
    category id -> the bucket that applies to it.

    A subcategory with no bucket of its own inherits its parent's, so classifying
    a top-level category covers everything beneath it — including subcategories
    added later — while an explicit one on the child still wins.
    """
    own = explicit_buckets(db)
    categories = db.query(Category).all()
    by_name = {normalise_name(c.name): c for c in categories}

    resolved: Dict[int, str] = {}
    for category in categories:
        bucket = own.get(category.id)
        if bucket is None and category.parent:
            parent = by_name.get(normalise_name(category.parent))
            if parent is not None:
                bucket = own.get(parent.id)
        if bucket:
            resolved[category.id] = bucket
    return resolved


def month_snapshot(db: Session, ym: str) -> Dict:
    """
    Everything the budget page needs for one month: the three card lists, the
    headline figures and the day-by-day calendar.
    """
    start, end = month_bounds(ym)
    base_currency = get_base_currency(db)
    ensure_month(db, ym)

    lines = db.query(BudgetMonthLine).filter(BudgetMonthLine.year_month == ym).all()
    transfer_ids = _transfer_location_ids(db)
    latest_rates = get_latest_rates(db)

    def to_base(amount: float, currency: str, rates: Dict[str, float]) -> float:
        rate = rates.get(currency or base_currency, 1.0) or 1.0
        return amount * (rates.get(base_currency, 1.0) / rate)

    # --- transactions of the month -------------------------------------------------
    transactions = db.query(Transaction).filter(and_(
        Transaction.date >= datetime.combine(start, time.min),
        Transaction.date <= datetime.combine(end, time.max),
    )).all()

    currencies = list({t.currency for t in transactions if t.currency} | {base_currency})
    historical = get_rates_bulk(db, currencies, start, end)

    def tx_base(tx: Transaction) -> float:
        rates = historical.get(as_date(tx.date), latest_rates)
        return to_base(tx.amount, tx.currency, rates)

    expenses, incomes, transfers_in = [], [], []
    for tx in transactions:
        is_transfer = bool(transfer_ids) and tx.location_id in transfer_ids
        if is_transfer:
            if tx.amount > 0:
                transfers_in.append(tx)
        elif tx.amount < 0:
            expenses.append(tx)
        elif tx.amount > 0:
            incomes.append(tx)

    # --- fixed expenses ------------------------------------------------------------
    fixed_lines = [l for l in lines if l.kind == "fixed"]
    planned_lines = [l for l in lines if l.kind == "planned"]
    income_lines = [l for l in lines if l.kind == "income"]

    matched_tx_ids: set = set()
    fixed_pairs, target_fixed, committed = [], 0.0, 0.0
    # Bills ticked off by hand that no transaction accounts for. Marking one paid
    # releases what was set aside for it, and without this the money would look
    # saved rather than spent — `remaining` rising by the whole budgeted amount.
    imputed_spent = 0.0

    for line in sorted(fixed_lines, key=lambda l: -(l.amount or 0)):
        budgeted = to_base(line.amount or 0, line.currency, latest_rates)
        target_fixed += budgeted
        paid, matches = _detect_payment(line, expenses, transfers_in, matched_tx_ids)
        if paid and not line.is_prorated:
            if not matches:
                # Ticked by hand with nothing recognised. Either the payment is
                # in the month under a payee the rules missed — in which case it
                # is already in `spent` and must not be counted again — or it was
                # never recorded, and the budgeted amount stands in for it. The
                # amount is the only evidence left, the payee having been what
                # failed to match in the first place.
                matches = _loose_match(line, expenses, matched_tx_ids)
                if not matches:
                    imputed_spent += budgeted
            for tx in matches:
                matched_tx_ids.add(tx.id)
        if not line.is_prorated and not paid:
            committed += budgeted

        fixed_pairs.append((line, {
            "id": line.id,
            "item_id": line.item_id,
            "name": line.name,
            "amount": round(line.amount or 0, 2),
            "converted_amount": round(budgeted, 2),
            "full_amount": round(line.full_amount or 0, 2),
            "currency": line.currency,
            "occurrences": line.occurrences,
            "is_prorated": bool(line.is_prorated),
            "period_months": round(line.period_months, 2) if line.period_months else None,
            "is_estimated": bool(line.is_estimated),
            "due_days": _loads(line.due_days),
            "payee_id": line.payee_id,
            "payee_name": line.payee.name if line.payee else None,
            "set_aside_account_id": line.set_aside_account_id,
            "set_aside_account_name": line.set_aside_account.name if line.set_aside_account else None,
            "paid": bool(paid),
            "paid_override": line.paid_override,
            "is_frozen": bool(line.is_frozen),
        }))

    # Money moved to an account of your own is a different kind of commitment
    # from a bill, and splits into two of its own: putting money by, and paying
    # something off. The test for "not a bill" is the one the engine already
    # makes to decide *how* to spot the payment — a line with somewhere to put
    # the money and nobody to pay is answered by a transfer arriving, not by a
    # charge going out. Prorated lines are excluded: those are sinking funds for
    # future bills, which have a section of their own.
    def _is_transfer_line(line) -> bool:
        return bool(line.set_aside_account_id) and not line.payee_id and not line.is_prorated

    transfer_pairs = [(line, out) for line, out in fixed_pairs if _is_transfer_line(line)]
    debt_ids = _debt_accounts(db, {line.set_aside_account_id for line, _ in transfer_pairs})

    fixed_out = [out for line, out in fixed_pairs if not _is_transfer_line(line)]
    savings_out = [out for line, out in transfer_pairs if line.set_aside_account_id not in debt_ids]
    debt_out = [out for line, out in transfer_pairs if line.set_aside_account_id in debt_ids]
    target_savings = sum(out["converted_amount"] for out in savings_out)
    target_debt = sum(out["converted_amount"] for out in debt_out)
    target_fixed -= target_savings + target_debt

    # --- sinking funds: verified per account, not per expense -----------------------
    funds: Dict[Optional[int], Dict] = {}
    for line, out in fixed_pairs:
        if not line.is_prorated:
            continue
        fund = funds.setdefault(line.set_aside_account_id, {
            "account_id": line.set_aside_account_id,
            "account_name": line.set_aside_account.name if line.set_aside_account else None,
            "expected": 0.0, "actual": 0.0, "items": [],
        })
        fund["expected"] += out["converted_amount"]
        fund["items"].append(out["name"])

    for tx in transfers_in:
        if tx.id in matched_tx_ids or tx.account_id not in funds:
            continue
        funds[tx.account_id]["actual"] += tx_base(tx)
        matched_tx_ids.add(tx.id)

    sinking_funds, set_aside_total = [], 0.0
    for fund in funds.values():
        shortfall = max(0.0, fund["expected"] - fund["actual"])
        committed += shortfall
        set_aside_total += min(fund["actual"], fund["expected"])
        sinking_funds.append({
            "account_id": fund["account_id"],
            "account_name": fund["account_name"],
            "expected": round(fund["expected"], 2),
            "actual": round(fund["actual"], 2),
            "shortfall": round(shortfall, 2),
            "items": fund["items"],
        })
    sinking_funds.sort(key=lambda f: -f["expected"])

    # --- planned expenses ----------------------------------------------------------
    #
    # A purchase belongs to one budget line, never several. It used to be counted
    # by every line whose filters it fitted — a grocery shop on the everyday card
    # answering for both "Groceries" and "everything on that card" — and since
    # each line then released its own share of `committed` while the spending was
    # only counted once, the headline figures drifted apart: money spent made
    # `remaining` go *up*. Lines are offered each transaction in order of how
    # specific they are, and the first to claim it keeps it.
    children = _category_children(db)
    planned_out, target_planned = [], 0.0

    resolved = []
    for line in planned_lines:
        account_ids = set(_loads(line.account_ids))
        category_ids = set()
        for cid in _loads(line.category_ids):
            category_ids.add(cid)
            category_ids.update(children.get(cid, []))
        resolved.append((line, account_ids, category_ids))

    def _specificity(entry) -> int:
        """Narrowest claim first; a line naming only accounts is the catch-all."""
        _, accounts, categories = entry
        if accounts and categories:
            return 0
        if categories:
            return 1
        return 2

    resolved.sort(key=lambda e: (_specificity(e), -(e[0].amount or 0)))

    # Seeded from the bills, so an expense already answered for by a fixed line
    # is not budgeted for twice. Kept separate from `matched_tx_ids` because that
    # set means "this is a bill" to the calendar, which a grocery shop is not.
    claimed_by_planned = set(matched_tx_ids)

    for line, account_ids, category_ids in resolved:
        budgeted = to_base(line.amount or 0, line.currency, latest_rates)
        target_planned += budgeted

        # With nothing to match on there is no way to follow the spending, so the
        # line stays at zero rather than claiming every expense of the month.
        spent_here, tx_ids = 0.0, []
        if account_ids or category_ids:
            for tx in expenses:
                if tx.id in claimed_by_planned:
                    continue  # already answered for by a narrower line, or a bill
                if account_ids and tx.account_id not in account_ids:
                    continue
                if category_ids and tx.category_id not in category_ids:
                    continue
                spent_here += abs(tx_base(tx))
                tx_ids.append(tx.id)
                claimed_by_planned.add(tx.id)

        committed += max(0.0, budgeted - spent_here)
        planned_out.append({
            "id": line.id,
            "item_id": line.item_id,
            "name": line.name,
            "amount": round(line.amount or 0, 2),
            "converted_amount": round(budgeted, 2),
            "currency": line.currency,
            "spent": round(spent_here, 2),
            "remaining": round(budgeted - spent_here, 2),
            "percentage": round(spent_here / budgeted * 100, 1) if budgeted > 0 else 0,
            "is_estimated": bool(line.is_estimated),
            "account_ids": sorted(account_ids),
            "category_ids": _loads(line.category_ids),
            "transaction_count": len(tx_ids),
            "is_frozen": bool(line.is_frozen),
        })

    # Claiming order is about specificity; the page still reads biggest first.
    planned_out.sort(key=lambda p: -p["converted_amount"])

    # --- expected income -----------------------------------------------------------
    income_out, income_expected = [], 0.0
    for line in sorted(income_lines, key=lambda l: -(l.amount or 0)):
        converted = to_base(line.amount or 0, line.currency, latest_rates)
        income_expected += converted
        income_out.append({
            "id": line.id,
            "item_id": line.item_id,
            "name": line.name,
            "amount": round(line.amount or 0, 2),
            "converted_amount": round(converted, 2),
            "currency": line.currency,
            "is_estimated": bool(line.is_estimated),
            "is_frozen": bool(line.is_frozen),
        })

    # --- headline figures ----------------------------------------------------------
    spent = sum(abs(tx_base(tx)) for tx in expenses)
    # Bills settled by transfer leave the accounts too, so they belong in "spent".
    spent += sum(tx_base(tx) for tx in transfers_in if tx.id in matched_tx_ids)
    # Bills ticked off by hand with no transaction to show for them: the money
    # left even though nothing here records it leaving.
    spent += imputed_spent
    income_actual = sum(tx_base(tx) for tx in incomes)

    target = target_fixed + target_savings + target_debt + target_planned

    # Two different questions, kept apart.
    #
    # `remaining` asks whether the plan is holding: it is the slice of the target
    # neither spent nor still owed, so it sits at zero all month and only moves
    # when a bill comes in under its budget or money goes somewhere unbudgeted.
    # That is what the progress bar divides up, and it is why it is nearly always
    # zero — which made "Available" a promise the figure never kept.
    #
    # `available` asks what there is left to spend, which is the question the word
    # actually poses: what is coming in, less what has gone and what is still
    # owed. Budget nothing as income and there is no such thing to answer with, so
    # it falls back to the target and behaves exactly as `remaining` does.
    remaining = target - spent - committed
    headroom_base = income_expected if income_expected > 0 else target
    available = headroom_base - spent - committed

    today = date.today()
    if start <= today <= end:
        days_remaining = (end - today).days + 1
    elif today < start:
        days_remaining = (end - start).days + 1
    else:
        days_remaining = 0

    days_in_month = (end - start).days + 1
    daily_target = target_planned / days_in_month if days_in_month else 0

    return {
        "year_month": ym,
        "base_currency": base_currency,
        "is_past": ym < current_ym(),
        "target": round(target, 2),
        "target_fixed": round(target_fixed, 2),
        "target_savings": round(target_savings, 2),
        "target_debt": round(target_debt, 2),
        "target_planned": round(target_planned, 2),
        "spent": round(spent, 2),
        # How much of "spent" stands in for bills ticked off by hand — money the
        # app has no transaction for, so the figure can be explained rather than
        # just being larger than the transactions add up to.
        "imputed_spent": round(imputed_spent, 2),
        "committed": round(committed, 2),
        # How the target divides up — what the progress bar draws.
        "remaining": round(remaining, 2),
        # What there is left to spend. The headline figure, and what "safe to
        # spend a day" is worth dividing.
        "available": round(available, 2),
        "percentage": round(spent / target * 100, 1) if target > 0 else 0,
        "days_remaining": days_remaining,
        "daily_available": round(available / days_remaining, 2) if days_remaining > 0 else 0,
        "daily_target": round(daily_target, 2),
        "set_aside_total": round(set_aside_total, 2),
        "income": {
            "actual": round(income_actual, 2),
            "expected": round(income_expected, 2),
        },
        "savings": {
            "actual": round(income_actual - spent, 2),
            "expected": round(income_expected - target, 2),
        },
        "fixed_expenses": fixed_out,
        "savings_goals": savings_out,
        "debt_payments": debt_out,
        "planned_expenses": planned_out,
        "income_items": income_out,
        "sinking_funds": sinking_funds,
        # The calendar marks what falls due each day, and a transfer to savings
        # falls due like anything else — so it sees both lists.
        "calendar": _calendar(db, ym, expenses, fixed_out + savings_out + debt_out, matched_tx_ids, tx_base),
        # Kept for the dashboard KPI in index.html, which reads these two keys.
        "budget": {"year_month": ym, "amount": round(target, 2), "currency": base_currency},
    }


def _detect_payment(line: BudgetMonthLine, expenses: List[Transaction],
                    transfers_in: List[Transaction],
                    already_matched: set) -> Tuple[Optional[bool], List[Transaction]]:
    """
    Whether a fixed line has been settled, and the transactions that settle it.
    A manual override always wins; prorated lines are answered by their fund.
    """
    if line.paid_override is not None:
        return bool(line.paid_override), []
    if line.is_prorated:
        return None, []

    expected = abs(line.full_amount or line.amount or 0)
    if expected <= 0:
        return False, []
    low, high = expected * (1 - AMOUNT_TOLERANCE), expected * (1 + AMOUNT_TOLERANCE)
    needed = max(1, line.occurrences or 1)

    # A transfer to a debt or savings account: look for money arriving there.
    if line.set_aside_account_id and not line.payee_id:
        matches = [t for t in transfers_in
                   if t.account_id == line.set_aside_account_id
                   and t.id not in already_matched
                   and low <= abs(t.amount) <= high]
    elif line.payee_id:
        matches = [t for t in expenses
                   if t.payee_id == line.payee_id
                   and t.id not in already_matched
                   and low <= abs(t.amount) <= high]
    else:
        return False, []  # nothing to match on

    matches = matches[:needed]
    return len(matches) >= needed, matches


DEBT_ACCOUNT_TYPES = {"LIABILITY", "CREDIT_CARD"}


def _debt_accounts(db: Session, account_ids: set) -> set:
    """
    Which of these accounts are debts rather than somewhere to put money by.

    Paying £200 into savings and £200 off a mortgage are the same gesture and the
    same arithmetic, but not the same intention, so they are told apart here.
    Two signals, both declared: agreed loan terms settle it outright, and failing
    those, an account typed as a liability or a card says so plainly.

    There used to be a third — an account whose very first movement was negative
    was read as one that opened by owing. It was dropped when the type became
    something you choose. On an imported database the first movement is only
    wherever the history happens to start, so an ordinary current account could
    be taken for a debt and have its budget lines moved out of Savings on the
    strength of a guess. What an account is, is now yours to say.

    Only the accounts a budget line actually points at are looked at, so this
    stays a handful of rows however many accounts exist.
    """
    if not account_ids:
        return set()

    debts = {row[0] for row in
             db.query(Loan.account_id).filter(Loan.account_id.in_(account_ids)).all()}

    for account in db.query(Account).filter(Account.id.in_(account_ids - debts)).all():
        if (account.type or "").strip().upper().replace(" ", "_") in DEBT_ACCOUNT_TYPES:
            debts.add(account.id)

    return debts


def _loose_match(line: BudgetMonthLine, expenses: List[Transaction],
                 already_matched: set) -> List[Transaction]:
    """
    A last look for the transaction behind a bill ticked off by hand.

    Detection can fail on either half of its test, so this drops each in turn.
    The payee goes first and without any amount check at all: naming a payee is
    saying "this is who bills me", so a charge from them is the bill however far
    it lands from the estimate — a water bill budgeted at £100 that arrives at
    £40 is still the water bill. Only with no payee to go on does the amount
    become the evidence.

    Finding nothing is itself the answer: it means the payment was never
    recorded, and the caller stands the budgeted amount in for it rather than
    letting it look like money saved.
    """
    needed = max(1, line.occurrences or 1)

    if line.payee_id:
        matches = [t for t in expenses
                   if t.payee_id == line.payee_id and t.id not in already_matched]
        if matches:
            return matches[:needed]

    expected = abs(line.full_amount or line.amount or 0)
    if expected <= 0:
        return []
    low, high = expected * (1 - AMOUNT_TOLERANCE), expected * (1 + AMOUNT_TOLERANCE)
    matches = [t for t in expenses
               if t.id not in already_matched and low <= abs(t.amount) <= high]
    return matches[:needed]


def _calendar(db: Session, ym: str, expenses: List[Transaction], fixed_out: List[Dict],
              matched_tx_ids: set, tx_base) -> List[Dict]:
    """
    One entry per day: spending split into kakeibo buckets, the bills falling that
    day, and the day-to-day total the pace colour is based on (bills excluded).
    """
    start, end = month_bounds(ym)
    buckets = bucket_map(db)

    days: Dict[str, Dict] = {}
    cursor = start
    while cursor <= end:
        days[cursor.isoformat()] = {
            "date": cursor.isoformat(),
            "buckets": {b: 0.0 for b in BUCKETS},
            "unmapped": 0.0,
            "variable": 0.0,
            "total": 0.0,
            "transactions": [],
            "bills": [],
        }
        cursor += timedelta(days=1)

    for tx in expenses:
        key = as_date(tx.date).isoformat()
        day = days.get(key)
        if day is None:
            continue
        amount = abs(tx_base(tx))
        bucket = buckets.get(tx.category_id, UNMAPPED)
        if bucket == UNMAPPED:
            day["unmapped"] += amount
        else:
            day["buckets"][bucket] += amount
        day["total"] += amount
        # Bills are already set aside, so they stay out of the pace colour.
        if tx.id not in matched_tx_ids:
            day["variable"] += amount
        day["transactions"].append({
            "id": tx.id,
            "bucket": bucket,
            "payee_name": tx.payee.name if tx.payee else None,
            "category_name": tx.category.name if tx.category else None,
            "note": tx.note,
            "amount": round(amount, 2),
            "is_bill": tx.id in matched_tx_ids,
        })

    for fixed in fixed_out:
        for day_number in fixed["due_days"]:
            key = date(start.year, start.month,
                       min(day_number, (end - start).days + 1)).isoformat()
            day = days.get(key)
            if day is not None:
                day["bills"].append({
                    "name": fixed["name"],
                    "amount": fixed["converted_amount"],
                    "paid": fixed["paid"],
                    "is_prorated": fixed["is_prorated"],
                })

    for day in days.values():
        day["buckets"] = {k: round(v, 2) for k, v in day["buckets"].items()}
        day["unmapped"] = round(day["unmapped"], 2)
        day["variable"] = round(day["variable"], 2)
        day["total"] = round(day["total"], 2)

    return list(days.values())


# =============================================================================
# SUGGESTIONS — a hand in filling the cards, never the last word
# =============================================================================

# Gap between charges (days) -> the recurrence it suggests.
_RHYTHMS = [
    (7, 1, "week"), (14, 2, "week"), (30.4, 1, "month"), (60.9, 2, "month"),
    (91.3, 3, "month"), (182.6, 6, "month"), (365.25, 1, "year"),
]


def _infer_rhythm(dates: List[date]) -> Optional[Tuple[int, str]]:
    """The rhythm that best fits the gaps between charges."""
    if len(dates) < 2:
        return None
    ordered = sorted(dates)
    gaps = [(b - a).days for a, b in zip(ordered, ordered[1:]) if (b - a).days > 0]
    if not gaps:
        return None
    gaps.sort()
    median = gaps[len(gaps) // 2]
    best = min(_RHYTHMS, key=lambda r: abs(r[0] - median))
    # A gap nothing like any known rhythm is better left to the user.
    if abs(best[0] - median) > best[0] * 0.35:
        return None
    return best[1], best[2]


def _consistent(amounts: List[float], max_variance: float) -> Optional[float]:
    """Mean amount, if the charges are close enough to each other to call it fixed."""
    mean = sum(amounts) / len(amounts)
    if mean <= 0:
        return None
    if max(abs(a - mean) for a in amounts) / mean > max_variance:
        return None
    return mean


def suggest_candidates(db: Session, kind: str = "fixed", min_occurrences: int = 3,
                       max_variance: float = 0.3, months_to_look_back: int = 12) -> List[Dict]:
    """
    Regularities in the history worth turning into budget items.

    For ``fixed``: charges that keep coming back to the same payee, and transfers
    that keep landing in the same account (loan repayments, savings). For
    ``income``: money that keeps arriving from the same payer. Whatever is already
    budgeted under that kind is left out.
    """
    today = date.today()
    cutoff = today - timedelta(days=months_to_look_back * 31)
    recent_cutoff = today - timedelta(days=75)  # must still be alive
    wants_income = kind == "income"

    taken_payees = {
        row[0] for row in
        db.query(BudgetItem.payee_id).filter(
            BudgetItem.is_active == 1, BudgetItem.kind == kind,
            BudgetItem.payee_id.isnot(None)).all()
    }
    taken_accounts = {
        row[0] for row in
        db.query(BudgetItem.set_aside_account_id).filter(
            BudgetItem.is_active == 1, BudgetItem.kind == kind,
            BudgetItem.set_aside_account_id.isnot(None)).all()
    }

    transfer_ids = _transfer_location_ids(db)
    transactions = db.query(Transaction).filter(
        Transaction.date >= datetime.combine(cutoff, time.min)
    ).all()

    by_payee: Dict[int, List[Transaction]] = {}
    by_account: Dict[int, List[Transaction]] = {}
    for tx in transactions:
        is_transfer = bool(transfer_ids) and tx.location_id in transfer_ids
        if is_transfer:
            # Money moved between your own accounts is never income.
            if tx.amount > 0 and tx.account_id and not wants_income:
                by_account.setdefault(tx.account_id, []).append(tx)
        elif tx.payee_id and ((tx.amount > 0) if wants_income else (tx.amount < 0)):
            by_payee.setdefault(tx.payee_id, []).append(tx)

    candidates = []

    def consider(group: List[Transaction], key_taken: bool) -> Optional[Dict]:
        if key_taken or len(group) < min_occurrences:
            return None
        dates = [as_date(t.date) for t in group]
        if max(dates) < recent_cutoff:
            return None  # stopped happening
        rhythm = _infer_rhythm(dates)
        if not rhythm:
            return None
        mean = _consistent([abs(t.amount) for t in group], max_variance)
        if mean is None:
            return None
        count, unit = rhythm
        return {
            "amount": round(mean, 2),
            "currency": group[0].currency or "GBP",
            "first_date": max(dates).isoformat() + "T00:00:00",
            "interval_count": count,
            "interval_unit": unit,
            "occurrences": len(group),
            "every": f"every {count} {unit}{'s' if count > 1 else ''}",
        }

    for payee_id, group in by_payee.items():
        found = consider(group, payee_id in taken_payees)
        if not found:
            continue
        payee = db.query(Payee).filter(Payee.id == payee_id).first()
        categories: Dict[int, int] = {}
        for tx in group:
            if tx.category_id:
                categories[tx.category_id] = categories.get(tx.category_id, 0) + 1
        category_name = None
        if categories:
            top = max(categories, key=categories.get)
            category = db.query(Category).filter(Category.id == top).first()
            category_name = category.name if category else None
        found.update({
            "payee_id": payee_id,
            "account_id": None,
            "suggested_name": payee.name if payee else "Unknown",
            "detail": " · ".join(filter(None, [
                found["every"],
                f"{found['occurrences']} {'payments' if wants_income else 'charges'}",
                category_name])),
        })
        candidates.append(found)

    for account_id, group in by_account.items():
        found = consider(group, account_id in taken_accounts)
        if not found:
            continue
        account = db.query(Account).filter(Account.id == account_id).first()
        name = account.name if account else "Account"
        found.update({
            "payee_id": None,
            "account_id": account_id,
            "suggested_name": f"Transfer to {name}",
            "detail": " · ".join([
                found["every"], f"{found['occurrences']} transfers", "into " + name]),
        })
        candidates.append(found)

    candidates.sort(key=lambda c: -c["amount"])
    return candidates


def month_history(db: Session, months: int = 12) -> List[Dict]:
    """
    Budgeted-vs-actual for the last `months` months, most recent first. Only
    months that already have lines are reported — the point is to audit what was
    budgeted at the time, not to invent budgets for months nobody planned.
    """
    cur = current_ym()
    wanted = [shift_ym(cur, -i) for i in range(months)]
    known = {
        row[0] for row in
        db.query(BudgetMonthLine.year_month)
        .filter(BudgetMonthLine.year_month.in_(wanted))
        .distinct().all()
    }
    return [month_summary(db, ym) for ym in wanted if ym in known]


def month_summary(db: Session, ym: str) -> Dict:
    """The headline numbers of a month, without the per-day detail."""
    snapshot = month_snapshot(db, ym)
    return {
        "year_month": ym,
        "target": snapshot["target"],
        "spent": snapshot["spent"],
        "committed": snapshot["committed"],
        "remaining": snapshot["remaining"],
        "percentage": snapshot["percentage"],
        "income": snapshot["income"],
        "savings": snapshot["savings"],
        "base_currency": snapshot["base_currency"],
    }
