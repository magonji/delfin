from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, date, timedelta, time
from sqlalchemy import func as sql_func, case, and_, or_, func
import shutil
import os
from backend.database import get_db, engine
from backend import models, schemas
from backend.models import Account, Category, Payee, Location, Project, Transaction, ExchangeRate
from backend.schemas import ExchangeRateResponse
from backend.helpers import (
    recalculate_balances_from_transaction, 
    initialise_all_balances,
    get_rates_bulk, 
    get_latest_rates
)

# Create tables if they don't exist
models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Delfin API",
    description="Personal finance management system based in Financisto",
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
    include_closed: bool = False,  # NEW: parameter to include closed accounts
    db: Session = Depends(get_db)
):
    """
    Retrieve all accounts. By default, only returns active accounts.
    Set include_closed=true to include closed accounts as well.
    """
    query = db.query(models.Account)
    # Filter by active status unless include_closed is True
    if not include_closed:
        query = query.filter(models.Account.is_active == 1)
    accounts = query.offset(skip).limit(limit).all()
    return accounts


@app.get("/accounts/with-balances")
def get_accounts_with_balances(
    include_closed: bool = False,
    db: Session = Depends(get_db)
):
    """
    Get all accounts with their current balances from the last transaction.
    More efficient than getting balance separately for each account.
    """
    query = db.query(models.Account)
    if not include_closed:
        query = query.filter(models.Account.is_active == 1)
    accounts = query.all()

    accounts_with_balances = []
    for account in accounts:
        # Get last transaction for this account
        last_transaction = db.query(models.Transaction).filter(
            models.Transaction.account_id == account.id
        ).order_by(models.Transaction.date.desc(), models.Transaction.id.desc()).first()

        if last_transaction and last_transaction.account_balance_after is not None:
            current_balance = last_transaction.account_balance_after
        else:
            current_balance = account.initial_balance

        accounts_with_balances.append({
            "id": account.id,
            "name": account.name,
            "type": account.type,
            "currency": account.currency,
            "initial_balance": account.initial_balance,
            "current_balance": current_balance,
            "is_active": account.is_active,
            "created_at": account.created_at
        })
    return accounts_with_balances


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


@app.delete("/categories/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db)
):
    """
    Delete a category. Will fail if transactions are using this category.
    """
    category = db.query(models.Category).filter(models.Category.id == category_id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    
    # Check if any transactions use this category
    transaction_count = db.query(models.Transaction).filter(
        models.Transaction.category_id == category_id
    ).count()
    
    if transaction_count > 0:
        raise HTTPException(
            status_code=400, 
            detail=f"Cannot delete category: {transaction_count} transactions are using it"
        )
    
    # Check if this is a parent category with subcategories
    if not category.parent:
        subcategories = db.query(models.Category).filter(
            models.Category.parent == category.name
        ).count()
        if subcategories > 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot delete parent category: {subcategories} subcategories exist"
            )
    
    db.delete(category)
    db.commit()
    return {"message": f"Category '{category.name}' deleted successfully"}


# ============================================
# PAYEES ENDPOINTS
# ============================================

@app.get("/payees", response_model=List[schemas.PayeeWithDetails])
def get_payees(db: Session = Depends(get_db)):
    """
    Retrieve all payees with their most common associations.
    """
    payees = db.query(Payee).all()
    result = []
    for payee in payees:
        payee_dict = {
            "id": payee.id,
            "name": payee.name,
            "most_common_category_id": payee.most_common_category_id,
            "most_common_location_id": payee.most_common_location_id,
            "most_common_project_id": payee.most_common_project_id,
            "created_at": payee.created_at,
            "updated_at": payee.updated_at,
            "most_common_category_name": payee.most_common_category.name if payee.most_common_category else None,
            "most_common_location_name": payee.most_common_location.name if payee.most_common_location else None,
            "most_common_project_name": payee.most_common_project.name if payee.most_common_project else None,
        }
        result.append(payee_dict)
    return result


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


# Add these endpoints after the create_payee endpoint (after line 182 in main.py)
@app.post("/payees/{payee_id}/recalculate-stats")
def recalculate_payee_stats(payee_id: int, db: Session = Depends(get_db)):
    """
    Recalculate most common category, location, and project for a specific payee.
    """
    payee = db.query(Payee).filter(Payee.id == payee_id).first()
    if not payee:
        raise HTTPException(status_code=404, detail="Payee not found")

    # Get all transactions for this payee
    transactions = db.query(Transaction).filter(Transaction.payee_id == payee_id).all()
    if not transactions:
        # Reset to None if no transactions
        payee.most_common_category_id = None
        payee.most_common_location_id = None
        payee.most_common_project_id = None
        payee.updated_at = datetime.utcnow()
        db.commit()
        return {"message": "Payee statistics reset (no transactions found)"}

    # Count occurrences
    category_counts = {}
    location_counts = {}
    project_counts = {}
    for trans in transactions:
        if trans.category_id:
            category_counts[trans.category_id] = category_counts.get(trans.category_id, 0) + 1
        if trans.location_id:
            location_counts[trans.location_id] = location_counts.get(trans.location_id, 0) + 1
        if trans.project_id:
            project_counts[trans.project_id] = project_counts.get(trans.project_id, 0) + 1

    # Get most common values
    payee.most_common_category_id = max(category_counts, key=category_counts.get) if category_counts else None
    payee.most_common_location_id = max(location_counts, key=location_counts.get) if location_counts else None
    payee.most_common_project_id = max(project_counts, key=project_counts.get) if project_counts else None
    payee.updated_at = datetime.utcnow()
    db.commit()
    return {
        "message": "Payee statistics recalculated successfully",
        "payee_id": payee_id,
        "most_common_category_id": payee.most_common_category_id,
        "most_common_location_id": payee.most_common_location_id,
        "most_common_project_id": payee.most_common_project_id,
        "transaction_count": len(transactions)
    }


@app.post("/payees/recalculate-all-stats")
def recalculate_all_payees_stats(db: Session = Depends(get_db)):
    """
    Recalculate statistics for all payees.
    This can be triggered from the 'Manage Payees' interface.
    """
    payees = db.query(Payee).all()
    updated_count = 0
    error_count = 0
    for payee in payees:
        try:
            # Get all transactions for this payee
            transactions = db.query(Transaction).filter(Transaction.payee_id == payee.id).all()
            if not transactions:
                payee.most_common_category_id = None
                payee.most_common_location_id = None
                payee.most_common_project_id = None
                payee.updated_at = datetime.utcnow()
                updated_count += 1
                continue

            # Count occurrences
            category_counts = {}
            location_counts = {}
            project_counts = {}
            for trans in transactions:
                if trans.category_id:
                    category_counts[trans.category_id] = category_counts.get(trans.category_id, 0) + 1
                if trans.location_id:
                    location_counts[trans.location_id] = location_counts.get(trans.location_id, 0) + 1
                if trans.project_id:
                    project_counts[trans.project_id] = project_counts.get(trans.project_id, 0) + 1

            # Get most common values
            payee.most_common_category_id = max(category_counts, key=category_counts.get) if category_counts else None
            payee.most_common_location_id = max(location_counts, key=location_counts.get) if location_counts else None
            payee.most_common_project_id = max(project_counts, key=project_counts.get) if project_counts else None
            payee.updated_at = datetime.utcnow()
            updated_count += 1
        except Exception as e:
            print(f"Error updating payee {payee.id}: {str(e)}")
            error_count += 1

    db.commit()
    return {
        "message": "All payee statistics recalculated",
        "total_payees": len(payees),
        "updated": updated_count,
        "errors": error_count
    }


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


@app.patch("/accounts/{account_id}/close")
def close_account(
    account_id: int,
    db: Session = Depends(get_db)
):
    """
    Close an account. Closed accounts won't appear in dropdowns or active account lists,
    but all historical data is preserved.
    """
    db_account = db.query(models.Account).filter(models.Account.id == account_id).first()
    if not db_account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Check if balance is approximately zero (accounting for floating point errors)
    if abs(db_account.current_balance) > 0.01:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot close account with non-zero balance: {db_account.current_balance}"
        )

    db_account.is_active = 0
    db.commit()
    db.refresh(db_account)
    return {
        "message": f"Account '{db_account.name}' closed successfully",
        "account": db_account
    }


@app.patch("/accounts/{account_id}/open")
def open_account(
    account_id: int,
    db: Session = Depends(get_db)
):
    """
    Reopen a previously closed account.
    """
    db_account = db.query(models.Account).filter(models.Account.id == account_id).first()
    if not db_account:
        raise HTTPException(status_code=404, detail="Account not found")

    db_account.is_active = 1
    db.commit()
    db.refresh(db_account)
    return {
        "message": f"Account '{db_account.name}' reopened successfully",
        "account": db_account
    }


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

# @app.get("/transactions", response_model=List[schemas.TransactionWithDetails])
# def get_transactions(
#     skip: int = 0,
#     limit: int = 200,
#     account_id: Optional[int] = None,
#     category_id: Optional[int] = None,
#     payee_id: Optional[int] = None,
#     location_id: Optional[int] = None,
#     project_id: Optional[int] = None,
#     currency: Optional[str] = None,
#     start_date: Optional[date] = None,
#     end_date: Optional[date] = None,
#     db: Session = Depends(get_db)
# ):
#     """
#     Retrieve transactions with optional filters. Returns enriched transactions with entity names.
#     """
#     query = db.query(models.Transaction)

#     # Apply filters
#     if account_id:
#         query = query.filter(models.Transaction.account_id == account_id)
#     if category_id:
#         query = query.filter(models.Transaction.category_id == category_id)
#     if payee_id:
#         query = query.filter(models.Transaction.payee_id == payee_id)
#     if location_id:
#         query = query.filter(models.Transaction.location_id == location_id)
#     if project_id:
#         query = query.filter(models.Transaction.project_id == project_id)
#     if currency:
#         query = query.filter(models.Transaction.currency == currency)
#     if start_date:
#         query = query.filter(models.Transaction.date >= datetime.combine(start_date, time.min))
#     if end_date:
#         query = query.filter(models.Transaction.date <= datetime.combine(end_date, time.max))

