"""
Helper functions for balance calculations and exchange rates.
Consolidates balance_calculator.py and exchange_rate_helpers.py.
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_, update as sa_update
from backend.models import Transaction, Account, ExchangeRate


# =============================================================================
# EXCHANGE RATE FUNCTIONS
# =============================================================================

def get_latest_rates(db: Session) -> Dict[str, float]:
    """
    Get the most recent exchange rate for each currency.
    Returns dictionary with currency codes as keys and rates as values.
    GBP is always 1.0 (base currency).
    """
    subquery = db.query(
        ExchangeRate.currency,
        func.max(ExchangeRate.date).label('max_date')
    ).group_by(ExchangeRate.currency).subquery()
    
    rates_query = db.query(ExchangeRate).join(
        subquery,
        (ExchangeRate.currency == subquery.c.currency) &
        (ExchangeRate.date == subquery.c.max_date)
    ).all()
    
    rates_dict = {rate.currency: rate.rate for rate in rates_query}
    rates_dict['GBP'] = 1.0
    return rates_dict


def get_rate_for_date(db: Session, currency: str, target_date: date) -> Optional[float]:
    """
    Get exchange rate for a specific currency on a specific date.
    Looks backward up to 7 days if exact date not found (weekends/holidays).
    """
    if currency == 'GBP':
        return 1.0
    
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    
    # Try exact date first
    rate = db.query(ExchangeRate).filter(
        ExchangeRate.currency == currency,
        func.date(ExchangeRate.date) == target_date
    ).first()
    
    if rate:
        return rate.rate
    
    # Look backward up to 7 days
    for days_back in range(1, 8):
        check_date = target_date - timedelta(days=days_back)
        rate = db.query(ExchangeRate).filter(
            ExchangeRate.currency == currency,
            func.date(ExchangeRate.date) == check_date
        ).first()
        if rate:
            return rate.rate
    
    return None


def get_rates_for_date(db: Session, target_date: date) -> Dict[str, float]:
    """Get all available exchange rates for a specific date."""
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    
    rates = db.query(ExchangeRate).filter(
        func.date(ExchangeRate.date) == target_date
    ).all()
    
    rates_dict = {rate.currency: rate.rate for rate in rates}
    
    # If no rates found, look backward up to 7 days
    if not rates_dict:
        for days_back in range(1, 8):
            check_date = target_date - timedelta(days=days_back)
            rates = db.query(ExchangeRate).filter(
                func.date(ExchangeRate.date) == check_date
            ).all()
            if rates:
                rates_dict = {rate.currency: rate.rate for rate in rates}
                break
    
    rates_dict['GBP'] = 1.0
    return rates_dict


def get_rates_bulk(db: Session, currencies: list, date_from: date, date_to: date) -> Dict[date, Dict[str, float]]:
    """
    Get exchange rates for multiple currencies across a date range.
    More efficient than calling get_rates_for_date multiple times.
    Returns nested dictionary: {date: {currency: rate}}
    """
    if isinstance(date_from, datetime):
        date_from = date_from.date()
    if isinstance(date_to, datetime):
        date_to = date_to.date()
    
    rates = db.query(ExchangeRate).filter(
        and_(
            ExchangeRate.currency.in_(currencies),
            func.date(ExchangeRate.date) >= date_from,
            func.date(ExchangeRate.date) <= date_to
        )
    ).order_by(ExchangeRate.date).all()
    
    # Organise by date
    rates_by_date = {}
    for rate in rates:
        rate_date = rate.date.date() if isinstance(rate.date, datetime) else rate.date
        if rate_date not in rates_by_date:
            rates_by_date[rate_date] = {'GBP': 1.0}
        rates_by_date[rate_date][rate.currency] = rate.rate
    
    # Fill missing dates using previous rate (carry forward)
    all_dates = []
    current_date = date_from
    while current_date <= date_to:
        all_dates.append(current_date)
        current_date += timedelta(days=1)
    
    complete_rates = {}
    last_rates = {'GBP': 1.0}
    
    for current_date in all_dates:
        if current_date in rates_by_date:
            last_rates.update(rates_by_date[current_date])
        complete_rates[current_date] = last_rates.copy()
    
    return complete_rates


def convert_amount(amount: float, from_currency: str, to_currency: str, 
                   rate_from: float, rate_to: float) -> float:
    """
    Convert amount between currencies using exchange rates.
    Rates are GBP-based: first converts to GBP, then to target currency.
    """
    if from_currency == to_currency:
        return amount
    
    amount_in_gbp = amount / rate_from if rate_from != 0 else 0
    return amount_in_gbp * rate_to


# =============================================================================
# BALANCE CALCULATION FUNCTIONS
# =============================================================================

def get_base_currency(db: Session) -> str:
    """
    Currency used to display aggregated totals (dashboard, budgets, etc.).

    Honours the user's ``display_currency`` setting: a fixed supported code, or
    "auto" to use the most commonly used currency across transactions -- falling
    back to the accounts before sterling, so that someone who has entered their
    accounts and not yet a single movement is not told their euros are pounds.
    """
    from backend import settings_store

    configured = settings_store.get_settings().get("display_currency", "auto")
    if configured and configured != "auto":
        return configured

    result = db.query(
        Transaction.currency,
        func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(
        func.count(Transaction.id).desc()
    ).first()
    if result:
        return result[0]

    by_account = db.query(
        Account.currency,
        func.count(Account.id).label('count')
    ).filter(Account.currency.isnot(None)).group_by(Account.currency).order_by(
        func.count(Account.id).desc()
    ).first()
    return by_account[0] if by_account else "GBP"


def convert_to_base_currency(amount: float, currency: str, base_currency: str, rates: dict) -> float:
    """Convert amount to base currency using provided rates."""
    if amount is None:
        return 0.0
    if currency == base_currency:
        return amount
    currency_rate = rates.get(currency, 1.0)
    base_rate = rates.get(base_currency, 1.0)
    return amount * (base_rate / currency_rate)


def _as_date(value):
    """Any date-like value as a plain date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return None


