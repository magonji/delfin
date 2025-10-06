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