#     # Order by date descending (most recent first)
#     transactions = query.order_by(models.Transaction.date.desc()).offset(skip).limit(limit).all()

#     # Enrich with entity names
#     enriched_transactions = []
#     for trans in transactions:
#         trans_dict = {
#             "id": trans.id,
#             "date": trans.date,
#             "amount": trans.amount,
#             "currency": trans.currency,
#             "note": trans.note,
#             "account_id": trans.account_id,
#             "category_id": trans.category_id,
#             "payee_id": trans.payee_id,
#             "location_id": trans.location_id,
#             "project_id": trans.project_id,
#             "account_balance_after": trans.account_balance_after,
#             "total_balance_after": trans.total_balance_after,
#             "created_at": trans.created_at,
#             "updated_at": trans.updated_at,
#             "account_name": trans.account.name if trans.account else None,
#             "category_name": trans.category.name if trans.category else None,
#             "payee_name": trans.payee.name if trans.payee else None,
#             "location_name": trans.location.name if trans.location else None,
#             "project_name": trans.project.name if trans.project else None,
#         }
#         enriched_transactions.append(trans_dict)

#     return enriched_transactions


from typing import List
from sqlalchemy.orm import joinedload
from sqlalchemy import or_

@app.get("/transactions", response_model=List[schemas.TransactionWithDetails])
def get_transactions(
    skip: int = 0,
    limit: int = 200,
    account_id: Optional[int] = None,
    category_id: Optional[int] = None,
    payee_id: Optional[int] = None,
    location_id: Optional[int] = None,
    project_id: Optional[int] = None,
    currency: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    search: Optional[str] = None,                # nuevo: búsqueda de texto (payee.name o note)
    db: Session = Depends(get_db)
):
    """
    Retrieve transactions with optional filters. Returns enriched transactions with entity names.

    - Usa joinedload(...) para evitar N+1.
    - Soporta paginación con skip & limit (útil para scroll infinito).
    - `search` aplica búsqueda en payee.name y transaction.note en el backend.
    """
    # Base query
    query = db.query(models.Transaction)

    # Apply filters (same logic as antes)
    if account_id:
        query = query.filter(models.Transaction.account_id == account_id)
    if category_id:
        query = query.filter(models.Transaction.category_id == category_id)
    if payee_id:
        query = query.filter(models.Transaction.payee_id == payee_id)
    if location_id:
        query = query.filter(models.Transaction.location_id == location_id)
    if project_id:
        query = query.filter(models.Transaction.project_id == project_id)
    if currency:
        query = query.filter(models.Transaction.currency == currency)
    if start_date:
        query = query.filter(models.Transaction.date >= datetime.combine(start_date, time.min))
    if end_date:
        query = query.filter(models.Transaction.date <= datetime.combine(end_date, time.max))

    # Search (backend) - only if provided
    if search:
        # Use case-insensitive LIKE for payee name and note.
        # Note: on SQLite .ilike behaves like LIKE (case-insensitive depending on collation).
        search_pattern = f"%{search}%"
        # If we want to filter by payee name we should join Payee (left outer join).
        query = query.outerjoin(models.Payee).filter(
            or_(
                models.Payee.name.ilike(search_pattern),
                models.Transaction.note.ilike(search_pattern)
            )
        )

    # Avoid N+1: eager-load related objects that you later access (account, category, payee, location, project)
    query = query.options(
        joinedload(models.Transaction.account),
        joinedload(models.Transaction.category),
        joinedload(models.Transaction.payee),
        joinedload(models.Transaction.location),
        joinedload(models.Transaction.project),
    )

    # Order by date descending (most recent first), then by id to make ordering deterministic
    query = query.order_by(models.Transaction.date.desc(), models.Transaction.id.desc())

    # Pagination (offset/limit)
    transactions = query.offset(skip).limit(limit).all()

    # Enrich with entity names (no extra queries because of joinedload)
    enriched_transactions = []
    for trans in transactions:
        # Safety check: skip None transactions (corrupted data)
        if trans is None:
            print("WARNING: Found None transaction in get_transactions query")
            continue
            
        trans_dict = {
            "id": trans.id,
            "date": trans.date.isoformat() if hasattr(trans.date, "isoformat") else str(trans.date),
            "amount": float(trans.amount) if trans.amount is not None else None,
            "currency": trans.currency,
            "note": trans.note,
            "account_id": trans.account_id,
            "category_id": trans.category_id,
            "payee_id": trans.payee_id,
            "location_id": trans.location_id,
            "project_id": trans.project_id,
            "account_balance_after": trans.account_balance_after,
            "total_balance_after": trans.total_balance_after,
            "created_at": trans.created_at.isoformat() if hasattr(trans.created_at, "isoformat") else trans.created_at,
            "updated_at": trans.updated_at.isoformat() if hasattr(trans.updated_at, "isoformat") else trans.updated_at,
            "account_name": trans.account.name if trans.account else None,
            "category_name": trans.category.name if trans.category else None,
            "payee_name": trans.payee.name if trans.payee else None,
            "location_name": trans.location.name if trans.location else None,
            "project_name": trans.project.name if trans.project else None,
        }
        enriched_transactions.append(trans_dict)

    return enriched_transactions



@app.get("/transactions/transfers")
def get_transfers(
    skip: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db)
):
    """
    Get transfer transactions grouped together.
    Identifies Transfer In/Out pairs and groups them.
    Supports pagination with skip & limit.
    
    Optimized: Uses O(n) algorithm with hash map instead of O(n²) nested loop.
    """

    # Get all transactions with Transfer locations
    transfer_in_location = db.query(models.Location).filter(
        models.Location.name == "Transfer In"
    ).first()
    transfer_out_location = db.query(models.Location).filter(
        models.Location.name == "Transfer Out"
    ).first()

    if not transfer_in_location or not transfer_out_location:
        return []

    # Get all transfer transactions with eager loading
    transfers = db.query(models.Transaction).options(
        joinedload(models.Transaction.account)
    ).filter(
        or_(
            models.Transaction.location_id == transfer_in_location.id,
            models.Transaction.location_id == transfer_out_location.id
        )
    ).order_by(models.Transaction.date.desc()).all()

    # O(n) optimization: separate into ins and outs, index by date
    transfers_in = []
    transfers_out = []
    
    for trans in transfers:
        if trans.location_id == transfer_in_location.id:
            transfers_in.append(trans)
        else:
            transfers_out.append(trans)
    
    # Index transfers_in by date for O(1) lookup
    transfers_in_by_date = {}
    for trans in transfers_in:
        date_key = str(trans.date)
        if date_key not in transfers_in_by_date:
            transfers_in_by_date[date_key] = []
        transfers_in_by_date[date_key].append(trans)
    
    # Match transfers - O(n) instead of O(n²)
    grouped_transfers = []
    processed_ids = set()

    for trans_out in transfers_out:
        if trans_out.id in processed_ids:
            continue
        
        date_key = str(trans_out.date)
        candidates = transfers_in_by_date.get(date_key, [])
        
        # Find matching transfer_in (same date, different account, not yet processed)
        matching = None
        for trans_in in candidates:
            if trans_in.id not in processed_ids and trans_in.account_id != trans_out.account_id:
                matching = trans_in
                break
        
        if matching:
            grouped_transfers.append({
                "id": f"transfer_{trans_out.id}_{matching.id}",
                "date": date_key,
                "from_account_id": trans_out.account_id,
                "from_account_name": trans_out.account.name if trans_out.account else None,
                "from_amount": abs(trans_out.amount),
                "from_currency": trans_out.currency,
                "to_account_id": matching.account_id,
                "to_account_name": matching.account.name if matching.account else None,
                "to_amount": matching.amount,
                "to_currency": matching.currency,
                "note": trans_out.note or matching.note,
                "transfer_out_id": trans_out.id,
                "transfer_in_id": matching.id
            })
            
            processed_ids.add(trans_out.id)
            processed_ids.add(matching.id)

    # Apply pagination to the grouped transfers
    return grouped_transfers[skip:skip + limit]


@app.get("/transactions/{transaction_id}", response_model=schemas.TransactionResponse)
def get_transaction(
    transaction_id: int,
    db: Session = Depends(get_db)
):
    """
    Retrieve a specific transaction by ID.
    """
    transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id
    ).first()
    if not transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return transaction



@app.post("/transactions/check-duplicate")
def check_duplicate_transaction(
    duplicate_check: schemas.DuplicateCheck,
    db: Session = Depends(get_db)
):
    """
    Check if a transaction with the same date (day), amount, and account already exists.
    This is used during CSV import to detect duplicates.
    
    Args:
        duplicate_check: DuplicateCheck model with date, amount, and account_id
    
    Returns:
        {"exists": bool}
    """
    try:
        # Parse the date string to get just the date part
        if 'T' in duplicate_check.date:
            date_part = duplicate_check.date.split('T')[0]
        else:
            date_part = duplicate_check.date
        
        # Parse to datetime to ensure it's valid
        parsed_date = datetime.fromisoformat(date_part)
        
        # Query for any transaction on the same day, same account, same amount
        # We use func.date() to compare only the date part, ignoring time
        exists = db.query(Transaction).filter(
            func.date(Transaction.date) == parsed_date.date(),
            Transaction.amount == duplicate_check.amount,
            Transaction.account_id == duplicate_check.account_id
        ).first() is not None
        
        return {"exists": exists}
        
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking duplicate: {str(e)}")


