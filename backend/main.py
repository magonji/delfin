from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware 
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, date

from backend.database import get_db, engine
from backend import models, schemas

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
    limit: int = 200,
    db: Session = Depends(get_db)
):
    """
    Retrieve all payees.
    """
    payees = db.query(models.Payee).offset(skip).limit(limit).all()
    return payees


# ============================================
# LOCATIONS ENDPOINTS
# ============================================

@app.get("/locations", response_model=List[schemas.PayeeResponse])
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


# ============================================
# PROJECTS ENDPOINTS
# ============================================

@app.get("/projects", response_model=List[schemas.PayeeResponse])
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
    Create a new transaction.
    """
    db_transaction = models.Transaction(**transaction.dict())
    db.add(db_transaction)
    db.commit()
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
    Update an existing transaction.
    """
    db_transaction = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    # Update fields
    for key, value in transaction.dict().items():
        setattr(db_transaction, key, value)
    
    db_transaction.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(db_transaction)
    
    return db_transaction


@app.delete("/transactions/{transaction_id}")
def delete_transaction(
    transaction_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a transaction.
    """
    db_transaction = db.query(models.Transaction).filter(models.Transaction.id == transaction_id).first()
    
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    
    db.delete(db_transaction)
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
    Create a transfer between two accounts.
    Creates two transactions: one negative (out) and one positive (in).
    """
    # Get Transfer locations
    transfer_out_loc = db.query(models.Location).filter(models.Location.name == "Transfer Out").first()
    transfer_in_loc = db.query(models.Location).filter(models.Location.name == "Transfer In").first()
    
    if not transfer_out_loc:
        transfer_out_loc = models.Location(name="Transfer Out")
        db.add(transfer_out_loc)
        db.flush()
    
    if not transfer_in_loc:
        transfer_in_loc = models.Location(name="Transfer In")
        db.add(transfer_in_loc)
        db.flush()
    
    # Get accounts to determine currencies
    from_account = db.query(models.Account).filter(models.Account.id == transfer.from_account_id).first()
    to_account = db.query(models.Account).filter(models.Account.id == transfer.to_account_id).first()
    
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
    
    db.commit()
    db.refresh(transaction_out)
    db.refresh(transaction_in)
    
    return {
        "transfer_out": transaction_out,
        "transfer_in": transaction_in,
        "message": "Transfer created successfully"
    }


# ============================================
# DASHBOARD / STATISTICS
# ============================================

@app.get("/dashboard/summary")
def get_dashboard_summary(db: Session = Depends(get_db)):
    """
    Get summary statistics for the dashboard.
    """
    total_transactions = db.query(models.Transaction).count()
    total_accounts = db.query(models.Account).count()
    total_categories = db.query(models.Category).count()
    
    # Calculate total balance across all accounts
    # (This is a simple sum - you might want more complex logic)
    from sqlalchemy import func
    total_balance = db.query(func.sum(models.Transaction.amount)).scalar() or 0
    
    return {
        "total_transactions": total_transactions,
        "total_accounts": total_accounts,
        "total_categories": total_categories,
        "total_balance": round(total_balance, 2)
    }


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