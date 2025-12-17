from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.models import Transaction, Account, ExchangeRate, Location
from typing import Optional, List

def get_exchange_rates(db: Session) -> dict:
    # ... (igual que antes) ...
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
    # ... (igual que antes) ...
    currency_counts = db.query(
        Transaction.currency,
        func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(
        func.count(Transaction.id).desc()
    ).first()
    return currency_counts[0] if currency_counts else "GBP"

def convert_to_base_currency(amount: float, currency: str, base_currency: str, rates: dict) -> float:
    if amount is None: return 0.0
    if currency == base_currency: return amount
    currency_rate = rates.get(currency, 1.0)
    base_rate = rates.get(base_currency, 1.0)
    return amount * (base_rate / currency_rate)

def recalculate_balances_from_transaction(
    db: Session,
    transaction_id: int,
    affected_account_ids: Optional[List[int]] = None
) -> None:
    """
    Recalcula balances asegurando visibilidad de transacciones nuevas.
    """
    # 1. Forzar que todo lo pendiente se escriba en la transacción actual de la BD
    db.flush()
    
    # Obtener tasas una sola vez
    rates = get_exchange_rates(db)
    base_currency = get_base_currency(db)
    
    # Identificar transacción gatillo
    trigger_transaction = db.query(Transaction).filter(Transaction.id == transaction_id).first()
    if not trigger_transaction:
        return
    
    if affected_account_ids is None:
        affected_account_ids = [trigger_transaction.account_id]
    
    # === PASO 1: Recalcular saldos de cuenta ===
    for account_id in affected_account_ids:
        # Obtener cuenta
        account = db.query(Account).filter(Account.id == account_id).first()
        if not account: continue
            
        # Obtener TODAS las transacciones ordenadas
        # NOTA: Al estar en la misma sesión, SQLAlchemy debería incluir las nuevas
        # si se han hecho flush().
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account_id
        ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()
        
        # Iniciar con saldo inicial seguro
        running_balance = float(account.initial_balance or 0.0)
        
        for t in transactions:
            amt = float(t.amount or 0.0)
            running_balance += amt
            
            # Actualizar el objeto (esto marcará el objeto como 'dirty' en la sesión)
            t.account_balance_after = round(running_balance, 2)
            
        # Actualizar saldo final de la cuenta
        account.current_balance = round(running_balance, 2)

    # === PASO 2: Recalcular saldos totales ===
    # Aquí es crítico: Si la query no ve las nuevas tx, el total será incorrecto
    # pero no debería causar None en account_balance_after.
    
    all_transactions = db.query(Transaction).order_by(
        Transaction.date.asc(), Transaction.id.asc()
    ).all()
    
    total_balance = 0.0
    for t in all_transactions:
        amt = float(t.amount or 0.0)
        converted = convert_to_base_currency(amt, t.currency, base_currency, rates)
        total_balance += converted
        t.total_balance_after = round(total_balance, 2)
        
    # Flush para preparar los cambios, pero no commit
    # El commit debe hacerse en la función que llama a esta
    db.flush()



def initialise_all_balances(db: Session) -> None:
    """
    Initialise balance columns for all existing transactions.
    Updated with robust error checking similar to recalculate function.
    """
    rates = get_exchange_rates(db)
    base_currency = get_base_currency(db)
    accounts = db.query(Account).all()
    
    # STEP 1: Account balances
    for account in accounts:
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account.id
        ).order_by(Transaction.date.asc(), Transaction.id.asc()).all()
        
        # Safety check for None
        account_balance = float(account.initial_balance) if account.initial_balance is not None else 0.0
        
        for transaction in transactions:
            amount = float(transaction.amount) if transaction.amount is not None else 0.0
            account_balance += amount
            transaction.account_balance_after = round(account_balance, 2)
        
        account.current_balance = round(account_balance, 2)
    
    # STEP 2: Total balances
    all_transactions = db.query(Transaction).order_by(
        Transaction.date.asc(), Transaction.id.asc()
    ).all()
    
    total_balance = 0.0
    for transaction in all_transactions:
        amount = float(transaction.amount) if transaction.amount is not None else 0.0
        
        converted_amount = convert_to_base_currency(
            amount,
            transaction.currency,
            base_currency,
            rates
        )
        total_balance += converted_amount
        transaction.total_balance_after = round(total_balance, 2)
    
    db.flush()
    print(f"Initialised balances for {len(all_transactions)} transactions")