@app.post("/transactions/check-duplicates-batch")
def check_duplicates_batch(
    transactions: List[schemas.DuplicateCheck],
    db: Session = Depends(get_db)
):
    """
    Check multiple transactions for duplicates in a single request.
    Much more efficient than checking one by one during CSV import.
    
    Args:
        transactions: List of DuplicateCheck objects with date, amount, and account_id
    
    Returns:
        List of booleans indicating whether each transaction is a duplicate
    """
    try:
        if not transactions:
            return {"duplicates": []}
        
        # Get unique account IDs
        account_ids = list(set(t.account_id for t in transactions))
        
        # Parse all dates and find date range
        parsed_dates = []
        for t in transactions:
            date_part = t.date.split('T')[0] if 'T' in t.date else t.date
            parsed_dates.append(datetime.fromisoformat(date_part).date())
        
        min_date = min(parsed_dates)
        max_date = max(parsed_dates)
        
        # Fetch all existing transactions in that date range for those accounts (single query)
        # Get raw date string from SQLite and the other fields
        existing = db.query(
            Transaction.date,
            Transaction.amount,
            Transaction.account_id
        ).filter(
            Transaction.account_id.in_(account_ids),
            func.date(Transaction.date) >= min_date.isoformat(),
            func.date(Transaction.date) <= max_date.isoformat()
        ).all()
        
        # Build a set of (date_str, amount, account_id) tuples for O(1) lookup
        # Convert date to YYYY-MM-DD string format for consistent comparison
        existing_set = set()
        for tx in existing:
            # Handle both datetime objects and strings
            if hasattr(tx.date, 'date'):
                tx_date_str = tx.date.date().isoformat()
            elif hasattr(tx.date, 'isoformat'):
                tx_date_str = tx.date.isoformat()
            else:
                # It's already a string, extract date part
                tx_date_str = str(tx.date).split('T')[0].split(' ')[0]
            
            existing_set.add((tx_date_str, round(float(tx.amount), 2), tx.account_id))
        
        # Check each transaction against the set
        results = []
        for i, t in enumerate(transactions):
            tx_date_str = parsed_dates[i].isoformat()
            tx_amount = round(float(t.amount), 2)
            is_duplicate = (tx_date_str, tx_amount, t.account_id) in existing_set
            results.append(is_duplicate)
        
        return {"duplicates": results}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error checking duplicates: {str(e)}")


@app.post("/transactions/batch")
def create_transactions_batch(
    transactions: List[schemas.TransactionCreate],
    db: Session = Depends(get_db)
):
    """
    Create multiple transactions in a single request.
    Balances are NOT recalculated here - call /admin/initialise-balances after.
    
    Args:
        transactions: List of TransactionCreate objects
    
    Returns:
        Summary of created transactions
    """
    try:
        created_count = 0
        errors = []
        
        for i, trans in enumerate(transactions):
            # Validate required fields
            if trans.amount is None or trans.date is None or trans.account_id is None:
                errors.append({"index": i, "error": "Missing required fields"})
                continue
            
            try:
                db_transaction = models.Transaction(**trans.dict())
                db.add(db_transaction)
                created_count += 1
            except Exception as e:
                errors.append({"index": i, "error": str(e)})
        
        # Commit all at once
        db.commit()
        
        return {
            "created": created_count,
            "errors": errors,
            "total_submitted": len(transactions)
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Batch creation failed: {str(e)}")


@app.post("/transactions", response_model=schemas.TransactionResponse)
def create_transaction(
    transaction: schemas.TransactionCreate,
    skip_recalculation: bool = Query(False),
    db: Session = Depends(get_db)
):
    """
    Create a new transaction and update account balance.
    STRICT VALIDATION ADDED to prevent database corruption.
    """
    # 1. VALIDATION: Ensure critical fields are not None
    if transaction.amount is None:
        raise HTTPException(status_code=400, detail="Transaction amount cannot be None/Null")
    
    if transaction.date is None:
        raise HTTPException(status_code=400, detail="Transaction date cannot be None")

    if transaction.account_id is None:
        raise HTTPException(status_code=400, detail="Transaction must be linked to an Account")

    # 2. CREATE: Only proceed if validation passes
    try:
        db_transaction = models.Transaction(**transaction.dict())
        db.add(db_transaction)
        db.flush()  # Get the ID without committing
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error during insert: {str(e)}")

    # 3. RECALCULATE
    if not skip_recalculation:
        try:
            recalculate_balances_from_transaction(db, db_transaction.id)
            db.commit()  # Commit después de recalcular
        except Exception as e:
            # If calculation fails, we MUST rollback the transaction so we don't save bad data
            db.rollback()
            print(f"CRITICAL: Calculation failed, rolled back transaction. Error: {e}")
            raise HTTPException(status_code=500, detail=f"Calculation error: {str(e)}")
    else:
        db.commit()

    db.refresh(db_transaction)
    return db_transaction


@app.put("/transactions/{transaction_id}", response_model=schemas.TransactionResponse)
def update_transaction(
    transaction_id: int,
    transaction: schemas.TransactionCreate,
    db: Session = Depends(get_db)
):
    """
    Update an existing transaction.
    """
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id
    ).first()
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Store old values
    old_account_id = db_transaction.account_id
    old_date = db_transaction.date

    # Update transaction fields
    for key, value in transaction.dict().items():
        setattr(db_transaction, key, value)
    db_transaction.updated_at = datetime.utcnow()

    # NO hacer commit aquí - se hará después del recálculo
    db.flush()  # Flush para que los cambios estén disponibles para las queries siguientes

    # Recalculate balances from the EARLIEST date for both accounts
    affected_account_ids = list(set([old_account_id, transaction.account_id]))
    earliest_date = min(old_date, db_transaction.date)
    
    # Find a transaction at or before the earliest date to use as trigger
    trigger_transaction = db.query(models.Transaction).filter(
        models.Transaction.date <= earliest_date
    ).order_by(models.Transaction.date.desc(), models.Transaction.id.desc()).first()
    
    if trigger_transaction:
        recalculate_balances_from_transaction(db, trigger_transaction.id, affected_account_ids)
        db.commit()
    else:
        initialise_all_balances(db)
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
    db_transaction = db.query(models.Transaction).filter(
        models.Transaction.id == transaction_id
    ).first()
    if not db_transaction:
        raise HTTPException(status_code=404, detail="Transaction not found")

    # Store the account ID and date before deleting
    affected_account_id = db_transaction.account_id
    transaction_date = db_transaction.date

    # Delete the transaction
    db.delete(db_transaction)
    db.flush()  # Flush en lugar de commit para mantener la transacción abierta

    # Find the next transaction after the deleted one to trigger recalculation
    next_transaction = db.query(models.Transaction).filter(
        models.Transaction.account_id == affected_account_id,
        models.Transaction.date >= transaction_date
    ).order_by(models.Transaction.date.asc(), models.Transaction.id.asc()).first()

    if next_transaction:
        recalculate_balances_from_transaction(db, next_transaction.id, [affected_account_id])
        db.commit()  # Commit después de recalcular
    else:
        # If no transactions remain for this account, reset current_balance
        account = db.query(models.Account).filter(
            models.Account.id == affected_account_id
        ).first()
        if account:
            account.current_balance = account.initial_balance
            db.commit()

    return {"message": "Transaction deleted successfully"}


@app.post("/transactions/batch-delete")
def delete_transactions_batch(
    transaction_ids: List[int],
    db: Session = Depends(get_db)
):
    """
    Delete multiple transactions in a single request and recalculate balances ONCE.
    Much more efficient than deleting one by one.
    
    Optimized: Only recalculates affected accounts, not all accounts.
    
    Args:
        transaction_ids: List of transaction IDs to delete
    
    Returns:
        Summary of deleted transactions
    """
    if not transaction_ids:
        return {"deleted": 0, "message": "No transactions to delete"}
    
    # Collect affected accounts before deleting
    affected_accounts = set()
    deleted_count = 0
    not_found = []
    
    for tx_id in transaction_ids:
        tx = db.query(models.Transaction).filter(
            models.Transaction.id == tx_id
        ).first()
        
        if tx:
            affected_accounts.add(tx.account_id)
            db.delete(tx)
            deleted_count += 1
        else:
            not_found.append(tx_id)
    
    # Flush deletions before recalculating
    db.flush()
    
    # Recalculate balances ONLY for affected accounts (not all accounts)
    if affected_accounts:
        recalculate_balances_for_accounts(db, list(affected_accounts))
    
    db.commit()
    
    result = {
        "deleted": deleted_count,
        "affected_accounts": list(affected_accounts),
        "message": f"Successfully deleted {deleted_count} transactions"
    }
    
    if not_found:
        result["not_found"] = not_found
    
    return result


from pydantic import BaseModel

class BatchUpdateItem(BaseModel):
    """Schema for a single transaction update in batch operations."""
    id: int
    updates: dict  # Fields to update (e.g., {"category_id": 5, "payee_id": 10})

class BatchUpdateRequest(BaseModel):
    """Schema for batch update request."""
    transactions: List[BatchUpdateItem]


@app.put("/transactions/batch-update")
def update_transactions_batch(
    request: BatchUpdateRequest,
    db: Session = Depends(get_db)
):
    """
    Update multiple transactions in a single request and recalculate balances ONCE.
    Much more efficient than updating one by one.
    
    Optimized: Only recalculates affected accounts, not all accounts.
    
    Args:
        request: BatchUpdateRequest with list of {id, updates} objects
    
    Returns:
        Summary of updated transactions
    """
    if not request.transactions:
        return {"updated": 0, "message": "No transactions to update"}
    
    # Collect affected accounts and track earliest date for recalculation
    affected_accounts = set()
    updated_count = 0
    not_found = []
    errors = []
    
    # Allowed fields that can be updated
    allowed_fields = {
        'amount', 'currency', 'note', 'account_id', 'category_id', 
        'payee_id', 'location_id', 'project_id', 'date'
    }
    
    # Fields that affect balance calculation
    balance_affecting_fields = {'amount', 'account_id', 'date'}
    needs_balance_recalc = False
    
    for item in request.transactions:
        tx = db.query(models.Transaction).filter(
            models.Transaction.id == item.id
        ).first()
        
        if not tx:
            not_found.append(item.id)
            continue
        
        try:
            # Track original account for balance recalculation
            affected_accounts.add(tx.account_id)
            
            # Apply updates (only allowed fields)
            for field, value in item.updates.items():
                if field in allowed_fields:
                    # Check if this affects balances
                    if field in balance_affecting_fields:
                        needs_balance_recalc = True
                    
                    setattr(tx, field, value)
                    
                    # If account changed, track new account too
                    if field == 'account_id' and value:
                        affected_accounts.add(value)
            
            tx.updated_at = datetime.utcnow()
            updated_count += 1
            
        except Exception as e:
            errors.append({"id": item.id, "error": str(e)})
    
    # Flush all updates before recalculating
    db.flush()
    
    # Recalculate balances ONLY for affected accounts (not all accounts)
    if affected_accounts and needs_balance_recalc:
        recalculate_balances_for_accounts(db, list(affected_accounts))
    
    db.commit()
    
    result = {
        "updated": updated_count,
        "affected_accounts": list(affected_accounts),
        "message": f"Successfully updated {updated_count} transactions"
    }
    
    if not_found:
        result["not_found"] = not_found
    if errors:
        result["errors"] = errors
    
    return result


