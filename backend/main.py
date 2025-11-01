from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware 
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, date

import shutil
import os

from backend.database import get_db, engine
from backend import models, schemas
from backend.models import Account, Category, Payee, Location, Project, Transaction, ExchangeRate
from backend.schemas import ExchangeRateResponse
from backend.balance_calculator import recalculate_balances_from_transaction, initialise_all_balances
from sqlalchemy import func

# Create tables if they don't exist
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Financisto Manager API",
    description="Personal finance management system",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (for development)
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)

# ============================================
# ACCOUNTS ENDPOINTS
# ============================================

@app.get("/accounts", response_model=List[schemas.AccountResponse])
def get_accounts(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    Retrieve all accounts.
    """
    accounts = db.query(models.Account).offset(skip).limit(limit).all()
    return accounts


@app.post("/accounts", response_model=schemas.AccountResponse)
def create_account(
    account: schemas.AccountCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new account.
    """
    db_account = models.Account(**account.dict())
    db.add(db_account)
    db.commit()
    db.refresh(db_account)
    return db_account


# ============================================
# CATEGORIES ENDPOINTS
# ============================================

@app.get("/categories", response_model=List[schemas.CategoryResponse])
def get_categories(
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db)
):
    """
    Retrieve all categories.
    """
    categories = db.query(models.Category).offset(skip).limit(limit).all()
    return categories


@app.post("/categories", response_model=schemas.CategoryResponse)
def create_category(
    category: schemas.CategoryCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new category.
    """
    db_category = models.Category(**category.dict())
    db.add(db_category)
    db.commit()
    db.refresh(db_category)
    return db_category


# ============================================
# PAYEES ENDPOINTS
# ============================================

@app.get("/payees", response_model=List[schemas.PayeeResponse])
def get_payees(
    skip: int = 0,
    limit: int = 1000,
    db: Session = Depends(get_db)
):
    """
    Retrieve all payees.
    """
    payees = db.query(models.Payee).offset(skip).limit(limit).all()
    return payees

@app.post("/payees", response_model=schemas.PayeeResponse)
def create_payee(
    payee: schemas.PayeeCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new payee.
    """
    # Check if payee already exists
    existing_payee = db.query(models.Payee).filter(models.Payee.name == payee.name).first()
    if existing_payee:
        return existing_payee  # Return existing instead of error
    
    db_payee = models.Payee(**payee.dict())
    db.add(db_payee)
    db.commit()
    db.refresh(db_payee)
    return db_payee


# ============================================
# LOCATIONS ENDPOINTS
# ============================================

@app.get("/locations", response_model=List[schemas.LocationResponse])
def get_locations(
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db)
):
    """
    Retrieve all locations.
    """
    locations = db.query(models.Location).offset(skip).limit(limit).all()
    return locations



