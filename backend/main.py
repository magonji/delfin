from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, date, timedelta, time  # <-- añadido 'time'
from sqlalchemy import func as sql_func, case, and_, or_
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


@app.post("/transactions", response_model=schemas.TransactionResponse)
def create_transaction(
    transaction: schemas.TransactionCreate,
    db: Session = Depends(get_db)
):
    """
    Create a new transaction and update account balance.
    """
    # Create transaction
    db_transaction = models.Transaction(**transaction.dict())
    db.add(db_transaction)
    db.flush()  # Get the ID without committing

    # Update account balance
    account = db.query(models.Account).filter(
        models.Account.id == transaction.account_id
    ).first()
    if account:
        account.current_balance += transaction.amount

    # Recalculate balances from this transaction onwards
    recalculate_balances_from_transaction(db, db_transaction.id)

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
    old_amount = db_transaction.amount
    old_account_id = db_transaction.account_id

    # Update the old account's balance (subtract old amount)
    if old_account_id:
        old_account = db.query(models.Account).filter(
            models.Account.id == old_account_id
        ).first()
        if old_account:
            old_account.current_balance -= old_amount

    # Update transaction fields
    for key, value in transaction.dict().items():
        setattr(db_transaction, key, value)
    db_transaction.updated_at = datetime.utcnow()

    # Update the new account's balance (add new amount)
    if transaction.account_id:
        new_account = db.query(models.Account).filter(
            models.Account.id == transaction.account_id
        ).first()
        if new_account:
            new_account.current_balance += transaction.amount

    # Recalculate balances from this transaction onwards for both accounts
    affected_account_ids = list(set([old_account_id, transaction.account_id]))
    recalculate_balances_from_transaction(db, transaction_id, affected_account_ids)

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

    # Update account balance
    account = db.query(models.Account).filter(
        models.Account.id == db_transaction.account_id
    ).first()
    if account:
        account.current_balance -= db_transaction.amount

    # Store the account ID and date before deleting
    affected_account_id = db_transaction.account_id
    transaction_date = db_transaction.date

    # Delete the transaction
    db.delete(db_transaction)
    db.commit()

    # Find the next transaction after the deleted one to trigger recalculation
    next_transaction = db.query(models.Transaction).filter(
        models.Transaction.account_id == affected_account_id,
        models.Transaction.date >= transaction_date
    ).order_by(models.Transaction.date.asc(), models.Transaction.id.asc()).first()

    if next_transaction:
        recalculate_balances_from_transaction(db, next_transaction.id, [affected_account_id])

    return {"message": "Transaction deleted successfully"}

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
    
# ============================================
# TRANSFER ENDPOINTS
# ============================================