def recalculate_balances_for_accounts(db: Session, account_ids: List[int]):
    """
    Recalculate balances only for specific accounts.
    Much faster than recalculating all accounts when only a few are affected.
    """
    for account_id in account_ids:
        account = db.query(models.Account).filter(
            models.Account.id == account_id
        ).first()
        
        if not account:
            continue
        
        # Get all transactions for this account ordered by date and id
        transactions = db.query(models.Transaction).filter(
            models.Transaction.account_id == account_id
        ).order_by(
            models.Transaction.date.asc(), 
            models.Transaction.id.asc()
        ).all()
        
        # Calculate running balance
        running_balance = float(account.initial_balance) if account.initial_balance else 0.0
        
        for tx in transactions:
            if tx.amount is not None:
                running_balance += float(tx.amount)
            tx.account_balance_after = round(running_balance, 2)
        
        # Update account's current balance
        account.current_balance = round(running_balance, 2)


class RecalculateBalancesRequest(BaseModel):
    """Schema for recalculate balances request."""
    account_ids: List[int]


@app.post("/admin/recalculate-balances-for-accounts")
def recalculate_balances_for_accounts_endpoint(
    request: RecalculateBalancesRequest,
    db: Session = Depends(get_db)
):
    """
    Recalculate balances for specific accounts.
    Useful after batch entry of transactions/transfers.
    
    Args:
        request: Object containing list of account IDs to recalculate
    
    Returns:
        Summary of recalculated accounts
    """
    if not request.account_ids:
        return {"message": "No accounts to recalculate", "accounts_processed": 0}
    
    try:
        recalculate_balances_for_accounts(db, request.account_ids)
        db.commit()
        
        return {
            "message": "Balances recalculated successfully",
            "accounts_processed": len(request.account_ids),
            "account_ids": request.account_ids
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error recalculating balances: {str(e)}")


@app.get("/transactions/batch")
def get_transactions_batch(
    ids: str = Query(..., description="Comma-separated list of transaction IDs"),
    db: Session = Depends(get_db)
):
    """
    Get multiple transactions by IDs in a single request.
    Much more efficient than fetching one by one.
    
    Args:
        ids: Comma-separated string of transaction IDs (e.g., "1,2,3,4,5")
    
    Returns:
        List of transactions
    """
    try:
        transaction_ids = [int(id.strip()) for id in ids.split(',') if id.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid ID format. Use comma-separated integers.")
    
    if not transaction_ids:
        return []
    
    # Fetch all transactions in one query with eager loading
    transactions = db.query(models.Transaction).options(
        joinedload(models.Transaction.account),
        joinedload(models.Transaction.category),
        joinedload(models.Transaction.payee),
        joinedload(models.Transaction.location),
        joinedload(models.Transaction.project),
    ).filter(
        models.Transaction.id.in_(transaction_ids)
    ).all()
    
    # Build response maintaining order of requested IDs
    tx_map = {tx.id: tx for tx in transactions}
    result = []
    
    for tx_id in transaction_ids:
        tx = tx_map.get(tx_id)
        if tx:
            result.append({
                "id": tx.id,
                "date": tx.date.isoformat() if hasattr(tx.date, "isoformat") else str(tx.date),
                "amount": float(tx.amount) if tx.amount is not None else None,
                "currency": tx.currency,
                "note": tx.note,
                "account_id": tx.account_id,
                "category_id": tx.category_id,
                "payee_id": tx.payee_id,
                "location_id": tx.location_id,
                "project_id": tx.project_id,
                "account_balance_after": tx.account_balance_after,
                "account_name": tx.account.name if tx.account else None,
                "category_name": tx.category.name if tx.category else None,
                "payee_name": tx.payee.name if tx.payee else None,
                "location_name": tx.location.name if tx.location else None,
                "project_name": tx.project.name if tx.project else None,
            })
    
    return result


# ============================================
# TRANSFER ENDPOINTS
# ============================================

@app.post("/transactions/transfers")
def create_transfer(
    transfer: schemas.TransferCreate,
    skip_recalculation: bool = Query(False),
    db: Session = Depends(get_db)
):
    """
    Create a transfer between two accounts.
    Creates two transactions: one outgoing and one incoming.
    
    Args:
        transfer: Transfer details
        skip_recalculation: If True, skip balance recalculation (useful for batch entry)
    """
    # Get or create Transfer In and Transfer Out locations
    transfer_in_loc = db.query(models.Location).filter(
        models.Location.name == "Transfer In"
    ).first()
    if not transfer_in_loc:
        transfer_in_loc = models.Location(name="Transfer In")
        db.add(transfer_in_loc)
        db.flush()

    transfer_out_loc = db.query(models.Location).filter(
        models.Location.name == "Transfer Out"
    ).first()
    if not transfer_out_loc:
        transfer_out_loc = models.Location(name="Transfer Out")
        db.add(transfer_out_loc)
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

    # Recalculate balances for both accounts (unless skipped for batch mode)
    if not skip_recalculation:
        # Use the earlier transaction ID to start recalculation
        earlier_transaction_id = min(transaction_out.id, transaction_in.id)
        recalculate_balances_from_transaction(
            db,
            earlier_transaction_id,
            [transfer.from_account_id, transfer.to_account_id]
        )
    
    db.commit()

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
        from backend.update_exchange_rates import update_exchange_rates  # ← Así
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
# HELPER FUNCTIONS FOR HISTORICAL RATES
# ============================================

def _to_date(value):
    """Convert any date-like value to datetime.date"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value)).date()
    except Exception:
        return None


def _as_datetime_floor(d):
    """Return datetime at 00:00:00 for a given date"""
    if isinstance(d, datetime):
        return d
    if isinstance(d, date):
        return datetime.combine(d, time.min)
    return None


def _as_datetime_ceil(d):
    """Return datetime at 23:59:59.999999 for a given date"""
    if isinstance(d, datetime):
        return d
    if isinstance(d, date):
        return datetime.combine(d, time.max)
    return None



# ============================================
# DASHBOARD / STATISTICS
# ============================================

@app.get("/dashboard/summary")
def get_dashboard_summary(db: Session = Depends(get_db)):
    """
    Get summary statistics for the dashboard with currency conversion.
    Uses LATEST exchange rates (this is correct for current total balance).
    """
    # Get latest exchange rates
    rates_dict = get_latest_rates(db)

    # Find most common currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()

    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    base_rate = rates_dict.get(base_currency, 1.0)

    # Currency conversion expression
    conversion_cases = []
    for currency, rate in rates_dict.items():
        conversion_factor = base_rate / rate
        conversion_cases.append(
            (Transaction.currency == currency, Transaction.amount * conversion_factor)
        )
    conversion_expression = case(
        *conversion_cases,
        else_=Transaction.amount
    )

    total_balance_converted = db.query(
        sql_func.sum(conversion_expression)
    ).scalar() or 0

    # Count queries
    total_transactions = db.query(sql_func.count(Transaction.id)).scalar()
    total_accounts = db.query(sql_func.count(Account.id)).filter(
        Account.is_active == 1
    ).scalar()
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
# OPTIMISED DASHBOARD ENDPOINTS
# ============================================

@app.get("/dashboard/networth/{period}")
def get_networth_evolution(
    period: str,
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    excluded_accounts: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Get net worth evolution with HISTORICAL exchange rates.
    Each transaction uses the exchange rate from its transaction date.
    """
    # Parse excluded accounts
    excluded_ids = []
    if excluded_accounts:
        excluded_ids = [int(id) for id in excluded_accounts.split(',') if id.strip().isdigit()]

    # Build query filters
    filters = []
    if excluded_ids:
        filters.append(~Transaction.account_id.in_(excluded_ids))
    if date_from:
        filters.append(Transaction.date >= _as_datetime_floor(date_from))
    if date_to:
        filters.append(Transaction.date <= _as_datetime_ceil(date_to))

    # Get all transactions in range
    query = db.query(Transaction)
    if filters:
        query = query.filter(and_(*filters))
    transactions = query.order_by(Transaction.date).all()

    if not transactions:
        return {
            "data_points": [],
            "summary": {
                "initial_balance": 0, "current_balance": 0, "total_change": 0,
                "percentage_change": 0, "peak_balance": 0, "peak_date": None,
                "lowest_balance": 0, "lowest_date": None
            },
            "base_currency": "GBP"
        }

    # Determine date range for exchange rates
    min_trans_date = _to_date(transactions[0].date)
    max_trans_date = _to_date(transactions[-1].date)
    
    if date_from and _to_date(date_from) < min_trans_date:
        min_trans_date = _to_date(date_from)

    # Get all currencies used
    currencies_query = db.query(Transaction.currency).distinct()
    if filters:
        currencies_query = currencies_query.filter(and_(*filters))
    currencies = [c[0] for c in currencies_query.all() if c[0]]

    # Add account currencies for baseline calculation
    if date_from:
        account_currencies = db.query(Account.currency).distinct().all()
        for c in account_currencies:
            if c[0] and c[0] not in currencies:
                currencies.append(c[0])

    # Load historical exchange rates (BULK)
    historical_rates = get_rates_bulk(db, currencies, min_trans_date, max_trans_date)

    # Determine base currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()
    
    base_currency = currency_counts[0][0] if currency_counts else "GBP"

    # Calculate baseline balances
    account_balances = {}
    all_balance_points = []

    if date_from:
        accounts_q = db.query(Account).filter(Account.is_active == 1)
        if excluded_ids:
            accounts_q = accounts_q.filter(~Account.id.in_(excluded_ids))
        accounts = accounts_q.all()

        baseline_date = _to_date(date_from)
        baseline_rates = historical_rates.get(baseline_date, {'GBP': 1.0})
        total_baseline = 0.0

        for acc in accounts:
            last_tx = db.query(Transaction).filter(
                Transaction.account_id == acc.id,
                Transaction.date < _as_datetime_floor(date_from)
            ).order_by(Transaction.date.desc(), Transaction.id.desc()).first()

            if last_tx and last_tx.account_balance_after is not None:
                baseline_native = last_tx.account_balance_after
            else:
                baseline_native = acc.initial_balance or 0

            acc_rate = baseline_rates.get(acc.currency, 1.0)
            base_rate = baseline_rates.get(base_currency, 1.0)
            baseline_converted = baseline_native * (base_rate / acc_rate)

            account_balances[acc.id] = baseline_converted
            total_baseline += baseline_converted

        all_balance_points.append({
            'date': baseline_date,
            'balance': round(total_baseline, 2)
        })

    # Process transactions with HISTORICAL rates
    for trans in transactions:
        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})
        
        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted_amount = trans.amount * (base_rate / trans_rate)

        if trans.account_id not in account_balances:
            account_balances[trans.account_id] = 0.0
        account_balances[trans.account_id] += converted_amount

        total_balance = sum(account_balances.values())
        all_balance_points.append({
            'date': trans_date,
            'balance': round(total_balance, 2)
        })

    # Aggregate by period
    aggregated_data = []
    if period == "monthly":
        monthly_data = {}
        for point in all_balance_points:
            if point['date'] is None:
                continue
            month_key = point['date'].strftime('%Y-%m')
            if month_key not in monthly_data or point['date'] > monthly_data[month_key]['date']:
                monthly_data[month_key] = point
        aggregated_data = sorted(monthly_data.values(), key=lambda x: x['date'])
    elif period == "weekly":
        weekly_data = {}
        for point in all_balance_points:
            if point['date'] is None:
                continue
            week_start = point['date'] - timedelta(days=point['date'].weekday())
            week_key = week_start.strftime('%Y-%m-%d')
            if week_key not in weekly_data or point['date'] > weekly_data[week_key]['date']:
                weekly_data[week_key] = point
        aggregated_data = sorted(weekly_data.values(), key=lambda x: x['date'])
    else:  # daily
        daily_data = {}
        for point in all_balance_points:
            if point['date'] is None:
                continue
            day_key = point['date'].strftime('%Y-%m-%d')
            if day_key not in daily_data:
                daily_data[day_key] = point
        aggregated_data = sorted(daily_data.values(), key=lambda x: x['date'])

    # Summary statistics
    if aggregated_data:
        initial_balance = aggregated_data[0]['balance']
        current_balance = aggregated_data[-1]['balance']
        total_change = current_balance - initial_balance
        percentage_change = ((total_change / abs(initial_balance)) * 100) if initial_balance != 0 else 0

        balances = [p['balance'] for p in aggregated_data]
        peak_balance = max(balances)
        lowest_balance = min(balances)
        peak_idx = balances.index(peak_balance)
        lowest_idx = balances.index(lowest_balance)

        summary = {
            "initial_balance": round(initial_balance, 2),
            "current_balance": round(current_balance, 2),
            "total_change": round(total_change, 2),
            "percentage_change": round(percentage_change, 2),
            "peak_balance": round(peak_balance, 2),
            "peak_date": aggregated_data[peak_idx]['date'].isoformat(),
            "lowest_balance": round(lowest_balance, 2),
            "lowest_date": aggregated_data[lowest_idx]['date'].isoformat()
        }
    else:
        summary = {
            "initial_balance": 0, "current_balance": 0, "total_change": 0,
            "percentage_change": 0, "peak_balance": 0, "peak_date": None,
            "lowest_balance": 0, "lowest_date": None
        }

    return {
        "data_points": [
            {'date': point['date'].isoformat(), 'balance': point['balance']}
            for point in aggregated_data
        ],
        "summary": summary,
        "base_currency": base_currency
    }

