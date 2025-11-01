from sqlalchemy.orm import Session
from sqlalchemy import func, case
from backend.models import Transaction, Account, ExchangeRate
from typing import Optional, List, Tuple
from datetime import datetime


def get_exchange_rates(db: Session) -> dict:
    """
    Retrieve the latest exchange rates for currency conversion.
    Returns a dictionary mapping currency codes to rates.
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


def get_base_currency(db: Session) -> str:
    """
    Determine the most commonly used currency as base currency.
    """
    currency_counts = db.query(
        Transaction.currency,
        func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(
        func.count(Transaction.id).desc()
    ).first()
    
    return currency_counts[0] if currency_counts else "GBP"


def convert_to_base_currency(amount: float, currency: str, base_currency: str, rates: dict) -> float:
    """
    Convert an amount from its currency to the base currency.
    """
    if currency == base_currency:
        return amount
    
    currency_rate = rates.get(currency, 1.0)
    base_rate = rates.get(base_currency, 1.0)
    
    # Convert: amount / currency_rate * base_rate
    return amount * (base_rate / currency_rate)


def is_transfer_transaction(transaction: Transaction, db: Session) -> bool:
    """
    Check if a transaction is part of a transfer (has Transfer In/Out location).
    """
    if not transaction.location_id:
        return False
    
    from backend.models import Location
    location = db.query(Location).filter(Location.id == transaction.location_id).first()
    
    return location and location.name in ["Transfer In", "Transfer Out"]


def recalculate_balances_from_transaction(
    db: Session,
    transaction_id: int,
    affected_account_ids: Optional[List[int]] = None
) -> None:
    """
    Recalculate balances for all transactions from a specific transaction onwards.
    
    Args:
        db: Database session
        transaction_id: ID of the transaction that was modified/created
        affected_account_ids: List of account IDs affected (for transfers)
    """
    # Get the transaction that triggered this recalculation
    trigger_transaction = db.query(Transaction).filter(
        Transaction.id == transaction_id
    ).first()
    
    if not trigger_transaction:
        return
    
    # Get exchange rates
    rates = get_exchange_rates(db)
    base_currency = get_base_currency(db)
    base_rate = rates.get(base_currency, 1.0)
    
    # Determine which accounts need recalculation
    if affected_account_ids is None:
        affected_account_ids = [trigger_transaction.account_id]
    
    # For each affected account, recalculate from the trigger transaction onwards
    for account_id in affected_account_ids:
        # Get all transactions for this account from trigger date onwards, sorted by date then id
        transactions_to_update = db.query(Transaction).filter(
            Transaction.account_id == account_id,
            Transaction.date >= trigger_transaction.date
        ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()
        
        # Get the balance before the first transaction to update
        previous_transactions = db.query(Transaction).filter(
            Transaction.account_id == account_id,
            Transaction.date < trigger_transaction.date
        ).order_by(Transaction.date.desc(), Transaction.id.desc()).first()
        
        account_balance = previous_transactions.account_balance_after if previous_transactions else 0.0
        
        # Update each transaction's account balance
        for transaction in transactions_to_update:
            account_balance += transaction.amount
            transaction.account_balance_after = account_balance
    
    # Now recalculate total balances for ALL transactions from trigger date onwards
    all_transactions_to_update = db.query(Transaction).filter(
        Transaction.date >= trigger_transaction.date
    ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()
    
    # Get total balance before the first transaction
    previous_total_transaction = db.query(Transaction).filter(
        Transaction.date < trigger_transaction.date
    ).order_by(Transaction.date.desc(), Transaction.id.desc()).first()
    
    total_balance = previous_total_transaction.total_balance_after if previous_total_transaction else 0.0
    
    # Update each transaction's total balance
    for transaction in all_transactions_to_update:
        # Convert transaction amount to base currency
        converted_amount = convert_to_base_currency(
            transaction.amount,
            transaction.currency,
            base_currency,
            rates
        )
        
        total_balance += converted_amount
        transaction.total_balance_after = total_balance
    
    db.commit()


def initialise_all_balances(db: Session) -> None:
    """
    Initialise balance columns for all existing transactions.
    This should be run once after adding the new columns to the database.
    """
    # Get exchange rates
    rates = get_exchange_rates(db)
    base_currency = get_base_currency(db)
    
    # Get all accounts
    accounts = db.query(Account).all()
    
    # For each account, calculate balances chronologically
    for account in accounts:
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account.id
        ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()
        
        account_balance = 0.0
        for transaction in transactions:
            account_balance += transaction.amount
            transaction.account_balance_after = account_balance
    
    # Calculate total balances across all accounts
    all_transactions = db.query(Transaction).order_by(
        Transaction.date.asc(), Transaction.id.asc()
    ).all()
    
    total_balance = 0.0
    for transaction in all_transactions:
        converted_amount = convert_to_base_currency(
            transaction.amount,
            transaction.currency,
            base_currency,
            rates
        )
        total_balance += converted_amount
        transaction.total_balance_after = total_balance
    
    db.commit()
    print(f"Initialised balances for {len(all_transactions)} transactions across {len(accounts)} accounts")