@app.post("/transactions/transfers")
def create_transfer(
    transfer: schemas.TransferCreate,
    db: Session = Depends(get_db)
):
    """
    Create a transfer between two accounts.
    Creates two transactions: one outgoing and one incoming.
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

    # Currency conversion expression
    conversion_cases = []
    for currency, rate in rates_dict.items():
        conversion_factor = base_rate / rate
        conversion_cases.append(
            (Transaction.currency == currency, Transaction.amount * conversion_factor)
        )
    conversion_expression = case(
        *conversion_cases,
        else_=Transaction.amount  # Fallback
    )

    total_balance_converted = db.query(
        sql_func.sum(conversion_expression)
    ).scalar() or 0

    # Count queries - NOTE: Only count ACTIVE accounts
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
    period: str,  # "daily", "weekly", "monthly"
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    excluded_accounts: Optional[str] = Query(None),  # comma-separated account IDs
    db: Session = Depends(get_db)
):
    """
    Get net worth evolution data optimised for charting.
    Period determines the data aggregation level.
    Returns pre-calculated data points ready for Chart.js
    """

    # --- Helpers para normalizar fechas / comparaciones ---
    def _to_date(value):
        """Normalise any date-like to datetime.date (no time)."""
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        try:
            return datetime.fromisoformat(str(value)).date()
        except Exception:
            return None

    def _as_datetime_floor(d):
        """Return a datetime at 00:00:00 for a given date."""
        if isinstance(d, datetime):
            return d
        if isinstance(d, date):
            return datetime.combine(d, time.min)
        return None

    def _as_datetime_ceil(d):
        """Return a datetime at 23:59:59.999999 for a given date."""
        if isinstance(d, datetime):
            return d
        if isinstance(d, date):
            return datetime.combine(d, time.max)
        return None

    # Parse excluded accounts
    excluded_ids = []
    if excluded_accounts:
        excluded_ids = [int(id) for id in excluded_accounts.split(',') if id.strip().isdigit()]

    # Get latest exchange rates for conversion
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

    # Determine base currency (most common in transactions)
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()

    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    base_rate = rates_dict.get(base_currency, 1.0)

    # Build query filters
    filters = []
    if excluded_ids:
        filters.append(~Transaction.account_id.in_(excluded_ids))
    if date_from:
        filters.append(Transaction.date >= _as_datetime_floor(date_from))  # normalizado
    if date_to:
        filters.append(Transaction.date <= _as_datetime_ceil(date_to))     # normalizado

    # Build currency conversion expression
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

    # Get all transactions with converted amounts (order by ascending date)
    query = db.query(
        Transaction.date,
        Transaction.account_id,
        conversion_expression.label('converted_amount')
    )
    if filters:
        query = query.filter(and_(*filters))
    query = query.order_by(Transaction.date)

    # ---------------- Baseline when date_from is set ----------------
    account_balances = {}
    all_balance_points = []

    if date_from:
        def convert_to_base(amount, currency):
            rate = rates_dict.get(currency, 1.0)
            return float(amount or 0) * (base_rate / rate)

        # Only active accounts (excluding those marked)
        accounts_q = db.query(Account).filter(Account.is_active == 1)
        if excluded_ids:
            accounts_q = accounts_q.filter(~Account.id.in_(excluded_ids))
        accounts_list = accounts_q.all()

        total_baseline = 0.0
        boundary = _as_datetime_floor(date_from)  # compare with DateTime column

        for acc in accounts_list:
            # Last transaction BEFORE date_from for this account
            last_tx = db.query(Transaction).filter(
                Transaction.account_id == acc.id,
                Transaction.date < boundary
            ).order_by(Transaction.date.desc(), Transaction.id.desc()).first()

            if last_tx and last_tx.account_balance_after is not None:
                baseline_native = last_tx.account_balance_after
            else:
                baseline_native = acc.initial_balance or 0

            baseline_conv = convert_to_base(baseline_native, acc.currency)
            account_balances[acc.id] = baseline_conv
            total_baseline += baseline_conv

        # Synthetic point at date_from with the accumulated net worth up to that day
        start_date = _to_date(date_from)
        all_balance_points.append({
            'date': start_date,
            'balance': round(total_baseline, 2)
        })

    # ---------------- Process transactions in range ----------------
    transactions_data = query.all()

    if not transactions_data and not all_balance_points:
        return {
            "data_points": [],
            "summary": {
                "initial_balance": 0,
                "current_balance": 0,
                "total_change": 0,
                "percentage_change": 0,
                "peak_balance": 0,
                "peak_date": None,
                "lowest_balance": 0,
                "lowest_date": None
            },
            "base_currency": base_currency
        }

    for trans in transactions_data:
        if trans.account_id not in account_balances:
            account_balances[trans.account_id] = 0.0
        account_balances[trans.account_id] += float(trans.converted_amount or 0)

        # Normalise transaction date to date (no time)
        d = _to_date(trans.date)

        total_balance = sum(account_balances.values())
        all_balance_points.append({
            'date': d,
            'balance': round(total_balance, 2)
        })

    # ---------------- Aggregate based on period ----------------
    aggregated_data = []
    if period == "monthly":
        # Group by month, keep last balance of each month
        monthly_data = {}
        for point in all_balance_points:
            if point['date'] is None:
                continue
            month_key = point['date'].strftime('%Y-%m')
            # Keep last balance of each month
            if month_key not in monthly_data or point['date'] > monthly_data[month_key]['date']:
                monthly_data[month_key] = point
        aggregated_data = sorted(monthly_data.values(), key=lambda x: x['date'])
    elif period == "weekly":
        # Group by week (start Monday), keep last balance of each week
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
        # Group by day (keep last)
        daily_data = {}
        for point in all_balance_points:
            if point['date'] is None:
                continue
            day_key = point['date'].strftime('%Y-%m-%d')
            if day_key not in daily_data or point['date'] > daily_data[day_key]['date']:
                daily_data[day_key] = point
        aggregated_data = sorted(daily_data.values(), key=lambda x: x['date'])

    # ---------------- Summary statistics ----------------
    if aggregated_data:
        initial_balance = aggregated_data[0]['balance']
        current_balance = aggregated_data[-1]['balance']
        total_change = current_balance - initial_balance
        percentage_change = ((total_change / abs(initial_balance)) * 100) if initial_balance != 0 else 0

        balances = [p['balance'] for p in aggregated_data]
        peak_balance = max(balances)
        lowest_balance = min(balances)

        peak_point = next((p for p in aggregated_data if p['balance'] == peak_balance), None)
        lowest_point = next((p for p in aggregated_data if p['balance'] == lowest_balance), None)

        summary = {
            "initial_balance": round(initial_balance, 2),
            "current_balance": round(current_balance, 2),
            "total_change": round(total_change, 2),
            "percentage_change": round(percentage_change, 2),
            "peak_balance": round(peak_balance, 2),
            "peak_date": peak_point['date'].isoformat() if peak_point else None,
            "lowest_balance": round(lowest_balance, 2),
            "lowest_date": lowest_point['date'].isoformat() if lowest_point else None
        }
    else:
        summary = {
            "initial_balance": 0,
            "current_balance": 0,
            "total_change": 0,
            "percentage_change": 0,
            "peak_balance": 0,
            "peak_date": None,
            "lowest_balance": 0,
            "lowest_date": None
        }

    # Format response
    data_points = [
        {
            "date": point['date'].isoformat(),
            "balance": point['balance']
        }
        for point in aggregated_data
    ]

    return {
        "data_points": data_points,
        "summary": summary,
        "base_currency": base_currency
    }


# ============================================
# (El resto de endpoints optimizados de categorías / yearly / top-payees / months)
#  -- se mantienen IGUAL que en tu versión --
# ============================================

@app.get("/dashboard/categories/{period}")
def get_categories_analysis(
    period: str,  # "monthly", "weekly", "daily"
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    category_ids: Optional[str] = Query(None),  # comma-separated category IDs
    limit: int = Query(20, description="Top N categories to return"),
    db: Session = Depends(get_db)
):
    """
    Get spending by category over time, optimised for charting.
    Returns time series data for selected categories.
    """
    from sqlalchemy import func as sql_func, case, and_, extract
    from datetime import datetime, timedelta
    # Parse category IDs if provided
    selected_categories = None
    if category_ids:
        selected_categories = [int(id) for id in category_ids.split(',')]

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

    # Get base currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()
    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    base_rate = rates_dict.get(base_currency, 1.0)

    # Build currency conversion
    conversion_cases = []
    for currency, rate in rates_dict.items():
        conversion_factor = base_rate / rate
        conversion_cases.append(
            (Transaction.currency == currency, sql_func.abs(Transaction.amount) * conversion_factor)
        )
    conversion_expression = case(
        *conversion_cases,
        else_=sql_func.abs(Transaction.amount)
    )

    # Build filters
    filters = [Transaction.amount < 0]  # Only expenses
    if date_from:
        filters.append(Transaction.date >= datetime.combine(date_from, time.min))
    if date_to:
        filters.append(Transaction.date <= datetime.combine(date_to, time.max))
    if selected_categories:
        filters.append(Transaction.category_id.in_(selected_categories))

    # Determine grouping based on period - SQLite compatible
    if period == "monthly":
        # Group by YYYY-MM
        date_group = sql_func.strftime('%Y-%m', Transaction.date)
    elif period == "weekly":
        # Group by YYYY-WWW (week number, Monday-first)
        date_group = sql_func.strftime('%Y-W%W', Transaction.date)
    else:  # daily
        # Group by YYYY-MM-DD
        date_group = sql_func.strftime('%Y-%m-%d', Transaction.date)

    # Query for category totals over time
    query = db.query(
        date_group.label('period_date'),
        Transaction.category_id,
        Category.name.label('category_name'),
        sql_func.sum(conversion_expression).label('total_amount')
    ).join(
        Category, Transaction.category_id == Category.id, isouter=True
    ).filter(
        and_(*filters)
    ).group_by(
        date_group,
        Transaction.category_id,
        Category.name
    ).order_by(
        date_group,
        sql_func.sum(conversion_expression).desc()
    )

    results = query.all()

    # -----------------------------
    # Procesar resultados en series
    # -----------------------------
    time_series_data = {}   # {category_name: {period_str: amount}}
    category_totals = {}    # totales por categoría (para top-N)
    observed_periods = set()

    for row in results:
        period_str = str(row.period_date)
        category_name = row.category_name or 'Uncategorised'
        amount = float(row.total_amount or 0)
        observed_periods.add(period_str)
        if category_name not in time_series_data:
            time_series_data[category_name] = {}
        if category_name not in category_totals:
            category_totals[category_name] = 0.0
        time_series_data[category_name][period_str] = round(amount, 2)
        category_totals[category_name] += amount

    # -----------------------------------------------
    # Construir línea temporal completa (con huecos 0)
    # -----------------------------------------------
    from datetime import date as dt_date

    def _parse_date(d):
        if isinstance(d, dt_date):
            return datetime.combine(d, time.min)
        if isinstance(d, datetime):
            return d
        if d:
            # 'YYYY-MM-DD' o 'YYYY-MM' desde el cliente
            try:
                # intentamos 'YYYY-MM-DD'
                return datetime.strptime(str(d), '%Y-%m-%d')
            except ValueError:
                try:
                    # intentamos 'YYYY-MM' -> 1er día del mes
                    return datetime.strptime(str(d), '%Y-%m')
                except ValueError:
                    return None
        return None

    # Determinar rango temporal
    start_dt = _parse_date(date_from) if date_from else None
    end_dt   = _parse_date(date_to)   if date_to   else None

    # Si no hay filtros, inferir del propio resultado:
    if not start_dt or not end_dt:
        # Convertir observed_periods a fechas reales según 'period'
        def _period_to_dt(p):
            if period == "monthly":
                # p = 'YYYY-MM'
                return datetime.strptime(p, '%Y-%m')
            elif period == "weekly":
                # p = 'YYYY-Www', ejemplo '2025-W06' (strftime con %W)
                y, w = p.split('-W')
                y = int(y); w = int(w)
                # Lunes de esa semana (semana estilo %W, Monday-first, 0-based)
                first = datetime.strptime(f"{y}-01-01", "%Y-%m-%d")
                monday = first + timedelta(days=-first.weekday()) + timedelta(weeks=w)
                return monday
            else:  # daily
                # p = 'YYYY-MM-DD'
                return datetime.strptime(p, '%Y-%m-%d')

        if observed_periods:
            inferred_dates = sorted(_period_to_dt(p) for p in observed_periods)
            start_dt = start_dt or inferred_dates[0]
            end_dt   = end_dt   or inferred_dates[-1]
        else:
            # Sin datos, definimos una ventana mínima de 1 período
            today = datetime.now()
            if period == "monthly":
                start_dt = datetime(today.year, today.month, 1)
                end_dt   = start_dt
            elif period == "weekly":
                start_dt = today - timedelta(days=today.weekday())
                end_dt   = start_dt
            else:
                start_dt = datetime(today.year, today.month, today.day)
                end_dt   = start_dt

    # Generar lista completa de períodos como strings
    def _iter_periods(start_dt, end_dt, granularity):
        periods = []
        if granularity == "monthly":
            y, m = start_dt.year, start_dt.month
            y2, m2 = end_dt.year, end_dt.month
            while (y < y2) or (y == y2 and m <= m2):
                periods.append(f"{y}-{str(m).zfill(2)}")
                # avanzar un mes
                m += 1
                if m == 13:
                    m = 1; y += 1
        elif granularity == "weekly":
            # normalizar a lunes
            cur = start_dt - timedelta(days=start_dt.weekday())
            end = end_dt   - timedelta(days=end_dt.weekday())
            while cur <= end:
                periods.append(cur.strftime('%Y-W%W'))
                cur += timedelta(weeks=1)
        else:  # daily
            cur = start_dt
            end = end_dt
            while cur.date() <= end.date():
                periods.append(cur.strftime('%Y-%m-%d'))
                cur += timedelta(days=1)
        return periods

    sorted_periods = _iter_periods(start_dt, end_dt, period)

    # Get top categories by total spending (puede ser 0 si no hay datos)
    top_categories = sorted(
        category_totals.items(),
        key=lambda x: x[1],
        reverse=True
    )[:limit]

    categories_data = {}
    # Asegurar que las top categorías tienen serie completa con 0s
    for category_name, total in top_categories:
        series = []
        per_map = time_series_data.get(category_name, {})
        for p in sorted_periods:
            series.append(per_map.get(p, 0))
        categories_data[category_name] = series

    # Calculate summary statistics
    total_spent = sum(category_totals.values())
    num_periods = len(sorted_periods)
    average_per_period = total_spent / num_periods if num_periods > 0 else 0

    # Format top categories for response
    top_categories_formatted = [
        {
            "name": name,
            "total": round(total, 2),
            "percentage": round((total / total_spent * 100), 1) if total_spent > 0 else 0
        }
        for name, total in top_categories
    ]

    return {
        "periods": sorted_periods,
        "categories": categories_data,
        "top_categories": top_categories_formatted,
        "summary": {
            "total_spent": round(total_spent, 2),
            "average_per_period": round(average_per_period, 2),
            "num_categories": len(category_totals)
        },
        "base_currency": base_currency
    }


@app.get("/dashboard/categories/breakdown/{year_month}")
def get_monthly_category_breakdown(
    year_month: str,  # Format: "2024-11"
    db: Session = Depends(get_db)
):
    """
    Get category breakdown for a specific month.
    Perfect for pie/doughnut charts.
    """
    from sqlalchemy import func as sql_func, case, and_, extract

    # Parse year and month
    try:
        year, month = year_month.split('-')
        year = int(year)
        month = int(month)
    except:
        raise HTTPException(status_code=400, detail="Invalid year_month format. Use YYYY-MM")

    # Get exchange rates
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

    # Get base currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()

    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    base_rate = rates_dict.get(base_currency, 1.0)

    # Build currency conversion
    conversion_cases = []
    for currency, rate in rates_dict.items():
        conversion_factor = base_rate / rate
        conversion_cases.append(
            (Transaction.currency == currency, sql_func.abs(Transaction.amount) * conversion_factor)
        )
    conversion_expression = case(
        *conversion_cases,
        else_=sql_func.abs(Transaction.amount)
    )

    # Query for category totals in the month

    transfer_ids = [
        r.id for r in db.query(Location.id)
        .filter(Location.name.in_(["Transfer In", "Transfer Out"]))
        .all()
    ]
    extra_filters = []
    if transfer_ids:
        extra_filters.append(~Transaction.location_id.in_(transfer_ids))

    # ====== DONA (totales por categoría del mes) ======
    query = db.query(
        Category.name.label('category_name'),
        Category.id.label('category_id'),
        sql_func.sum(conversion_expression).label('total_amount'),
        sql_func.count(Transaction.id).label('transaction_count')
    ).join(
        Category, Transaction.category_id == Category.id, isouter=True
    ).filter(
        and_(
            Transaction.amount < 0,                            # solo gastos
            sql_func.strftime('%Y', Transaction.date) == str(year),
            sql_func.strftime('%m', Transaction.date) == str(month).zfill(2),
            *extra_filters                                     # ⬅️ EXCLUIR traspasos
        )
    ).group_by(
        Category.id, Category.name
    ).order_by(
        sql_func.sum(conversion_expression).desc()
    )

    results = query.all()

    # ====== TOP 10 GASTOS DEL MES ======
    expenses_query = db.query(
        Transaction.date,
        Transaction.note,
        conversion_expression.label('amount'),
        Category.name.label('category_name'),
        Payee.name.label('payee_name')
    ).join(
        Category, Transaction.category_id == Category.id, isouter=True
    ).join(
        Payee, Transaction.payee_id == Payee.id, isouter=True
    ).filter(
        and_(
            Transaction.amount < 0,
            sql_func.strftime('%Y', Transaction.date) == str(year),
            sql_func.strftime('%m', Transaction.date) == str(month).zfill(2),
            *extra_filters                                     # ⬅️ EXCLUIR traspasos
        )
    ).order_by(
        conversion_expression.desc()
    ).limit(10)

    top_expenses = expenses_query.all()

    # Format response
    categories = []
    total_amount = 0
    for row in results:
        category_name = row.category_name or 'Uncategorised'
        amount = float(row.total_amount)
        total_amount += amount
        categories.append({
            "id": row.category_id,
            "name": category_name,
            "amount": round(amount, 2),
            "transaction_count": row.transaction_count
        })

    # Add percentages
    for category in categories:
        category["percentage"] = round((category["amount"] / total_amount * 100), 1) if total_amount > 0 else 0

    # Format top expenses
    top_expenses_formatted = [
        {
            "date": expense.date.isoformat() if hasattr(expense.date, 'isoformat') else str(expense.date),
            "amount": round(float(expense.amount), 2),
            "category": expense.category_name or 'Uncategorised',
            "payee": expense.payee_name or 'Unknown',
            "note": expense.note
        }
        for expense in top_expenses
    ]

    return {
        "month": year_month,
        "categories": categories[:20],  # Top 20 categories
        "top_expenses": top_expenses_formatted,
        "summary": {
            "total_spent": round(total_amount, 2),
            "num_categories": len(categories),
            "num_transactions": sum(c["transaction_count"] for c in categories)
        },
        "base_currency": base_currency
    }


@app.get("/dashboard/yearly-summary")
def get_yearly_summary(
    year: Optional[int] = Query(None, description="Year to analyze (default: current year)"),
    db: Session = Depends(get_db)
):
    """
    Get yearly summary with month-by-month breakdown of income and expenses.
    Perfect for yearly overview charts.
    """
    from sqlalchemy import func as sql_func, case, and_

    # Use current year if not specified
    if not year:
        year = datetime.now().year

    # Get exchange rates
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

    # Get base currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()

    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    base_rate = rates_dict.get(base_currency, 1.0)

    # Build currency conversion
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

    # Query for monthly income and expenses
    monthly_query = db.query(
        sql_func.strftime('%m', Transaction.date).label('month'),
        sql_func.sum(
            case(
                (Transaction.amount > 0, conversion_expression),
                else_=0
            )
        ).label('income'),
        sql_func.sum(
            case(
                (Transaction.amount < 0, sql_func.abs(conversion_expression)),
                else_=0
            )
        ).label('expenses')
    ).filter(
        func.strftime('%Y', Transaction.date) == str(year),
        or_(
            Transaction.location_id.is_(None),
            Transaction.location.has(Location.name.notin_(["Transfer In", "Transfer Out"]))
        )
    ).group_by(
        sql_func.strftime('%m', Transaction.date)
    ).order_by(
        sql_func.strftime('%m', Transaction.date)
    )

    monthly_results = monthly_query.all()

    # Query for category breakdown for the year
    category_query = db.query(
        Category.name.label('category_name'),
        sql_func.sum(sql_func.abs(conversion_expression)).label('total')
    ).join(
        Category, Transaction.category_id == Category.id, isouter=True
    ).filter(
        and_(
            Transaction.amount < 0,
            sql_func.strftime('%Y', Transaction.date) == str(year)
        )
    ).filter(
        Transaction.location.has(models.Location.name.notin_(["Transfer In", "Transfer Out"]))
    ).group_by(
        Category.name
    ).order_by(
        sql_func.sum(sql_func.abs(conversion_expression)).desc()
    ).limit(10)

    category_results = category_query.all()

    # Build monthly data
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
            'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    monthly_data = []
    total_income = 0
    total_expenses = 0

    # ✅ Acumular por mes en vez de sobrescribir
    monthly_sums = {}
    for row in monthly_results:
        m = row.month  # '01'..'12'
        if m not in monthly_sums:
            monthly_sums[m] = {'income': 0.0, 'expenses': 0.0}
        monthly_sums[m]['income']  += float(row.income or 0)
        monthly_sums[m]['expenses'] += float(row.expenses or 0)

    for i in range(1, 13):
        month_str = str(i).zfill(2)
        sums = monthly_sums.get(month_str, {'income': 0.0, 'expenses': 0.0})
        income   = sums['income']
        expenses = sums['expenses']
        total_income   += income
        total_expenses += expenses
        monthly_data.append({
            "month": months[i-1],
            "month_num": i,
            "income": round(income, 2),
            "expenses": round(expenses, 2),
            "net": round(income - expenses, 2)
        })

    # Calculate averages
    months_with_data = len([m for m in monthly_data if m['income'] > 0 or m['expenses'] > 0])
    avg_monthly_income = total_income / months_with_data if months_with_data > 0 else 0
    avg_monthly_expenses = total_expenses / months_with_data if months_with_data > 0 else 0

    # Format category breakdown
    category_breakdown = [
        {
            "name": row.category_name or 'Uncategorised',
            "amount": round(float(row.total), 2),
            "percentage": round((float(row.total) / total_expenses * 100), 1) if total_expenses > 0 else 0
        }
        for row in category_results
    ]

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
            "highest_expense_month": max(monthly_data, key=lambda x: x['expenses'])['month'] if monthly_data else None,
            "highest_income_month": max(monthly_data, key=lambda x: x['income'])['month'] if monthly_data else None
        },
        "base_currency": base_currency
    }


@app.get("/dashboard/top-payees")
def get_top_payees(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date] = Query(None),
    limit: int = Query(20, description="Number of top payees to return"),
    db: Session = Depends(get_db)
):
    """
    Get top payees by transaction amount and frequency.
    Useful for identifying spending patterns.
    """
    from sqlalchemy import func as sql_func, case, and_

    # Get exchange rates
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

    # Get base currency
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()

    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    base_rate = rates_dict.get(base_currency, 1.0)

    # Build currency conversion
    conversion_cases = []
    for currency, rate in rates_dict.items():
        conversion_factor = base_rate / rate
        conversion_cases.append(
            (Transaction.currency == currency, sql_func.abs(Transaction.amount) * conversion_factor)
        )
    conversion_expression = case(
        *conversion_cases,
        else_=sql_func.abs(Transaction.amount)
    )

    # Build filters
    filters = [Transaction.amount < 0]  # Only expenses
    if date_from:
        filters.append(Transaction.date >= datetime.combine(date_from, time.min))
    if date_to:
        filters.append(Transaction.date <= datetime.combine(date_to, time.max))

    # Query for top payees
    query = db.query(
        Payee.id,
        Payee.name,
        sql_func.sum(conversion_expression).label('total_spent'),
        sql_func.count(Transaction.id).label('transaction_count'),
        sql_func.avg(conversion_expression).label('avg_transaction'),
        sql_func.max(Transaction.date).label('last_transaction'),
        Category.name.label('most_common_category')
    ).join(
        Payee, Transaction.payee_id == Payee.id
    ).join(
        Category, Payee.most_common_category_id == Category.id, isouter=True
    ).filter(
        and_(*filters)
    ).group_by(
        Payee.id,
        Payee.name,
        Category.name
    ).order_by(
        sql_func.sum(conversion_expression).desc()
    ).limit(limit)

    results = query.all()

    # Calculate total for percentage
    total_query = db.query(
        sql_func.sum(conversion_expression).label('total')
    ).filter(
        and_(*filters)
    )
    total_result = total_query.scalar() or 0

    # Format response
    payees = []
    for row in results:
        payees.append({
            "id": row.id,
            "name": row.name,
            "total_spent": round(float(row.total_spent), 2),
            "transaction_count": row.transaction_count,
            "avg_transaction": round(float(row.avg_transaction), 2),
            "last_transaction": row.last_transaction.isoformat() if hasattr(row.last_transaction, 'isoformat') else str(row.last_transaction),
            "most_common_category": row.most_common_category or 'Uncategorised',
            "percentage_of_total": round((float(row.total_spent) / float(total_result) * 100), 1) if total_result > 0 else 0
        })

    return {
        "payees": payees,
        "summary": {
            "total_spent": round(float(total_result), 2),
            "num_payees": len(results),
            "date_range": {
                "from": date_from.isoformat() if date_from else None,
                "to": date_to.isoformat() if date_to else None
            }
        },
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

# === NUEVO ENDPOINT: Top N gastos individuales (excluye traspasos) ===
from fastapi import Query

@app.get("/dashboard/top-individual-expenses")
def get_top_individual_expenses(
    date_from: Optional[date] = Query(None),
    date_to: Optional[date]   = Query(None),
    limit: int = Query(20, ge=1, le=200, description="Número de gastos a devolver"),
    exclude_transfers: bool = Query(True, description="Excluir Transfer In/Out"),
    db: Session = Depends(get_db)
):
    """
    Devuelve las N transacciones individuales de gasto más altas
    en moneda base, opcionalmente filtradas por rango de fechas
    y excluyendo traspasos entre cuentas.
    """
    from sqlalchemy import func as sql_func, case, and_

    # --- Tipos de cambio recientes (igual que en otros endpoints) ---
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

    # Moneda base = la más común en transacciones (como haces en otros)
    currency_counts = db.query(
        Transaction.currency,
        sql_func.count(Transaction.id).label('count')
    ).group_by(Transaction.currency).order_by(sql_func.count(Transaction.id).desc()).all()
    base_currency = currency_counts[0][0] if currency_counts else "GBP"
    base_rate = rates_dict.get(base_currency, 1.0)

    # Conversión a base: |amount| * (base_rate / rate_moneda)
    conversion_cases = []
    for curr, rate in rates_dict.items():
        conv_factor = base_rate / rate
        conversion_cases.append((Transaction.currency == curr, sql_func.abs(Transaction.amount) * conv_factor))
    conversion_expr = case(*conversion_cases, else_=sql_func.abs(Transaction.amount))

    # --- Filtros: solo gastos, rango fechas, excluir traspasos ---
    filters = [Transaction.amount < 0]
    if date_from:
        filters.append(Transaction.date >= datetime.combine(date_from, time.min))
    if date_to:
        filters.append(Transaction.date <= datetime.combine(date_to, time.max))

    if exclude_transfers:
        transfer_ids = [
            r.id for r in db.query(Location.id)
            .filter(Location.name.in_(["Transfer In", "Transfer Out"]))
            .all()
        ]
        if transfer_ids:
            filters.append(~Transaction.location_id.in_(transfer_ids))

    # --- Consulta principal: top N por importe convertido ---
    q = db.query(
        Transaction.id,
        Transaction.date,
        conversion_expr.label('amount_converted'),
        Transaction.note,
        Payee.name.label('payee_name'),
        Category.name.label('category_name'),
        Account.name.label('account_name'),
        Location.name.label('location_name')
    ).join(Payee,   Transaction.payee_id   == Payee.id,   isouter=True
    ).join(Category, Transaction.category_id == Category.id, isouter=True
    ).join(Account,  Transaction.account_id  == Account.id,  isouter=True
    ).join(Location, Transaction.location_id == Location.id, isouter=True
    ).filter(and_(*filters)
    ).order_by(sql_func.abs(conversion_expr).desc()
    ).limit(limit)

    rows = q.all()

    items = []
    for r in rows:
        items.append({
            "id": r.id,
            "date": r.date.isoformat() if hasattr(r.date, "isoformat") else str(r.date),
            "amount": round(float(r.amount_converted or 0), 2),
            "currency": base_currency,
            "payee": r.payee_name or "Unknown",
            "category": r.category_name or "Uncategorised",
            "account": r.account_name or None,
            "note": r.note,
            "location": r.location_name or None,
        })

    return {
        "items": items,
        "summary": {
            "count": len(items),
            "date_range": {
                "from": date_from.isoformat() if date_from else None,
                "to": date_to.isoformat() if date_to else None
            }
        },
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
    Initialise balance columns for all existing transactions.
    This should be run once after migration.
    """
    try:
        initialise_all_balances(db)
        return {"message": "Balances initialised successfully"}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to initialise balances: {str(e)}")


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