# ============================================
# DASHBOARD ENDPOINTS (categories, yearly, top payees/locations)
# ============================================

@app.get("/dashboard/categories/{period}")
def get_categories_evolution(
    period: str,
    category_ids: str = Query(...),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Get category spending evolution with historical exchange rates.
    Includes all periods in range, even those with zero spending.
    """
    cat_ids = [int(x) for x in category_ids.split(',') if x.strip().isdigit()]
    if not cat_ids:
        return {"periods": [], "categories": {}}

    filters = [Transaction.category_id.in_(cat_ids)]
    if date_from:
        filters.append(Transaction.date >= _as_datetime_floor(date_from))
    if date_to:
        filters.append(Transaction.date <= _as_datetime_ceil(date_to))

    transactions = db.query(Transaction).filter(and_(*filters)).order_by(Transaction.date).all()
    
    category_names = {}
    for cat_id in cat_ids:
        cat = db.query(Category).filter(Category.id == cat_id).first()
        if cat:
            category_names[cat_id] = cat.name

    if not category_names:
        return {"periods": [], "categories": {}}

    if date_from and date_to:
        min_date = date_from
        max_date = date_to
    elif transactions:
        min_date = _to_date(transactions[0].date) if not date_from else date_from
        max_date = _to_date(transactions[-1].date) if not date_to else date_to
    else:
        return {"periods": [], "categories": {cat_name: [] for cat_name in category_names.values()}}

    currencies = list(set([t.currency for t in transactions if t.currency])) if transactions else []
    historical_rates = get_rates_bulk(db, currencies, min_date, max_date) if currencies else {}
    base_currency = "GBP"

    # Generate all periods in range
    all_periods = []
    current_date = min_date
    
    if period == "monthly":
        current_date = date(min_date.year, min_date.month, 1)
        while current_date <= max_date:
            all_periods.append(current_date.strftime('%Y-%m'))
            if current_date.month == 12:
                current_date = date(current_date.year + 1, 1, 1)
            else:
                current_date = date(current_date.year, current_date.month + 1, 1)
    elif period == "weekly":
        current_date = min_date - timedelta(days=min_date.weekday())
        while current_date <= max_date:
            all_periods.append(current_date.strftime('%Y-%m-%d'))
            current_date += timedelta(days=7)
    else:
        while current_date <= max_date:
            all_periods.append(current_date.strftime('%Y-%m-%d'))
            current_date += timedelta(days=1)

    data_by_period = {p: {cat_name: 0.0 for cat_name in category_names.values()} for p in all_periods}
    
    for trans in transactions:
        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})
        
        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted = abs(trans.amount) * (base_rate / trans_rate)

        if period == "monthly":
            period_key = trans_date.strftime('%Y-%m')
        elif period == "weekly":
            week_start = trans_date - timedelta(days=trans_date.weekday())
            period_key = week_start.strftime('%Y-%m-%d')
        else:
            period_key = trans_date.strftime('%Y-%m-%d')

        cat_name = trans.category.name if trans.category else "Uncategorized"
        if period_key in data_by_period and cat_name in data_by_period[period_key]:
            data_by_period[period_key][cat_name] += converted

    categories = {cat_name: [] for cat_name in category_names.values()}
    
    for period_key in all_periods:
        for cat_name in categories:
            value = data_by_period[period_key].get(cat_name, 0)
            categories[cat_name].append(round(value, 2))

    return {
        "periods": all_periods,
        "categories": categories,
        "base_currency": base_currency
    }

@app.get("/dashboard/categories/breakdown/{year_month}")
def get_monthly_category_breakdown(
    year_month: str,
    view_mode: str = Query("top", description="View mode: 'top' for top expenses, 'category' for parent categories, 'subcategory' for full category names"),
    db: Session = Depends(get_db)
):
    """
    Get category breakdown for a month with historical exchange rates.
    """
    try:
        year, month = map(int, year_month.split('-'))
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid year_month format. Use YYYY-MM")

    transfer_ids = [
        r.id for r in db.query(Location.id)
        .filter(Location.name.in_(["Transfer In", "Transfer Out"]))
        .all()
    ]

    filters = [
        Transaction.date >= _as_datetime_floor(start_date),
        Transaction.date <= _as_datetime_ceil(end_date)
    ]
    if transfer_ids:
        filters.append(~Transaction.location_id.in_(transfer_ids))

    transactions = db.query(Transaction).filter(and_(*filters)).all()

    if not transactions:
        return {
            "month": year_month,
            "categories": [],
            "top_expenses": [],
            "summary": {
                "total_spent": 0,
                "num_categories": 0,
                "num_transactions": 0
            },
            "base_currency": "GBP"
        }

    currencies = list(set([t.currency for t in transactions if t.currency]))
    historical_rates = get_rates_bulk(db, currencies, start_date, end_date)
    base_currency = "GBP"

    category_data = {}
    total_income = 0
    total_expenses = 0
    all_expenses = []

    for trans in transactions:
        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})
        
        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted = trans.amount * (base_rate / trans_rate)

        if converted > 0:
            total_income += converted
        else:
            total_expenses += abs(converted)
            
            cat_name = trans.category.name if trans.category else "Uncategorised"
            parent_name = trans.category.parent if (trans.category and trans.category.parent) else cat_name
            
            all_expenses.append({
                "date": trans_date.isoformat(),
                "amount": abs(converted),
                "category": cat_name,
                "parent_category": parent_name,
                "payee": trans.payee.name if trans.payee else "Unknown",
                "note": trans.note
            })

            if view_mode == "category":
                cat_key = parent_name
            else:
                cat_key = cat_name

            if cat_key not in category_data:
                category_data[cat_key] = {
                    "name": cat_key,
                    "amount": 0,
                    "transaction_count": 0
                }
            
            category_data[cat_key]["amount"] += abs(converted)
            category_data[cat_key]["transaction_count"] += 1

    categories = sorted(category_data.values(), key=lambda x: x["amount"], reverse=True)[:20]

    for category in categories:
        category["percentage"] = round((category["amount"] / total_expenses * 100), 1) if total_expenses > 0 else 0
        category["amount"] = round(category["amount"], 2)

    top_expenses = sorted(all_expenses, key=lambda x: x["amount"], reverse=True)[:10]
    for expense in top_expenses:
        expense["amount"] = round(expense["amount"], 2)

    return {
        "month": year_month,
        "categories": categories,
        "top_expenses": top_expenses,
        "summary": {
            "total_spent": round(total_expenses, 2),
            "num_categories": len(categories),
            "num_transactions": sum(c["transaction_count"] for c in categories)
        },
        "base_currency": base_currency
    }

@app.get("/dashboard/yearly-summary")
def get_yearly_summary(
    year: Optional[int] = Query(None, description="Year to analyse (default: current year)"),
    db: Session = Depends(get_db)
):
    """
    Get yearly summary with month-by-month breakdown using historical exchange rates.
    """
    if not year:
        year = datetime.now().year

    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    transactions = db.query(Transaction).filter(
        and_(
            Transaction.date >= _as_datetime_floor(start_date),
            Transaction.date <= _as_datetime_ceil(end_date),
            or_(
                Transaction.location_id.is_(None),
                Transaction.location.has(Location.name.notin_(["Transfer In", "Transfer Out"]))
            )
        )
    ).all()

    if not transactions:
        months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        monthly_data = [
            {
                "month": months[i],
                "month_num": i+1,
                "income": 0,
                "expenses": 0,
                "net": 0
            }
            for i in range(12)
        ]
        return {
            "year": year,
            "monthly_data": monthly_data,
            "category_breakdown": [],
            "summary": {
                "total_income": 0,
                "total_expenses": 0,
                "net_savings": 0,
                "savings_rate": 0,
                "avg_monthly_income": 0,
                "avg_monthly_expenses": 0,
                "highest_expense_month": None,
                "highest_income_month": None
            },
            "base_currency": "GBP"
        }

    currencies = list(set([t.currency for t in transactions if t.currency]))
    historical_rates = get_rates_bulk(db, currencies, start_date, end_date)
    base_currency = "GBP"

    monthly_data_dict = {}
    category_totals = {}
    total_income = 0
    total_expenses = 0

    for trans in transactions:
        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})
        
        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted = trans.amount * (base_rate / trans_rate)

        month_num = trans_date.month
        
        if month_num not in monthly_data_dict:
            monthly_data_dict[month_num] = {"income": 0, "expenses": 0}

        if converted > 0:
            monthly_data_dict[month_num]["income"] += converted
            total_income += converted
        else:
            monthly_data_dict[month_num]["expenses"] += abs(converted)
            total_expenses += abs(converted)

            cat_name = trans.category.name if trans.category else "Uncategorised"
            if cat_name not in category_totals:
                category_totals[cat_name] = 0
            category_totals[cat_name] += abs(converted)

    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_data = []
    
    for i in range(1, 13):
        data = monthly_data_dict.get(i, {"income": 0, "expenses": 0})
        monthly_data.append({
            "month": months[i-1],
            "month_num": i,
            "income": round(data["income"], 2),
            "expenses": round(data["expenses"], 2),
            "net": round(data["income"] - data["expenses"], 2)
        })

    months_with_data = len([m for m in monthly_data if m['income'] > 0 or m['expenses'] > 0])
    avg_monthly_income = total_income / months_with_data if months_with_data > 0 else 0
    avg_monthly_expenses = total_expenses / months_with_data if months_with_data > 0 else 0

    category_breakdown = [
        {
            "name": name,
            "amount": round(amount, 2),
            "percentage": round((amount / total_expenses * 100), 1) if total_expenses > 0 else 0
        }
        for name, amount in sorted(category_totals.items(), key=lambda x: x[1], reverse=True)[:10]
    ]

    highest_expense_month = max(monthly_data, key=lambda x: x['expenses'])['month'] if monthly_data else None
    highest_income_month = max(monthly_data, key=lambda x: x['income'])['month'] if monthly_data else None

    return {
        "year": year,
        "monthly_data": monthly_data,
        "category_breakdown": category_breakdown,
        "summary": {
            "total_income": round(total_income, 2),
            "total_expenses": round(total_expenses, 2),
            "net_savings": round(total_income - total_expenses, 2),
            "savings_rate": round(((total_income - total_expenses) / total_income * 100), 1) if total_income > 0 else 0,
            "avg_monthly_income": round(avg_monthly_income, 2),
            "avg_monthly_expenses": round(avg_monthly_expenses, 2),
            "highest_expense_month": highest_expense_month,
            "highest_income_month": highest_income_month
        },
        "base_currency": base_currency
    }

@app.get("/dashboard/top-payees")
def get_top_payees(
    limit: int = Query(20),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Get top payees by spending with historical exchange rates.
    """
    filters = [Transaction.payee_id.isnot(None)]
    if date_from:
        filters.append(Transaction.date >= _as_datetime_floor(date_from))
    if date_to:
        filters.append(Transaction.date <= _as_datetime_ceil(date_to))

    transactions = db.query(Transaction).filter(and_(*filters)).all()

    if not transactions:
        return {"payees": [], "base_currency": "GBP"}

    min_date = _to_date(min(t.date for t in transactions))
    max_date = _to_date(max(t.date for t in transactions))

    currencies = list(set([t.currency for t in transactions if t.currency]))
    historical_rates = get_rates_bulk(db, currencies, min_date, max_date)
    base_currency = "GBP"

    payee_data = {}

    for trans in transactions:
        if trans.amount >= 0:
            continue

        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})
        
        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted = abs(trans.amount) * (base_rate / trans_rate)

        payee_id = trans.payee_id
        if payee_id not in payee_data:
            payee_data[payee_id] = {
                "name": trans.payee.name if trans.payee else "Unknown",
                "total_spent": 0,
                "transaction_count": 0,
                "most_common_category": None
            }

        payee_data[payee_id]["total_spent"] += converted
        payee_data[payee_id]["transaction_count"] += 1

    # Add most common category
    for payee_id, data in payee_data.items():
        payee = db.query(Payee).filter(Payee.id == payee_id).first()
        if payee and payee.most_common_category:
            data["most_common_category"] = payee.most_common_category.name

    # Sort and limit
    top_payees = sorted(payee_data.values(), key=lambda x: x["total_spent"], reverse=True)[:limit]

    return {
        "payees": [
            {
                "name": p["name"],
                "total_spent": round(p["total_spent"], 2),
                "transaction_count": p["transaction_count"],
                "most_common_category": p["most_common_category"]
            }
            for p in top_payees
        ],
        "base_currency": base_currency
    }