@app.post("/locations", response_model=schemas.LocationResponse)
def create_location(
    location: schemas.LocationCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new location.
    """
    db_location = models.Location(**location.dict())
    db.add(db_location)
    db.commit()
    db.refresh(db_location)
    return db_location


# ============================================
# PROJECTS ENDPOINTS
# ============================================

@app.get("/projects", response_model=List[schemas.ProjectResponse])
def get_projects(
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db)
):
    """
    Retrieve all projects.
    """
    projects = db.query(models.Project).offset(skip).limit(limit).all()
    return projects


@app.post("/projects", response_model=schemas.ProjectResponse)
def create_project(
    project: schemas.ProjectCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new project.
    """
    db_project = models.Project(**project.dict())
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return db_project


# ============================================
# UPDATE ENDPOINTS
# ============================================

@app.put("/accounts/{account_id}", response_model=schemas.AccountResponse)
def update_account(
    account_id: int,
    account: schemas.AccountCreate,
    db: Session = Depends(get_db)
):
    """
    Update an existing account.
    """
    db_account = db.query(models.Account).filter(models.Account.id == account_id).first()
    
    if not db_account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # Update fields
    for key, value in account.dict().items():
        setattr(db_account, key, value)
    
    db.commit()
    db.refresh(db_account)
    
    return db_account


@app.put("/categories/{category_id}", response_model=schemas.CategoryResponse)
def update_category(
    category_id: int,
    category: schemas.CategoryCreate,
    db: Session = Depends(get_db)
):
    """
    Update an existing category.
    """
    db_category = db.query(models.Category).filter(models.Category.id == category_id).first()
    
    if not db_category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Update fields
    for key, value in category.dict().items():
        setattr(db_category, key, value)
    
    db.commit()
    db.refresh(db_category)
    
    return db_category


@app.put("/payees/{payee_id}", response_model=schemas.PayeeResponse)
def update_payee(
    payee_id: int,
    payee: schemas.PayeeCreate,
    db: Session = Depends(get_db)
):
    """
    Update an existing payee.
    """
    db_payee = db.query(models.Payee).filter(models.Payee.id == payee_id).first()
    
    if not db_payee:
        raise HTTPException(status_code=404, detail="Payee not found")
    
    # Update fields
    for key, value in payee.dict().items():
        setattr(db_payee, key, value)
    
    db.commit()
    db.refresh(db_payee)
    
    return db_payee


@app.put("/locations/{location_id}", response_model=schemas.LocationResponse)
def update_location(
    location_id: int,
    location: schemas.LocationCreate,
    db: Session = Depends(get_db)
):
    """
    Update an existing location.
    """
    db_location = db.query(models.Location).filter(models.Location.id == location_id).first()
    
    if not db_location:
        raise HTTPException(status_code=404, detail="Location not found")
    
    # Update fields
    for key, value in location.dict().items():
        setattr(db_location, key, value)
    
    db.commit()
    db.refresh(db_location)
    
    return db_location


@app.put("/projects/{project_id}", response_model=schemas.ProjectResponse)
def update_project(
    project_id: int,
    project: schemas.ProjectCreate,
    db: Session = Depends(get_db)
):
    """
    Update an existing project.
    """
    db_project = db.query(models.Project).filter(models.Project.id == project_id).first()
    
    if not db_project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    # Update fields
    for key, value in project.dict().items():
        setattr(db_project, key, value)
    
    db.commit()
    db.refresh(db_project)
    
    return db_project


# ============================================
# TRANSACTIONS ENDPOINTS
# ============================================

@app.get("/transactions", response_model=List[schemas.TransactionWithDetails])
def get_transactions(
    skip: int = 0,
    limit: int = 50,
    account_id: Optional[int] = None,
    category_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    """
    Retrieve transactions with optional filters.
    """
    query = db.query(models.Transaction)
    
    # Apply filters
    if account_id:
        query = query.filter(models.Transaction.account_id == account_id)
    if category_id:
        query = query.filter(models.Transaction.category_id == category_id)
    if start_date:
        query = query.filter(models.Transaction.date >= start_date)
    if end_date:
        query = query.filter(models.Transaction.date <= end_date)
    
    # Order by date descending (most recent first)
    query = query.order_by(models.Transaction.date.desc())
    
    transactions = query.offset(skip).limit(limit).all()
    
    # Add related entity names
    result = []
    for t in transactions:
        trans_dict = {
            "id": t.id,
            "date": t.date,
            "amount": t.amount,
            "currency": t.currency,
            "note": t.note,
            "account_id": t.account_id,
            "category_id": t.category_id,
            "payee_id": t.payee_id,
            "location_id": t.location_id,
            "project_id": t.project_id,
            "created_at": t.created_at,
            "updated_at": t.updated_at,
            "account_balance_after": t.account_balance_after,
            "total_balance_after": t.total_balance_after,
            "account_name": t.account.name if t.account else None,
            "category_name": t.category.name if t.category else None,
            "payee_name": t.payee.name if t.payee else None,
            "location_name": t.location.name if t.location else None,
            "project_name": t.project.name if t.project else None,
        }
        result.append(trans_dict)
    
    return result


@app.post("/transactions", response_model=schemas.TransactionResponse)
def create_transaction(
    transaction: schemas.TransactionCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new transaction and recalculate balances.
    """
    db_transaction = models.Transaction(**transaction.dict())
    db.add(db_transaction)
    db.flush()  # Get the ID without committing
    
    # Recalculate balances from this transaction onwards
    recalculate_balances_from_transaction(db, db_transaction.id)
    
    db.refresh(db_transaction)
    return db_transaction


@app.get("/transactions/transfers")
def get_transfers(db: Session = Depends(get_db)):
    """
    Get transfer transactions grouped together.
    Identifies Transfer In/Out pairs and groups them.
    """
    from sqlalchemy import or_
    
    # Get all transactions with Transfer locations
    transfer_in_location = db.query(models.Location).filter(models.Location.name == "Transfer In").first()
    transfer_out_location = db.query(models.Location).filter(models.Location.name == "Transfer Out").first()
    
    if not transfer_in_location or not transfer_out_location:
        return []
    
    # Get all transfer transactions
    transfers = db.query(models.Transaction).filter(
        or_(
            models.Transaction.location_id == transfer_in_location.id,
            models.Transaction.location_id == transfer_out_location.id
        )
    ).order_by(models.Transaction.date.desc()).all()
    
    # Group transfers by datetime and amount
    grouped_transfers = []
    processed_ids = set()
    
    for trans in transfers:
        if trans.id in processed_ids:
            continue
            
        # Look for matching transfer (same datetime, opposite amount)
        matching = None
        for other in transfers:
            if other.id != trans.id and other.id not in processed_ids:
                # Same datetime, opposite signs
                if (trans.date == other.date and 
                    trans.amount * other.amount < 0 and
                    trans.location_id != other.location_id):
                    matching = other
                    break
        
        if matching:
            # Determine which is out and which is in
            if trans.amount < 0:
                transfer_out = trans
                transfer_in = matching
            else:
                transfer_out = matching
                transfer_in = trans
            
            grouped_transfers.append({
                "id": f"transfer_{transfer_out.id}_{transfer_in.id}",
                "date": str(trans.date),
                "from_account_id": transfer_out.account_id,
                "from_account_name": transfer_out.account.name if transfer_out.account else None,
                "from_amount": abs(transfer_out.amount),
                "from_currency": transfer_out.currency,
                "to_account_id": transfer_in.account_id,
                "to_account_name": transfer_in.account.name if transfer_in.account else None,
                "to_amount": transfer_in.amount,
                "to_currency": transfer_in.currency,
                "note": transfer_out.note or transfer_in.note,
                "transfer_out_id": transfer_out.id,
                "transfer_in_id": transfer_in.id
            })
            
            processed_ids.add(trans.id)
            processed_ids.add(matching.id)
    
    return grouped_transfers


@app.get("/transactions/{transaction_id}", response_model=schemas.TransactionWithDetails)
def get_transaction(
    transaction_id: int,
    db: Session = Depends(get_db)
):
    """
    Retrieve a specific transaction by ID.
    """
    transaction = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    return {
        "id": transaction.id,
        "date": transaction.date,
        "amount": transaction.amount,
        "currency": transaction.currency,
        "note": transaction.note,
        "account_id": transaction.account_id,
        "category_id": transaction.category_id,
        "payee_id": transaction.payee_id,
        "location_id": transaction.location_id,
        "project_id": transaction.project_id,
        "created_at": transaction.created_at,
        "updated_at": transaction.updated_at,
        "account_balance_after": transaction.account_balance_after,
        "total_balance_after": transaction.total_balance_after,
        "account_name": transaction.account.name if transaction.account else None,
        "category_name": transaction.category.name if transaction.category else None,
        "payee_name": transaction.payee.name if transaction.payee else None,
        "location_name": transaction.location.name if transaction.location else None,
        "project_name": transaction.project.name if transaction.project else None,
    }

@app.put("/transactions/{transaction_id}", response_model=schemas.TransactionResponse)
def update_transaction(
    transaction_id: int,
    transaction: schemas.TransactionCreate,
    db: Session = Depends(get_db)
):
    """
    Update an existing transaction and recalculate balances.
    """
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id
    ).first()
    
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    old_date = db_transaction.date
    old_account_id = db_transaction.account_id
    
    # Update fields
    for key, value in transaction.dict().items():
        setattr(db_transaction, key, value)
    
    db_transaction.updated_at = datetime.utcnow()
    db.flush()
    
    # Determine which accounts are affected
    affected_accounts = list(set([old_account_id, db_transaction.account_id]))
    
    # Recalculate from the earlier of old_date or new date
    earliest_date = min(old_date, db_transaction.date)
    earliest_transaction = db.query(models.Transaction).filter(
        models.Transaction.date == earliest_date
    ).order_by(models.Transaction.id.asc()).first()
    
    if earliest_transaction:
        recalculate_balances_from_transaction(
            db, 
            earliest_transaction.id,
            affected_accounts
        )
    
    db.refresh(db_transaction)
    return db_transaction


@app.delete("/transactions/{transaction_id}")
def delete_transaction(
    transaction_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a transaction and recalculate balances.
    """
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id
    ).first()
    
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    transaction_date = db_transaction.date
    account_id = db_transaction.account_id
    
    # Delete the transaction
    db.delete(db_transaction)
    db.flush()
    
    # Find the next transaction after the deleted one
    next_transaction = db.query(models.Transaction).filter(
        models.Transaction.date >= transaction_date,
        models.Transaction.account_id == account_id
    ).order_by(
        models.Transaction.date.asc(), 
        models.Transaction.id.asc()
    ).first()
    
    if next_transaction:
        recalculate_balances_from_transaction(db, next_transaction.id, [account_id])
    
    db.commit()
    return {"message": "Transaction deleted successfully"}


from pydantic import BaseModel

class TransferCreate(BaseModel):
    date: datetime
    from_account_id: int
    to_account_id: int
    from_amount: float
    to_amount: Optional[float] = None  # If None, uses from_amount
    note: Optional[str] = None

@app.post("/transactions/transfer")
def create_transfer(
    transfer: TransferCreate,
    db: Session = Depends(get_db)
):
    """
    Create a transfer between two accounts and recalculate balances.
    Creates two transactions: one negative (out) and one positive (in).
    """
    # Get Transfer locations
    transfer_out_loc = db.query(models.Location).filter(
        models.Location.name == "Transfer Out"
    ).first()
    transfer_in_loc = db.query(models.Location).filter(
        models.Location.name == "Transfer In"
    ).first()
    
    if not transfer_out_loc:
        transfer_out_loc = models.Location(name="Transfer Out")
        db.add(transfer_out_loc)
        db.flush()
    
    if not transfer_in_loc:
        transfer_in_loc = models.Location(name="Transfer In")
        db.add(transfer_in_loc)
        db.flush()
    
    # Get accounts to determine currencies
    from_account = db.query(models.Account).filter(
        models.Account.id == transfer.from_account_id
    ).first()
    to_account = db.query(models.Account).filter(
        models.Account.id == transfer.to_account_id
    ).first()
    
    if not from_account or not to_account:
        raise HTTPException(status_code=404, detail="Account not found")
    
    # If to_amount not specified, use from_amount
    to_amount = transfer.to_amount if transfer.to_amount else transfer.from_amount
    
    # Create outgoing transaction
    transaction_out = models.Transaction(
        date=transfer.date,
        amount=-abs(transfer.from_amount),
        currency=from_account.currency,
        account_id=transfer.from_account_id,
        location_id=transfer_out_loc.id,
        note=transfer.note
    )
    db.add(transaction_out)
    db.flush()
    
    # Create incoming transaction
    transaction_in = models.Transaction(
        date=transfer.date,
        amount=abs(to_amount),
        currency=to_account.currency,
        account_id=transfer.to_account_id,
        location_id=transfer_in_loc.id,
        note=transfer.note
    )
    db.add(transaction_in)
    db.flush()
    
    # Recalculate balances for both accounts
    # Use the earlier transaction ID to start recalculation
    earlier_transaction_id = min(transaction_out.id, transaction_in.id)
    recalculate_balances_from_transaction(
        db,
        earlier_transaction_id,
        [transfer.from_account_id, transfer.to_account_id]
    )
    
    db.refresh(transaction_out)
    db.refresh(transaction_in)
    
    return {
        "transfer_out": transaction_out,
        "transfer_in": transaction_in,
        "message": "Transfer created successfully"
    }

# ============================================
# EXCHANGE RATES ENDPOINTS
# ============================================

@app.get("/exchange-rates/latest")
def get_latest_exchange_rates(db: Session = Depends(get_db)):
    """
    Get the most recent exchange rates for all currencies.
    """
    from sqlalchemy import func
    
    # Get the most recent rate for each currency
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
    
    # Always ensure GBP is 1.0 (base currency)
    rates_dict['GBP'] = 1.0
    
    # Get most common currency
    currency_counts = db.query(
        Transaction.currency,
        func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(func.count(Transaction.id).desc()).all()
    
    most_common_currency = currency_counts[0][0] if currency_counts else "GBP"
    
    return {
        "base_currency": most_common_currency,
        "rates": rates_dict,
        "last_updated": rates_query[0].date.isoformat() if rates_query else None
    }


@app.post("/exchange-rates/update")
def trigger_exchange_rate_update(db: Session = Depends(get_db)):
    """
    Manually trigger an exchange rate update.
    """
    try:
        from update_exchange_rates import update_exchange_rates
        update_exchange_rates()
        return {"message": "Exchange rates updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to update rates: {str(e)}")


@app.get("/exchange-rates", response_model=List[ExchangeRateResponse])
def get_exchange_rates_history(
    currency: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """
    Get historical exchange rates with optional currency filter.
    """
    query = db.query(ExchangeRate)
    
    if currency:
        query = query.filter(ExchangeRate.currency == currency)
    
    query = query.order_by(ExchangeRate.date.desc())
    rates = query.offset(skip).limit(limit).all()
    
    return rates

# ============================================
# DASHBOARD / STATISTICS
# ============================================

@app.get("/dashboard/summary")
def get_dashboard_summary(db: Session = Depends(get_db)):
    """
    Get summary statistics for the dashboard with currency conversion.
    Optimised version with aggregation instead of loading all rows.
    """
    from sqlalchemy import func as sql_func, case
    
    # Get latest exchange rates
    subquery = db.query(
        ExchangeRate.currency,
        sql_func.max(ExchangeRate.date).label('max_date')
    ).group_by(ExchangeRate.currency).subquery()
    
    rates_query = db.query(ExchangeRate).join(
        subquery,
        (ExchangeRate.currency == subquery.c.currency) &
        (ExchangeRate.date == subquery.c.max_date)
    ).all()
    
    rates_dict = {rate.currency: rate.rate for rate in rates_query}
    rates_dict['GBP'] = 1.0
    
    # Find most common currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()
    
    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    base_rate = rates_dict.get(base_currency, 1.0)
    
    # OPTIMISATION: Use aggregation with CASE for currency conversion
    # Instead of loading all transactions into memory
    conversion_cases = []
    for currency, rate in rates_dict.items():
        conversion_factor = base_rate / rate
        conversion_cases.append(
            (Transaction.currency == currency, Transaction.amount * conversion_factor)
        )
    
    # Add fallback for currencies without rates
    conversion_expression = case(
        *conversion_cases,
        else_=Transaction.amount  # Fallback
    )
    
    total_balance_converted = db.query(
        sql_func.sum(conversion_expression)
    ).scalar() or 0
    
    # Count queries remain the same (already optimised)
    total_transactions = db.query(sql_func.count(Transaction.id)).scalar()
    total_accounts = db.query(sql_func.count(Account.id)).scalar()
    total_categories = db.query(sql_func.count(Category.id)).scalar()
    
    return {
        "total_transactions": total_transactions,
        "total_accounts": total_accounts,
        "total_categories": total_categories,
        "total_balance": round(total_balance_converted, 2),
        "base_currency": base_currency,
        "rates_available": len(rates_dict) > 0
    }


# ============================================
# ADMIN / MAINTENANCE ENDPOINTS
# ============================================

@app.post("/admin/initialise-balances")
def initialise_balances(db: Session = Depends(get_db)):
    """
    Initialise balance columns for all existing transactions.
    This should be run once after migration.
    """
    try:
        initialise_all_balances(db)
        return {"message": "Balances initialised successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to initialise balances: {str(e)}")


@app.get("/")
def root():
    """
    Root endpoint with API information.
    """
    return {
        "message": "Welcome to Financisto Manager API",
        "docs": "/docs",
        "version": "1.0.0"
    }


@app.post("/admin/backup-database")
def backup_database():
    """
    Create a backup copy of the database and return it as a download.
    """
    try:
        # Source database path
        source_db = "./data/finance.db"
        
        # Check if database exists
        if not os.path.exists(source_db):
            raise HTTPException(status_code=404, detail="Database file not found")
        
        # Generate timestamp for backup filename
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"finance_backup_{timestamp}.db"
        backup_path = f"./data/{backup_filename}"
        
        # Create backup copy
        shutil.copy2(source_db, backup_path)
        
        # Return the file as a download
        return FileResponse(
            path=backup_path,
            filename=backup_filename,
            media_type='application/octet-stream',
            background=None  # Keep file after sending
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create backup: {str(e)}")