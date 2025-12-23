"""
Helper functions for balance calculations and exchange rates.
Consolidates balance_calculator.py and exchange_rate_helpers.py.
"""
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
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
    """Get the most commonly used currency in transactions."""
    result = db.query(
        Transaction.currency,
        func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(
        func.count(Transaction.id).desc()
    ).first()
    return result[0] if result else "GBP"


def convert_to_base_currency(amount: float, currency: str, base_currency: str, rates: dict) -> float:
    """Convert amount to base currency using provided rates."""
    if amount is None:
        return 0.0
    if currency == base_currency:
        return amount
    currency_rate = rates.get(currency, 1.0)
    base_rate = rates.get(base_currency, 1.0)
    return amount * (base_rate / currency_rate)


def recalculate_balances_from_transaction(
    db: Session,
    transaction_id: int,
    affected_account_ids: Optional[List[int]] = None
) -> None:
    """
    Recalculate balances starting from a specific transaction.
    Updates account_balance_after and total_balance_after for affected transactions.
    """
    db.flush()
    
    rates = get_latest_rates(db)
    base_currency = get_base_currency(db)
    
    trigger_transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not trigger_transaction:
        return
    
    if affected_account_ids is None:
        affected_account_ids = [trigger_transaction.account_id]
    
    # Step 1: Recalculate account balances
    for account_id in affected_account_ids:
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account:
            continue
            
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account_id
        ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()
        
        running_balance = float(account.initial_balance or 0.0)
        
        for t in transactions:
            running_balance += float(t.amount or 0.0)
            t.account_balance_after = round(running_balance, 2)
            
        account.current_balance = round(running_balance, 2)

    # Step 2: Recalculate total balances
    all_transactions = db.query(Transaction).order_by(
        Transaction.date.asc(), Transaction.id.asc()
    ).all()
    
    total_balance = 0.0
    for t in all_transactions:
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
    
    # Step 2: Total balances
    all_transactions = db.query(Transaction).order_by(
        Transaction.date.asc(), Transaction.id.asc()
    ).all()
    
    total_balance = 0.0
    for transaction in all_transactions:
        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        converted = convert_to_base_currency(amount, transaction.currency, base_currency, rates)
        total_balance += converted
        transaction.total_balance_after = round(total_balance, 2)
    
    db.flush()
    print(f"Initialised balances for {len(all_transactions)} transactions")