@app.get("/dashboard/top-locations")
def get_top_locations(
    limit: int = Query(20),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    db: Session = Depends(get_db)
):
    """
    Get top locations by spending with HISTORICAL exchange rates.
    """
    # Build filters
    filters = [Transaction.location_id.isnot(None)]
    if date_from:
        filters.append(Transaction.date >= _as_datetime_floor(date_from))
    if date_to:
        filters.append(Transaction.date <= _as_datetime_ceil(date_to))

    # Exclude transfer locations
    transfer_ids = [
        r.id for r in db.query(Location.id)
        .filter(Location.name.in_(["Transfer In", "Transfer Out"]))
        .all()
    ]
    if transfer_ids:
        filters.append(~Transaction.location_id.in_(transfer_ids))

    # Get transactions
    transactions = db.query(Transaction).filter(and_(*filters)).all()

    if not transactions:
        return {"locations": [], "base_currency": "GBP"}

    # Date range
    min_date = _to_date(min(t.date for t in transactions))
    max_date = _to_date(max(t.date for t in transactions))

    # Get currencies
    currencies = list(set([t.currency for t in transactions if t.currency]))

    # Load historical rates
    historical_rates = get_rates_bulk(db, currencies, min_date, max_date)
    base_currency = "GBP"

    # Aggregate by location
    location_data = {}

    for trans in transactions:
        if trans.amount >= 0:  # Skip income
            continue

        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})
        
        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted = abs(trans.amount) * (base_rate / trans_rate)

        location_id = trans.location_id
        if location_id not in location_data:
            location_data[location_id] = {
                "name": trans.location.name if trans.location else "Unknown",
                "total_spent": 0,
                "transaction_count": 0,
                "most_common_category": None,
                "categories": {}
            }

        location_data[location_id]["total_spent"] += converted
        location_data[location_id]["transaction_count"] += 1
        
        # Track categories for this location
        if trans.category:
            cat_name = trans.category.name
            location_data[location_id]["categories"][cat_name] = \
                location_data[location_id]["categories"].get(cat_name, 0) + 1

    # Determine most common category for each location
    for loc_id, data in location_data.items():
        if data["categories"]:
            data["most_common_category"] = max(data["categories"], key=data["categories"].get)
        del data["categories"]

    # Sort and limit
    top_locations = sorted(location_data.values(), key=lambda x: x["total_spent"], reverse=True)[:limit]

    return {
        "locations": [
            {
                "name": loc["name"],
                "total_spent": round(loc["total_spent"], 2),
                "transaction_count": loc["transaction_count"],
                "most_common_category": loc["most_common_category"]
            }
            for loc in top_locations
        ],
        "base_currency": base_currency
    }

@app.get("/dashboard/available-months")
def get_available_months(db: Session = Depends(get_db)):
    """
    Get list of months that have transactions.
    Useful for populating month selectors in the UI.
    """
    from sqlalchemy import func as sql_func

    # Query for unique year-month combinations
    query = db.query(
        sql_func.strftime('%Y-%m', Transaction.date).label('month')
    ).group_by(
        sql_func.strftime('%Y-%m', Transaction.date)
    ).order_by(
        sql_func.strftime('%Y-%m', Transaction.date).desc()
    )

    results = query.all()

    # Format response
    months = []
    for row in results:
        year, month = row.month.split('-')
        months.append({
            "value": row.month,
            "label": f"{datetime(int(year), int(month), 1).strftime('%B %Y')}",
            "year": int(year),
            "month": int(month)
        })

    # Get summary statistics
    total_months = len(months)
    if months:
        earliest = months[-1]['value']
        latest = months[0]['value']
    else:
        earliest = None
        latest = None

    return {
        "months": months,
        "summary": {
            "total_months": total_months,
            "earliest_month": earliest,
            "latest_month": latest,
            "current_month": datetime.now().strftime('%Y-%m')
        }
    }