def rebuild_total_balances(db: Session) -> None:
    """
    Rewrite ``total_balance_after`` for every transaction.

    The column is what the transactions list shows as "Total balance": the worth
    of every account added together at that moment, in the base currency. So it
    counts the accounts' opening balances, and converts each one at the rate of
    the day being reported rather than at today's.

    This is the single definition of that column. Three rebuilds used to keep one
    each, two of which summed transaction amounts from zero and so left out every
    opening balance -- a whole-ledger shift the moment either of them ran.
    """
    base_currency = get_base_currency(db)
    accounts = db.query(Account).all()
    accounts_map = {a.id: a for a in accounts}

    # Columns, not objects. Loading twelve thousand rows as mapped instances and
    # then marking each one dirty puts the whole cost of this in SQLAlchemy's
    # unit of work -- two thirds of it, measured -- for a column the ledger only
    # ever reads. The figures below are unchanged; only the way they are fetched
    # and written is.
    all_transactions = db.query(
        Transaction.id, Transaction.date, Transaction.account_id, Transaction.amount
    ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()
    if not all_transactions:
        return

    currencies = list({a.currency for a in accounts if a.currency and a.currency != base_currency})
    min_date = _as_date(all_transactions[0].date)
    max_date = _as_date(all_transactions[-1].date)
    historical_rates = get_rates_bulk(db, currencies, min_date, max_date) if currencies else {}

    # Accounts that never appear in a transaction still hold their opening
    # balance throughout, so they are seeded up front; leaving them out made this
    # figure disagree with the dashboard, which counts them.
    converted = {}
    updates = []
    touched = {t.account_id for t in all_transactions}
    opening_rates = historical_rates.get(min_date, {}) or {}
    opening_base_rate = opening_rates.get(base_currency, 1.0)
    for acc in accounts:
        if acc.id in touched or not acc.initial_balance:
            continue
        acc_rate = opening_rates.get(acc.currency or base_currency, 1.0)
        converted[acc.id] = float(acc.initial_balance) * (opening_base_rate / acc_rate)

    for t in all_transactions:
        if t is None:
            continue
        rates_for_day = historical_rates.get(_as_date(t.date), {base_currency: 1.0})
        base_rate = rates_for_day.get(base_currency, 1.0)
        acc = accounts_map.get(t.account_id)
        currency = acc.currency if acc else base_currency

        # An account enters the total carrying its opening balance, converted at
        # the rate of the day it first appears.
        if t.account_id not in converted:
            opening = 0.0
            if acc and acc.initial_balance:
                opening = float(acc.initial_balance) * (
                    base_rate / rates_for_day.get(acc.currency or base_currency, 1.0))
            converted[t.account_id] = opening

        converted[t.account_id] += float(t.amount or 0.0) * (
            base_rate / rates_for_day.get(currency, 1.0))
        updates.append({"id": t.id, "total_balance_after": round(sum(converted.values()), 2)})

    # Whatever the caller has already worked out goes to the database first.
    #
    # The session is created with autoflush off, so a change made on an instance
    # sits in memory until something flushes it -- and expiring the session does
    # not postpone such a change, it throws it away. The caller above this one
    # writes `account_balance_after` on every transaction it has just walked and
    # then calls this; the expiry below therefore used to discard the lot, while
    # the statement beside it wrote `total_balance_after` quite happily. An
    # imported statement came out with a total balance on every row and no
    # account balance at all, and recalculating did it again.
    db.flush()

    # One statement, by primary key, instead of a flush that has to work out what
    # changed on every instance it is holding.
    if updates:
        db.execute(sa_update(Transaction), updates)
        # Written behind the session's back, so what it is holding is stale.
        db.expire_all()


def recalculate_balances_from_transaction(
    db: Session,
    transaction_id: int,
    affected_account_ids: Optional[List[int]] = None
) -> None:
    """
    Recalculate balances starting from a specific transaction.
    Only recalculates from that point forward, not from the beginning.
    Updates account_balance_after and total_balance_after for affected transactions.
    """
    db.flush()

    rates = get_latest_rates(db)
    base_currency = get_base_currency(db)

    trigger_transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not trigger_transaction:
        return

    trigger_date = trigger_transaction.date

    if affected_account_ids is None:
        affected_account_ids = [trigger_transaction.account_id]

    # Step 1: Recalculate account balances only from trigger point forward
    for account_id in affected_account_ids:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            continue

        # Get the balance just before the trigger date for this account
        prev_transaction = db.query(Transaction).filter(
            Transaction.account_id == account_id,
            (Transaction.date < trigger_date) |
            ((Transaction.date == trigger_date) & (Transaction.id < trigger_transaction.id))
        ).order_by(Transaction.date.desc(), Transaction.id.desc()).first()

        if prev_transaction is None:
            running_balance = float(account.initial_balance or 0.0)
        elif prev_transaction.account_balance_after is not None:
            running_balance = float(prev_transaction.account_balance_after)
        else:
            # The predecessor carries no balance of its own: it was written with
            # recalculation deferred, which is what entering a run of transactions
            # through Save & New leaves behind. Its cached figure cannot seed the
            # running total, and opening the account again from its initial
            # balance would silently drop everything recorded before this point,
            # so add those amounts up instead.
            prior = db.query(func.sum(Transaction.amount)).filter(
                Transaction.account_id == account_id,
                (Transaction.date < trigger_date) |
                ((Transaction.date == trigger_date) & (Transaction.id < trigger_transaction.id))
            ).scalar()
            running_balance = float(account.initial_balance or 0.0) + float(prior or 0.0)

        # Only fetch transactions from the trigger point forward
        transactions_from = db.query(Transaction).filter(
            Transaction.account_id == account_id,
            (Transaction.date > trigger_date) |
            ((Transaction.date == trigger_date) & (Transaction.id >= trigger_transaction.id))
        ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()

        for t in transactions_from:
            running_balance += float(t.amount or 0.0)
            t.account_balance_after = round(running_balance, 2)

        account.current_balance = round(running_balance, 2)

    # Step 2: Recalculate total balances only from trigger point forward
    # Get total_balance just before the trigger transaction
    prev_total_tx = db.query(Transaction).filter(
        (Transaction.date < trigger_date) |
        ((Transaction.date == trigger_date) & (Transaction.id < trigger_transaction.id))
    ).order_by(Transaction.date.desc(), Transaction.id.desc()).first()

    before_trigger = (
        (Transaction.date < trigger_date) |
        ((Transaction.date == trigger_date) & (Transaction.id < trigger_transaction.id))
    )

    if prev_total_tx is None:
        # Nothing precedes the trigger, so this is the first transaction in the
        # ledger and the whole column is about to be rewritten from it anyway --
        # which is exactly what the one definition of the column does, opening
        # balances and all. Seeding from zero instead is what made the very first
        # transaction a brand new user entered report a total of minus its own
        # amount, next to a dashboard showing the money they actually had.
        rebuild_total_balances(db)
        db.flush()
        return
    if prev_total_tx.total_balance_after is not None:
        total_balance = float(prev_total_tx.total_balance_after)
    else:
        # Same gap as above, and costlier left alone: this figure seeds the total
        # of every transaction from here on, whatever account it belongs to, so a
        # wrong seed rewrites the whole tail of the ledger. Rather than re-derive
        # the base -- which would mean guessing at opening balances and the rates
        # they were converted at -- carry on from the last row that does hold a
        # total, adding up the deferred rows lying between the two.
        anchor = db.query(Transaction).filter(
            Transaction.total_balance_after.isnot(None), before_trigger
        ).order_by(Transaction.date.desc(), Transaction.id.desc()).first()

        if anchor is None:
            # Every row before this one is deferred too, so there is no total to
            # carry on from. Same answer as above: rewrite the column from its one
            # definition rather than invent a base.
            rebuild_total_balances(db)
            db.flush()
            return

        total_balance = float(anchor.total_balance_after)
        gap = db.query(Transaction).filter(
            before_trigger,
            (Transaction.date > anchor.date) |
            ((Transaction.date == anchor.date) & (Transaction.id > anchor.id)),
        )
        for row in gap.order_by(Transaction.date.asc(), Transaction.id.asc()).all():
            total_balance += convert_to_base_currency(
                float(row.amount or 0.0), row.currency, base_currency, rates
            )

    # Only iterate transactions from the trigger point forward
    transactions_from = db.query(Transaction).filter(
        (Transaction.date > trigger_date) |
        ((Transaction.date == trigger_date) & (Transaction.id >= trigger_transaction.id))
    ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()

    for t in transactions_from:
        converted = convert_to_base_currency(
            float(t.amount or 0.0), t.currency, base_currency, rates
        )
        total_balance += converted
        t.total_balance_after = round(total_balance, 2)

    db.flush()


def initialise_all_balances(db: Session) -> None:
    """
    Initialise balance columns for all existing transactions.
    Should be called on first run or to fix inconsistencies.
    """
    rates = get_latest_rates(db)
    base_currency = get_base_currency(db)
    accounts = db.query(Account).all()
    
    # Step 1: Account balances
    for account in accounts:
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account.id
        ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()
        
        account_balance = float(account.initial_balance) if account.initial_balance is not None else 0.0
        
        for transaction in transactions:
            amount = float(transaction.amount) if transaction.amount is not None else 0.0
            account_balance += amount
            transaction.account_balance_after = round(account_balance, 2)
        
        account.current_balance = round(account_balance, 2)
    
    # Step 2: Total balances, from the one definition of that column
    rebuild_total_balances(db)
    print("Initialised balances for all transactions")