@app.get("/dashboard/top-individual-expenses")
def get_top_individual_expenses(
    limit: int = Query(20),
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    exclude_transfers: bool = Query(True),
    type: str = Query("expenses", description="Type: 'expenses' or 'income'"),
    db: Session = Depends(get_db)
):
    """
    Get top individual expenses or income with HISTORICAL exchange rates.
    """
    # Build filters based on type
    if type == "income":
        filters = [Transaction.amount > 0]
    else:
        filters = [Transaction.amount < 0]
    
    if date_from:
        filters.append(Transaction.date >= _as_datetime_floor(date_from))
    if date_to:
        filters.append(Transaction.date <= _as_datetime_ceil(date_to))
    if exclude_transfers:
        filters.append(or_(
            Transaction.category_id.isnot(None),
            Transaction.payee_id.isnot(None)
        ))

    # Get transactions - order depends on type
    if type == "income":
        transactions = db.query(Transaction).filter(and_(*filters)).order_by(Transaction.amount.desc()).all()
    else:
        transactions = db.query(Transaction).filter(and_(*filters)).order_by(Transaction.amount).all()

    if not transactions:
        return {"items": [], "base_currency": "GBP"}

    # Take top by absolute amount
    transactions = transactions[:limit * 2]  # Get extra to ensure we have enough after conversion

    # Date range
    min_date = _to_date(min(t.date for t in transactions))
    max_date = _to_date(max(t.date for t in transactions))

    # Get currencies
    currencies = list(set([t.currency for t in transactions if t.currency]))

    # Load historical rates
    historical_rates = get_rates_bulk(db, currencies, min_date, max_date)
    base_currency = "GBP"

    # Convert and collect
    items = []
    for trans in transactions:
        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})
        
        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted = abs(trans.amount) * (base_rate / trans_rate)

        items.append({
            "id": trans.id,
            "date": trans_date.isoformat(),
            "amount": round(converted, 2),
            "payee": trans.payee.name if trans.payee else "No payee",
            "category": trans.category.name if trans.category else "No category",
            "note": trans.note
        })

    # Sort by converted amount and take top limit
    items = sorted(items, key=lambda x: x["amount"], reverse=True)[:limit]

    return {
        "items": items,
        "base_currency": base_currency
    }

# ============================================
# LOANS & CREDIT CARDS ENDPOINTS 
# ============================================

@app.get("/loans/summary")
def get_loans_summary(db: Session = Depends(get_db)):
    """
    Get summary of all loans and credit cards.
    Detects loans/credit cards dynamically based on transaction patterns:
    - Account must start with a negative transaction
    - Credit cards: 3+ unique payees (excluding transfers)
    - Loans: fewer than 3 unique payees
    """
    # Get all accounts
    all_accounts = db.query(Account).all()
    
    # Determine base currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()
    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    
    # Get exchange rates
    subq = db.query(
        ExchangeRate.currency,
        sql_func.max(ExchangeRate.date).label('max_date')
    ).group_by(ExchangeRate.currency).subquery()
    
    rates_q = db.query(ExchangeRate).join(
        subq,
        (ExchangeRate.currency == subq.c.currency) &
        (ExchangeRate.date == subq.c.max_date)
    ).all()
    rates_dict = {r.currency: r.rate for r in rates_q}
    rates_dict['GBP'] = 1.0
    base_rate = rates_dict.get(base_currency, 1.0)
    
    # Get transfer location IDs
    transfer_locations = db.query(Location.id).filter(
        Location.name.in_(["Transfer In", "Transfer Out"])
    ).all()
    transfer_location_ids = set(loc.id for loc in transfer_locations)
    
    active_credit_cards = 0
    active_loans = 0
    total_owed = 0
    total_interest = 0
    
    CREDIT_CARD_PAYEE_THRESHOLD = 3
    
    for account in all_accounts:
        # Get all transactions for this account, sorted chronologically
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account.id
        ).order_by(Transaction.date, Transaction.id).all()
        
        if not transactions:
            continue
        
        # Check if account starts with negative transaction (debt account)
        first_transaction = transactions[0]
        if first_transaction.amount >= 0:
            continue  # Not a debt account
        
        # Identify transfer transactions
        transfer_ids = set()
        for tx in transactions:
            if tx.location_id and tx.location_id in transfer_location_ids:
                transfer_ids.add(tx.id)
        
        # Count unique payees (excluding transfers)
        unique_payees = set()
        for tx in transactions:
            if tx.payee_id and tx.id not in transfer_ids:
                unique_payees.add(tx.payee_id)
        
        # Determine if it's a credit card or loan
        is_credit_card = len(unique_payees) >= CREDIT_CARD_PAYEE_THRESHOLD
        
        # Calculate metrics in account's original currency, then convert to base
        borrowed = 0
        repaid = 0
        interest = 0
        balance = 0
        
        # Keep track of negative transfer amounts for loans (initial disbursements)
        negative_transfers = []
        
        for tx in transactions:
            # Work in original currency first
            amount = tx.amount
            balance += amount
            
            if amount > 0:
                # Positive = payment
                repaid += amount
            elif amount < 0:
                abs_amount = abs(amount)
                
                # Check if it's a transfer
                is_transfer = tx.id in transfer_ids
                
                if is_transfer:
                    # For loans, negative transfers might be initial disbursements
                    if not is_credit_card:
                        negative_transfers.append(abs_amount)
                else:
                    # Not a transfer - check if it's interest/fees by category (with and without accents)
                    category_name = tx.category.name if tx.category else ""
                    cat_lower = category_name.lower()
                    is_interest_or_fee = (
                        'interes' in cat_lower or 'interés' in cat_lower or
                        'interest' in cat_lower or
                        'comision' in cat_lower or 'comisión' in cat_lower or
                        'fee' in cat_lower or 'hipoteca' in cat_lower or
                        'mortgage' in cat_lower
                    )
                    
                    if is_interest_or_fee:
                        interest += abs_amount
                    else:
                        borrowed += abs_amount
        
        # For loans: if borrowed is 0 or very small, but we have negative transfers,
        # those transfers are likely the loan disbursements
        if not is_credit_card and borrowed < 1 and negative_transfers:
            borrowed = sum(negative_transfers)
        
        # Now convert totals to base currency for summary
        account_rate = rates_dict.get(account.currency, 1.0)
        conversion_factor = base_rate / account_rate
        
        current_owed = abs(min(balance, 0)) * conversion_factor
        
        # Credit cards are NEVER completed, loans are completed when balance >= -0.5
        is_completed = (not is_credit_card) and (balance >= -0.5)
        
        interest_in_base = interest * conversion_factor
        
        # Count active accounts and sum totals
        if is_credit_card:
            # All credit cards count as active
            active_credit_cards += 1
            total_owed += current_owed
        elif not is_completed:
            # Only unpaid loans count as active
            active_loans += 1
            total_owed += current_owed
        
        total_interest += interest_in_base
    
    return {
        "active_credit_cards": active_credit_cards,
        "active_loans": active_loans,
        "total_owed": round(total_owed, 2),
        "total_interest": round(total_interest, 2),
        "base_currency": base_currency
    }


@app.get("/loans/details")
def get_loans_details(
    include_completed: bool = Query(False, description="Include completed loans/credit cards"),
    db: Session = Depends(get_db)
):
    """
    Get detailed information about all loans and credit cards.
    Uses dynamic detection based on transaction patterns.
    """
    # Get all accounts
    all_accounts = db.query(Account).all()
    
    # Determine base currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()
    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    
    # Get transfer location IDs
    transfer_locations = db.query(Location.id).filter(
        Location.name.in_(["Transfer In", "Transfer Out"])
    ).all()
    transfer_location_ids = set(loc.id for loc in transfer_locations)
    
    result = {
        "credit_cards": [],
        "loans": [],
        "completed": [],
        "base_currency": base_currency
    }
    
    CREDIT_CARD_PAYEE_THRESHOLD = 3
    
    for account in all_accounts:
        # Get all transactions for this account, sorted chronologically
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account.id
        ).order_by(Transaction.date, Transaction.id).all()
        
        if not transactions:
            continue
        
        # Check if account starts with negative transaction (debt account)
        first_transaction = transactions[0]
        if first_transaction.amount >= 0:
            continue  # Not a debt account
        
        # Identify transfer transactions
        transfer_ids = set()
        for tx in transactions:
            if tx.location_id and tx.location_id in transfer_location_ids:
                transfer_ids.add(tx.id)
        
        # Count unique payees (excluding transfers)
        unique_payees = set()
        payee_names = []
        for tx in transactions:
            if tx.payee_id and tx.id not in transfer_ids:
                unique_payees.add(tx.payee_id)
                if tx.payee and tx.payee.name:
                    payee_names.append(tx.payee.name)
        
        # Determine if it's a credit card or loan
        is_credit_card = len(unique_payees) >= CREDIT_CARD_PAYEE_THRESHOLD
        
        # Calculate metrics IN ACCOUNT'S ORIGINAL CURRENCY
        borrowed = 0
        repaid = 0
        interest = 0
        balance = 0
        close_date = None
        
        # Get lender name
        lender_name = account.name
        if payee_names and not is_credit_card:
            lender_name = payee_names[0]
        
        # Keep track of negative transfer amounts for loans (initial disbursements)
        negative_transfers = []
        
        tx_list = []
        for tx in transactions:
            # Work in original currency
            amount = tx.amount
            balance += amount
            
            # Check if loan is paid off (only for loans, not credit cards)
            if not is_credit_card and balance >= -0.5 and close_date is None:
                close_date = tx.date
            
            if amount > 0:
                # Positive = payment
                repaid += amount
            elif amount < 0:
                abs_amount = abs(amount)
                
                # Check if it's a transfer
                is_transfer = tx.id in transfer_ids
                
                if is_transfer:
                    # For loans, negative transfers might be initial disbursements
                    if not is_credit_card:
                        negative_transfers.append(abs_amount)
                else:
                    # Not a transfer - check if it's interest/fees by category (with and without accents)
                    category_name = tx.category.name if tx.category else ""
                    cat_lower = category_name.lower()
                    is_interest_or_fee = (
                        'interes' in cat_lower or 'interés' in cat_lower or
                        'interest' in cat_lower or
                        'comision' in cat_lower or 'comisión' in cat_lower or
                        'fee' in cat_lower or 'hipoteca' in cat_lower or
                        'mortgage' in cat_lower
                    )
                    
                    if is_interest_or_fee:
                        interest += abs_amount
                    else:
                        borrowed += abs_amount
            
            # Add transaction to list
            tx_list.append({
                "id": tx.id,
                "date": tx.date.isoformat() if hasattr(tx.date, 'isoformat') else str(tx.date),
                "amount": round(amount, 2),
                "currency": tx.currency,
                "payee_name": tx.payee.name if tx.payee else None,
                "category_name": tx.category.name if tx.category else None,
                "location_name": tx.location.name if tx.location else None,
                "note": tx.note if hasattr(tx, 'note') else None
            })
        
        # For loans: if borrowed is 0 or very small, but we have negative transfers,
        # those transfers are likely the loan disbursements
        if not is_credit_card and borrowed < 1 and negative_transfers:
            borrowed = sum(negative_transfers)
        
        current_owed = abs(min(balance, 0))
        
        # Credit cards are NEVER completed, loans are completed when balance >= -0.5
        is_completed = (not is_credit_card) and (balance >= -0.5)
        
        # For credit cards, get the actual current balance
        current_balance = round(balance, 2) if is_credit_card else None
        
        open_date = first_transaction.date
        
        debt_data = {
            "account": {
                "id": account.id,
                "name": account.name,
                "type": "CREDIT_CARD" if is_credit_card else "LOAN",
                "currency": account.currency,
                "is_active": account.is_active
            },
            "borrowed": round(borrowed, 2),
            "repaid": round(repaid, 2),
            "interest": round(interest, 2),
            "current_owed": round(current_owed, 2),
            "current_balance": current_balance,
            "is_completed": is_completed,
            "open_date": open_date.isoformat() if hasattr(open_date, 'isoformat') else str(open_date),
            "close_date": close_date.isoformat() if close_date and hasattr(close_date, 'isoformat') else None,
            "lender_name": lender_name,
            "unique_payees": len(unique_payees),
            "transactions": tx_list
        }
        
        # Categorize by type and status
        # Credit cards ALWAYS go to credit_cards list (never to completed)
        if is_credit_card:
            result["credit_cards"].append(debt_data)
        elif is_completed:
            if include_completed:
                result["completed"].append(debt_data)
        else:
            result["loans"].append(debt_data)
    
    return result

# ============================================
# ADMIN / MAINTENANCE ENDPOINTS
# ============================================

@app.post("/admin/initialise-balances")
def initialise_balances(db: Session = Depends(get_db)):
    """
    Recalculate balances for all accounts from scratch.
    This includes:
    1. account_balance_after for each transaction (per-account running balance)
    2. total_balance_after for each transaction (global balance across all accounts in base currency)
    """
    print("--- STARTING BALANCE RECALCULATION ---")
    try:
        # Get all accounts
        accounts = db.query(models.Account).all()
        accounts_map = {acc.id: acc for acc in accounts}
        total_tx_count = 0
        
        # PHASE 1: Calculate account_balance_after for each account
        # Also track each account's running balance at each transaction
        account_running_balances = {}  # account_id -> running_balance
        
        for account in accounts:
            print(f"Processing account: {account.name} (ID: {account.id})")
            account_running_balances[account.id] = float(account.initial_balance) if account.initial_balance is not None else 0.0
            
            # Get transactions ordered by date and ID
            transactions = db.query(models.Transaction).filter(
                models.Transaction.account_id == account.id
            ).order_by(models.Transaction.date.asc(), models.Transaction.id.asc()).all()
            
            # Calculate running balance
            running_balance = account_running_balances[account.id]
            
            for t in transactions:
                if t is None:
                    print("WARNING: Found None transaction in list. Skipping.")
                    continue
                
                if not hasattr(t, 'amount') or t.amount is None:
                    print(f"WARNING: Transaction ID {t.id} has None or invalid amount. Assuming 0.")
                    amount = 0.0
                else:
                    amount = float(t.amount)

                running_balance += amount
                t.account_balance_after = running_balance
                total_tx_count += 1
            
            # Update account's current balance
            account.current_balance = running_balance
        
        # PHASE 2: Calculate total_balance_after for all transactions globally
        print("--- CALCULATING TOTAL BALANCE AFTER ---")
        
        # Get exchange rates (to convert to base currency GBP)
        # ExchangeRate model: currency (foreign), rate (how many units of foreign = 1 GBP)
        BASE_CURRENCY = 'GBP'
        exchange_rates = db.query(models.ExchangeRate).order_by(
            models.ExchangeRate.date.desc()
        ).all()
        
        # Build rate lookup - get most recent rate for each currency
        rate_to_base = {BASE_CURRENCY: 1.0}
        seen_currencies = set()
        for r in exchange_rates:
            if r.currency not in seen_currencies:
                # rate is "1 GBP = X foreign", so to convert foreign to GBP: divide by rate
                rate_to_base[r.currency] = 1.0 / float(r.rate)
                seen_currencies.add(r.currency)
        
        # Get ALL transactions ordered globally by date and ID
        all_transactions = db.query(models.Transaction).order_by(
            models.Transaction.date.asc(), 
            models.Transaction.id.asc()
        ).all()
        
        # Reset running balances to initial values
        for account in accounts:
            account_running_balances[account.id] = float(account.initial_balance) if account.initial_balance is not None else 0.0
        
        # Process transactions in global order
        for t in all_transactions:
            if t is None:
                continue
            
            # Update this account's running balance
            amount = float(t.amount) if t.amount is not None else 0.0
            account_running_balances[t.account_id] += amount
            
            # Calculate total balance across all accounts in base currency
            total_balance = 0.0
            for acc_id, balance in account_running_balances.items():
                acc = accounts_map.get(acc_id)
                if acc:
                    currency = acc.currency or BASE_CURRENCY
                    rate = rate_to_base.get(currency, 1.0)
                    total_balance += balance * rate
            
            t.total_balance_after = round(total_balance, 2)
        
        # Commit all changes
        db.commit()
        print(f"--- FINISHED: {len(accounts)} accounts, {total_tx_count} transactions ---")
        
        return {
            "message": "Balances recalculated successfully",
            "accounts_processed": len(accounts),
            "transactions_processed": total_tx_count
        }
        
    except Exception as e:
        db.rollback()
        import traceback
        error_msg = f"CRITICAL ERROR RECALCULATING BALANCES: {str(e)}"
        print(error_msg)
        print(traceback.format_exc())
        raise HTTPException(status_code=500, detail=error_msg)


@app.post("/admin/recalculate-account-balances")
def recalculate_account_balances(db: Session = Depends(get_db)):
    """
    Recalculate current_balance for all accounts based on their transactions.
    This should be run once to fix any discrepancies.
    """
    try:
        # Get all accounts
        accounts = db.query(Account).all()
        for account in accounts:
            # Calculate balance from initial_balance + sum of all transactions
            total_transactions = db.query(func.sum(Transaction.amount)).filter(
                Transaction.account_id == account.id
            ).scalar() or 0

            # Update current_balance
            account.current_balance = account.initial_balance + total_transactions

        db.commit()

        # Return summary
        account_balances = [
            {
                "id": acc.id,
                "name": acc.name,
                "current_balance": acc.current_balance,
                "initial_balance": acc.initial_balance
            }
            for acc in accounts
        ]
        return {
            "message": f"Recalculated balances for {len(accounts)} accounts",
            "accounts": account_balances
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to recalculate balances: {str(e)}")


@app.get("/")
def root():
    """
    Root endpoint with API information.
    """
    return {
        "message": "Welcome to Delfin API",
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


@app.delete("/admin/clean-corrupt-transactions")
def clean_corrupt_transactions(db: Session = Depends(get_db)):
    """
    Identifies and permanently deletes transactions that are corrupted 
    (where amount or date is NULL), which causes 'NoneType' errors.
    """
    try:
        # 1. DELETE rows where the 'amount' field is NULL
        deleted_by_amount = db.query(models.Transaction).filter(
            models.Transaction.amount == None
        ).delete(synchronize_session=False)
        
        # 2. DELETE rows where the 'date' field is NULL
        deleted_by_date = db.query(models.Transaction).filter(
            models.Transaction.date == None
        ).delete(synchronize_session=False)

        total_deleted = deleted_by_amount + deleted_by_date
        db.commit()
        
        # Recalculate balances after cleanup
        initialise_all_balances(db)
        db.commit()
        
        return {
            "message": "Database cleanup complete.",
            "details": f"Successfully deleted {total_deleted} corrupt transactions (deleted by amount: {deleted_by_amount}, deleted by date: {deleted_by_date})."
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database cleanup failed: {str(e)}")


@app.post("/admin/create-indexes")
def create_database_indexes(db: Session = Depends(get_db)):
    """
    Create database indexes to improve query performance.
    Safe to run multiple times (uses IF NOT EXISTS).
    
    This endpoint creates any indexes that aren't defined in models.py
    but are useful for specific query patterns.
    """
    try:
        from sqlalchemy import text
        
        indexes_created = []
        
        # Additional indexes not defined in models.py
        # These complement the SQLAlchemy-defined indexes
        index_definitions = [
            # Payee name for case-insensitive search (COLLATE NOCASE)
            ("idx_payee_name_nocase", "payees", "name COLLATE NOCASE"),
            
            # Note search optimization (helps with prefix searches)
            ("idx_transaction_note", "transactions", "note"),
            
            # Exchange rate lookup optimization
            ("idx_exchange_rate_lookup", "exchange_rates", "currency, date DESC"),
        ]
        
        for idx_name, table, columns in index_definitions:
            sql = f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})"
            try:
                db.execute(text(sql))
                indexes_created.append(idx_name)
            except Exception as e:
                print(f"Could not create index {idx_name}: {e}")
        
        db.commit()
        
        # Run ANALYZE to update query planner statistics
        db.execute(text("ANALYZE"))
        db.commit()
        
        # Get list of all indexes for reporting
        result = db.execute(text("SELECT name FROM sqlite_master WHERE type='index' ORDER BY name"))
        all_indexes = [row[0] for row in result if row[0] and not row[0].startswith('sqlite_')]
        
        return {
            "message": f"Index maintenance complete",
            "indexes_created_now": indexes_created,
            "total_indexes": len(all_indexes),
            "all_indexes": all_indexes,
            "note": "ANALYZE was run to update query statistics"
        }
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create indexes: {str(e)}")