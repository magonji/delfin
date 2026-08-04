from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, date, timedelta, time
from sqlalchemy import func as sql_func, case, and_, or_, func
import shutil
import os
from backend.database import get_db
from backend import database
from backend import models, schemas
from backend import maintenance
from backend import backup as db_backup
from backend import security
from backend.models import Account, Category, Payee, Location, Project, Transaction, ExchangeRate, Budget, RecurringExpense, RecurringExpenseHistory, RecurringExpensePayment, PlannedExpense, Loan
from backend.schemas import ExchangeRateResponse
from backend.helpers import (
    recalculate_balances_from_transaction,
    initialise_all_balances,
    get_rates_bulk,
    get_latest_rates,
    get_base_currency
)
from backend import budget_engine
from backend import loan_engine

# The database is encrypted; tables are created on unlock() after login,
# not at import time (there is no engine until the app is unlocked).

app = FastAPI(
    title="Delfin API",
    description="Personal finance management system based in Financisto",
    version="1.0.0"
)

import threading
import logging

logger = logging.getLogger("delfin")

_rates_last_checked: Optional[date] = None
_rates_lock = threading.Lock()

def _check_and_update_rates():
    """Update exchange rates if last stored rate is from a previous day. Thread-safe, runs at most once per day."""
    global _rates_last_checked
    if not database.is_unlocked():
        return  # DB is locked (no one logged in yet) — nothing we can do
    today = date.today()
    if _rates_last_checked == today:
        return
    with _rates_lock:
        if _rates_last_checked == today:
            return
        try:
            from backend.update_exchange_rates import update_exchange_rates, get_last_exchange_rate_date
            from backend.database import SessionLocal
            db = SessionLocal()
            try:
                last_date = get_last_exchange_rate_date(db)
                if not last_date or last_date < today:
                    logger.info(f"Auto-updating exchange rates (last: {last_date})...")
                    update_exchange_rates()
                    logger.info("Exchange rates updated successfully.")
                else:
                    logger.info("Exchange rates are up to date.")
            finally:
                db.close()
            _rates_last_checked = today
        except Exception as e:
            logger.warning(f"Auto-update rates failed (non-fatal): {e}")
            _rates_last_checked = today

@app.on_event("startup")
def auto_update_exchange_rates():
    """Auto-update exchange rates on startup (no-op until the app is unlocked)."""
    _check_and_update_rates()

@app.on_event("startup")
def start_maintenance_scheduler():
    """Start the daily maintenance scheduler. It idles while the app is locked
    and starts working once someone has logged in (unlocked the DB)."""
    maintenance.start_scheduler()

class CacheControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Trigger daily rate check when any HTML page is loaded (the nightly
        # maintenance job handles the full refresh + backup on its own schedule)
        if request.url.path.endswith(".html") or request.url.path == "/":
            threading.Thread(target=_check_and_update_rates, daemon=True).start()
        response = await call_next(request)
        if request.url.path.startswith("/app/"):
            if request.url.path.endswith((".png", ".ico", ".svg")):
                response.headers["Cache-Control"] = "public, max-age=86400"
            else:
                response.headers["Cache-Control"] = "public, max-age=0, must-revalidate"
        return response

# --- Authentication gate -----------------------------------------------------
# Paths reachable without a session (login flow + assets the login page needs).
_PUBLIC_PREFIXES = ("/docs", "/redoc", "/app/icons/")
_PUBLIC_PATHS = {
    "/auth/status", "/auth/login", "/auth/setup", "/auth/recover",
    "/login.html", "/favicon.ico", "/openapi.json", "/manifest.json", "/sw.js",
    # PWA assets the login page / "Add to Home Screen" needs before auth.
    "/app/manifest.json", "/app/sw.js",
    "/apple-touch-icon.png", "/apple-touch-icon-precomposed.png",
}

def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS or any(path.startswith(p) for p in _PUBLIC_PREFIXES)

class AuthMiddleware(BaseHTTPMiddleware):
    """Require a valid session AND an unlocked DB for every non-public route.
    After a restart the DB is locked, so even a still-valid cookie is bounced to
    login until the password re-unlocks the database."""
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if _is_public(path):
            return await call_next(request)
        if request.session.get("authenticated") and database.is_unlocked():
            return await call_next(request)
        accept = request.headers.get("accept", "")
        if request.method == "GET" and ("text/html" in accept or path == "/" or path.startswith("/app")):
            return RedirectResponse(url="/login.html")
        return JSONResponse({"detail": "Not authenticated"}, status_code=401)

# add_middleware prepends, so the LAST added runs first. Desired request order:
# CORS -> Session -> Auth -> CacheControl -> GZip -> app.
app.add_middleware(GZipMiddleware, minimum_size=500)
app.add_middleware(CacheControlMiddleware)
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=security.get_session_secret(),
    same_site="lax",
    https_only=False,           # set True behind HTTPS
    max_age=14 * 24 * 3600,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins (for development)
    allow_credentials=True,
    allow_methods=["*"],  # Allow all methods (GET, POST, etc.)
    allow_headers=["*"],  # Allow all headers
)


# --- Authentication endpoints -------------------------------------------------

@app.get("/auth/status")
def auth_status(request: Request):
    """Tells the frontend whether to show first-run setup, login, or the app."""
    return {
        "initialised": security.is_initialised(),
        "authenticated": bool(request.session.get("authenticated")),
        "unlocked": database.is_unlocked(),
    }

@app.post("/auth/setup")
def auth_setup(request: Request, payload: schemas.SetupIn):
    """First-run: choose a password, encrypt the existing database, log in.
    Returns the recovery code (shown once)."""
    if security.is_initialised():
        raise HTTPException(status_code=400, detail="Already set up.")
    if not payload.password:
        raise HTTPException(status_code=400, detail="Password must not be empty.")
    dek_hex, recovery_code = security.setup(payload.password)
    try:
        _encrypt_existing_database(dek_hex)
    except Exception as e:
        # Roll back the keyfile so the user can retry cleanly.
        try: os.remove(security.KEYFILE)
        except OSError: pass
        raise HTTPException(status_code=500, detail=f"Could not encrypt the database: {e}")
    database.unlock(dek_hex)
    request.session["authenticated"] = True
    return {"recovery_code": recovery_code}

@app.post("/auth/login")
def auth_login(request: Request, payload: schemas.LoginIn):
    if not security.is_initialised():
        raise HTTPException(status_code=400, detail="Not set up yet.")
    try:
        dek_hex = security.unlock_with_password(payload.password)
    except security.InvalidCredential:
        raise HTTPException(status_code=401, detail="Incorrect password.")
    database.unlock(dek_hex)
    request.session["authenticated"] = True
    return {"ok": True}

@app.post("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return {"ok": True}

@app.post("/auth/recover")
def auth_recover(request: Request, payload: schemas.RecoverIn):
    """Set a new password using the recovery code, then log in."""
    if not security.is_initialised():
        raise HTTPException(status_code=400, detail="Not set up yet.")
    try:
        dek_hex = security.reset_password_with_recovery(payload.recovery_code, payload.new_password)
    except security.InvalidCredential:
        raise HTTPException(status_code=401, detail="Incorrect recovery code.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    database.unlock(dek_hex)
    request.session["authenticated"] = True
    return {"ok": True}

@app.post("/auth/change-password")
def auth_change_password(payload: schemas.ChangePasswordIn):
    """Change the password (re-wraps the data key; the DB is not re-encrypted)."""
    try:
        security.change_password(payload.old_password, payload.new_password)
    except security.InvalidCredential:
        # 400 (not 401) so the UI's global "401 -> /login.html" handler doesn't
        # kick the user out of their session over a wrong field.
        raise HTTPException(status_code=400, detail="Incorrect current password.")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}

@app.get("/login.html")
def login_page():
    return FileResponse("frontend/login.html")


def _encrypt_existing_database(dek_hex: str) -> None:
    """Convert the current plaintext finance.db into a SQLCipher database keyed
    with dek_hex. No-op if the DB doesn't exist yet (a fresh encrypted DB is then
    created on unlock). Verifies the encrypted copy opens before replacing."""
    import sqlcipher3.dbapi2 as sqlcipher
    plain = database.DB_PATH
    if not os.path.exists(plain):
        return
    enc = plain + ".enc.tmp"
    if os.path.exists(enc):
        os.remove(enc)
    con = sqlcipher.connect(plain)           # opens the plaintext DB (no key)
    try:
        con.execute(f"ATTACH DATABASE '{enc}' AS encrypted KEY \"x'{dek_hex}'\"")
        con.execute("SELECT sqlcipher_export('encrypted')")
        con.execute("DETACH DATABASE encrypted")
    finally:
        con.close()
    # Verify the encrypted copy really opens with the key before destroying the original.
    v = sqlcipher.connect(enc)
    try:
        v.execute(f"PRAGMA key = \"x'{dek_hex}'\"")
        v.execute("SELECT count(*) FROM sqlite_master").fetchone()
    finally:
        v.close()
    for suffix in ("-wal", "-shm"):
        p = plain + suffix
        if os.path.exists(p):
            try: os.remove(p)
            except OSError: pass
    os.replace(enc, plain)

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

    # Bulk stats so we don't run a query per payee.
    tx_counts = dict(
        db.query(Transaction.payee_id, func.count(Transaction.id))
        .filter(Transaction.payee_id.isnot(None))
        .group_by(Transaction.payee_id).all()
    )

    # (payee, category) counts, to derive each payee's most-used categories.
    per_payee_cats = {}
    for pid, cid, cnt in (
        db.query(Transaction.payee_id, Transaction.category_id, func.count(Transaction.id))
        .filter(Transaction.payee_id.isnot(None), Transaction.category_id.isnot(None))
        .group_by(Transaction.payee_id, Transaction.category_id).all()
    ):
        per_payee_cats.setdefault(pid, []).append((cid, cnt))

    cat_info = {c.id: (c.name, c.parent) for c in db.query(Category).all()}

    result = []
    for payee in payees:
        top = sorted(per_payee_cats.get(payee.id, []), key=lambda x: x[1], reverse=True)[:3]
        top_categories = [
            {
                "category_id": cid,
                "name": cat_info.get(cid, (None, None))[0],
                "parent": cat_info.get(cid, (None, None))[1],
                "count": cnt,
            }
            for cid, cnt in top
        ]
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
            "transaction_count": tx_counts.get(payee.id, 0),
            "top_categories": top_categories,
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
    This can be triggered from the 'Manage Payees' interface and is also part of
    the nightly maintenance job (shared logic in maintenance.py).
    """
    total = maintenance.recalculate_all_payee_stats(db)
    db.commit()
    return {
        "message": "All payee statistics recalculated",
        "total_payees": total,
        "updated": total,
        "errors": 0
    }


@app.delete("/payees/{payee_id}")
def delete_payee(payee_id: int, db: Session = Depends(get_db)):
    """Delete a payee WITHOUT deleting its transactions: their payee link is
    cleared (set to NULL). Recurring expenses referencing it are unlinked too."""
    payee = db.query(Payee).filter(Payee.id == payee_id).first()
    if not payee:
        raise HTTPException(status_code=404, detail="Payee not found")

    payee_name = payee.name
    tx_cleared = db.query(Transaction).filter(
        Transaction.payee_id == payee_id
    ).update({Transaction.payee_id: None})
    rec_cleared = db.query(RecurringExpense).filter(
        RecurringExpense.payee_id == payee_id
    ).update({RecurringExpense.payee_id: None})

    db.delete(payee)
    db.commit()

    return {
        "deleted": {"id": payee_id, "name": payee_name},
        "transactions_unlinked": tx_cleared,
        "recurring_unlinked": rec_cleared,
    }


@app.get("/payees/duplicates")
def detect_duplicate_payees(db: Session = Depends(get_db)):
    """Detect payees with identical or very similar names."""
    from difflib import SequenceMatcher

    payees = db.query(Payee).order_by(Payee.name).all()
    tx_counts = {}
    for pid, cnt in db.query(Transaction.payee_id, func.count()).filter(
        Transaction.payee_id.isnot(None)
    ).group_by(Transaction.payee_id).all():
        tx_counts[pid] = cnt

    def normalize(name):
        return name.strip().lower()

    groups = []
    seen = set()
    for i, a in enumerate(payees):
        if a.id in seen:
            continue
        norm_a = normalize(a.name)
        matches = []
        for b in payees[i + 1:]:
            if b.id in seen:
                continue
            norm_b = normalize(b.name)
            ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
            if ratio >= 0.82:
                matches.append(b)
                seen.add(b.id)
        if matches:
            seen.add(a.id)
            group = [a] + matches
            # Sort by transaction count descending so the most-used payee is first
            group.sort(key=lambda p: tx_counts.get(p.id, 0), reverse=True)
            groups.append([{
                "id": p.id,
                "name": p.name,
                "transaction_count": tx_counts.get(p.id, 0)
            } for p in group])

    return {"groups": groups}


@app.post("/payees/{payee_id}/merge/{duplicate_id}")
def merge_payees(payee_id: int, duplicate_id: int, db: Session = Depends(get_db)):
    """Merge duplicate payee into the kept payee. Reassigns all transactions and recurring expenses, then deletes the duplicate."""
    keep = db.query(Payee).filter(Payee.id == payee_id).first()
    if not keep:
        raise HTTPException(status_code=404, detail="Payee to keep not found")
    duplicate = db.query(Payee).filter(Payee.id == duplicate_id).first()
    if not duplicate:
        raise HTTPException(status_code=404, detail="Duplicate payee not found")

    # Reassign transactions
    tx_updated = db.query(Transaction).filter(
        Transaction.payee_id == duplicate_id
    ).update({Transaction.payee_id: payee_id})

    # Reassign recurring expenses
    rec_updated = db.query(RecurringExpense).filter(
        RecurringExpense.payee_id == duplicate_id
    ).update({RecurringExpense.payee_id: payee_id})

    # Delete the duplicate
    db.delete(duplicate)
    db.commit()

    return {
        "kept": {"id": keep.id, "name": keep.name},
        "deleted": {"id": duplicate_id, "name": duplicate.name},
        "transactions_reassigned": tx_updated,
        "recurring_reassigned": rec_updated
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
    Retrieve all locations ordered by usage count (most used first).
    """
    counts = dict(
        db.query(models.Transaction.location_id, func.count(models.Transaction.id))
        .filter(models.Transaction.location_id.isnot(None))
        .group_by(models.Transaction.location_id).all()
    )
    result = [
        {"id": loc.id, "name": loc.name, "created_at": loc.created_at,
         "transaction_count": counts.get(loc.id, 0)}
        for loc in db.query(models.Location).all()
    ]
    result.sort(key=lambda x: x["transaction_count"], reverse=True)
    return result[skip:skip + limit]


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


_SYSTEM_LOCATIONS = ("Transfer In", "Transfer Out")


@app.delete("/locations/{location_id}")
def delete_location(location_id: int, db: Session = Depends(get_db)):
    """Delete a location WITHOUT deleting its transactions: their location link is
    cleared (set to NULL). Payee 'most common location' hints are cleared too."""
    loc = db.query(models.Location).filter(models.Location.id == location_id).first()
    if not loc:
        raise HTTPException(status_code=404, detail="Location not found")
    if loc.name in _SYSTEM_LOCATIONS:
        raise HTTPException(status_code=400, detail="Cannot delete a system transfer location")

    name = loc.name
    tx_cleared = db.query(Transaction).filter(
        Transaction.location_id == location_id
    ).update({Transaction.location_id: None})
    db.query(Payee).filter(
        Payee.most_common_location_id == location_id
    ).update({Payee.most_common_location_id: None})

    db.delete(loc)
    db.commit()
    return {"deleted": {"id": location_id, "name": name}, "transactions_unlinked": tx_cleared}


@app.post("/locations/{location_id}/merge/{duplicate_id}")
def merge_locations(location_id: int, duplicate_id: int, db: Session = Depends(get_db)):
    """Merge the duplicate location into the kept one: reassign its transactions
    (and payee hints), then delete the duplicate."""
    if location_id == duplicate_id:
        raise HTTPException(status_code=400, detail="Cannot merge a location into itself")
    keep = db.query(models.Location).filter(models.Location.id == location_id).first()
    if not keep:
        raise HTTPException(status_code=404, detail="Location to keep not found")
    dup = db.query(models.Location).filter(models.Location.id == duplicate_id).first()
    if not dup:
        raise HTTPException(status_code=404, detail="Duplicate location not found")
    if keep.name in _SYSTEM_LOCATIONS or dup.name in _SYSTEM_LOCATIONS:
        raise HTTPException(status_code=400, detail="Cannot merge a system transfer location")

    tx_updated = db.query(Transaction).filter(
        Transaction.location_id == duplicate_id
    ).update({Transaction.location_id: location_id})
    db.query(Payee).filter(
        Payee.most_common_location_id == duplicate_id
    ).update({Payee.most_common_location_id: location_id})

    dup_name = dup.name
    db.delete(dup)
    db.commit()
    return {
        "kept": {"id": keep.id, "name": keep.name},
        "deleted": {"id": duplicate_id, "name": dup_name},
        "transactions_reassigned": tx_updated,
    }


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
    Retrieve all projects ordered by usage count (most used first).
    """
    counts = dict(
        db.query(models.Transaction.project_id, func.count(models.Transaction.id))
        .filter(models.Transaction.project_id.isnot(None))
        .group_by(models.Transaction.project_id).all()
    )
    result = [
        {"id": proj.id, "name": proj.name, "created_at": proj.created_at,
         "transaction_count": counts.get(proj.id, 0)}
        for proj in db.query(models.Project).all()
    ]
    result.sort(key=lambda x: x["transaction_count"], reverse=True)
    return result[skip:skip + limit]


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


@app.delete("/projects/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    """Delete a project WITHOUT deleting its transactions: their project link is
    cleared (set to NULL). Payee 'most common project' hints are cleared too."""
    proj = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not proj:
        raise HTTPException(status_code=404, detail="Project not found")

    name = proj.name
    tx_cleared = db.query(Transaction).filter(
        Transaction.project_id == project_id
    ).update({Transaction.project_id: None})
    db.query(Payee).filter(
        Payee.most_common_project_id == project_id
    ).update({Payee.most_common_project_id: None})

    db.delete(proj)
    db.commit()
    return {"deleted": {"id": project_id, "name": name}, "transactions_unlinked": tx_cleared}


@app.post("/projects/{project_id}/merge/{duplicate_id}")
def merge_projects(project_id: int, duplicate_id: int, db: Session = Depends(get_db)):
    """Merge the duplicate project into the kept one: reassign its transactions
    (and payee hints), then delete the duplicate."""
    if project_id == duplicate_id:
        raise HTTPException(status_code=400, detail="Cannot merge a project into itself")
    keep = db.query(models.Project).filter(models.Project.id == project_id).first()
    if not keep:
        raise HTTPException(status_code=404, detail="Project to keep not found")
    dup = db.query(models.Project).filter(models.Project.id == duplicate_id).first()
    if not dup:
        raise HTTPException(status_code=404, detail="Duplicate project not found")

    tx_updated = db.query(Transaction).filter(
        Transaction.project_id == duplicate_id
    ).update({Transaction.project_id: project_id})
    db.query(Payee).filter(
        Payee.most_common_project_id == duplicate_id
    ).update({Payee.most_common_project_id: project_id})

    dup_name = dup.name
    db.delete(dup)
    db.commit()
    return {
        "kept": {"id": keep.id, "name": keep.name},
        "deleted": {"id": duplicate_id, "name": dup_name},
        "transactions_reassigned": tx_updated,
    }


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
    search: Optional[str] = None,                # text search (payee.name or note)
    db: Session = Depends(get_db)
):
    """
    Retrieve transactions with optional filters. Returns enriched transactions with entity names.

    - Uses joinedload(...) to avoid N+1 queries.
    - Supports pagination with skip & limit (useful for infinite scroll).
    - `search` filters on payee.name and transaction.note in the backend.
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
            "split_group_id": trans.split_group_id,
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



@app.get("/transactions/summary")
def get_transactions_summary(
    account_id: Optional[int] = None,
    category_id: Optional[int] = None,
    payee_id: Optional[int] = None,
    location_id: Optional[int] = None,
    project_id: Optional[int] = None,
    currency: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """
    Totals for the current filter set (same filters as GET /transactions): how many
    transactions match, and money in / money out converted to the base currency
    (GBP) at historical rates. Transfers are excluded so the in/out figures reflect
    real income and spending, not money moved between own accounts.
    """
    base_currency = get_base_currency(db)

    transfer_ids = [
        r.id for r in db.query(models.Location.id)
        .filter(models.Location.name.in_(["Transfer In", "Transfer Out"]))
        .all()
    ]

    query = db.query(models.Transaction)
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
    if transfer_ids:
        # A transaction with no location must still count: SQL evaluates
        # "NOT IN" as NULL, not true, when the column itself is NULL.
        query = query.filter(or_(models.Transaction.location_id.is_(None),
                                 ~models.Transaction.location_id.in_(transfer_ids)))
    if search:
        pattern = f"%{search}%"
        query = query.outerjoin(models.Payee).filter(
            or_(models.Payee.name.ilike(pattern), models.Transaction.note.ilike(pattern))
        )

    transactions = query.all()
    if not transactions:
        return {"count": 0, "money_in": 0, "money_out": 0, "base_currency": base_currency}

    # Convert every matched transaction to GBP at that day's historical rate.
    dates = [_to_date(t.date) for t in transactions]
    currencies = list({t.currency for t in transactions if t.currency})
    historical_rates = get_rates_bulk(db, currencies, min(dates), max(dates))

    money_in = 0.0
    money_out = 0.0
    for t in transactions:
        rates = historical_rates.get(_to_date(t.date), {'GBP': 1.0})
        trans_rate = rates.get(t.currency, 1.0)
        base_rate = rates.get(base_currency, 1.0)
        converted = t.amount * (base_rate / trans_rate)
        if converted >= 0:
            money_in += converted
        else:
            money_out += -converted

    # A split is one transaction to the user, however many lines it holds.
    split_groups = {t.split_group_id for t in transactions if t.split_group_id}
    count = sum(1 for t in transactions if not t.split_group_id) + len(split_groups)

    return {
        "count": count,
        "money_in": round(money_in, 2),
        "money_out": round(money_out, 2),
        "base_currency": base_currency,
    }


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

        # Find matching transfer_in (same date, different account, not yet processed).
        # Prefer one with matching amount to disambiguate multiple transfers on the same date.
        available = [
            t for t in candidates
            if t.id not in processed_ids and t.account_id != trans_out.account_id
        ]
        matching = next(
            (t for t in available if abs(trans_out.amount) == t.amount),
            None
        ) or (available[0] if available else None)
        
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


# ============================================
# SPLIT TRANSACTIONS
#
# One purchase whose lines belong to different categories, projects or notes.
# Each line is stored as an ordinary transaction row; what ties them together
# is a shared ``split_group_id`` — the id of the lowest-numbered line. Because
# the lines are real transactions, balances, filters, budgets and every
# category report keep working without knowing that splits exist; only the
# ledger view and these endpoints treat a group as a single entry.
# ============================================

def _split_lines(db: Session, group_id: int) -> List[models.Transaction]:
    """The lines of a split, in entry order."""
    return db.query(models.Transaction).filter(
        models.Transaction.split_group_id == group_id
    ).order_by(models.Transaction.id.asc()).all()


def _reanchor_split(db: Session, group_id: Optional[int]) -> Optional[int]:
    """
    Keep a split keyed by its lowest surviving line, so the key can never point
    at a deleted row (whose id SQLite may hand out again). A group down to a
    single line is no longer a split and is dissolved back into a plain
    transaction. Returns the group id that survives, or None.
    """
    if not group_id:
        return None
    lines = _split_lines(db, group_id)
    if not lines:
        return None
    if len(lines) == 1:
        lines[0].split_group_id = None
        return None
    anchor = min(line.id for line in lines)
    if anchor != group_id:
        for line in lines:
            line.split_group_id = anchor
    return anchor


def _recalculate_from_date(db: Session, earliest_date, account_ids: List[int]) -> None:
    """
    Recalculate balances from the first transaction at or after a date.

    Anchoring on the *first* row at that date rather than the last one before it
    matters as soon as several transactions share a timestamp — which is exactly
    what the lines of a split do. Recalculation runs from the trigger forward,
    so the trigger has to be the earliest row that could have changed.
    """
    trigger = db.query(models.Transaction).filter(
        models.Transaction.date >= earliest_date
    ).order_by(models.Transaction.date.asc(), models.Transaction.id.asc()).first()
    if trigger:
        recalculate_balances_from_transaction(db, trigger.id, account_ids)
    else:
        initialise_all_balances(db)


def _serialise_split(db: Session, group_id: int, line_ids: Optional[List[int]] = None) -> dict:
    """
    Present a group of lines as one transaction with a breakdown.

    ``line_ids`` names the rows to read when the group no longer exists —
    an update that leaves a single line behind dissolves the split, and the
    caller still needs the result of what it just saved.
    """
    query = db.query(models.Transaction).options(
        joinedload(models.Transaction.account),
        joinedload(models.Transaction.category),
        joinedload(models.Transaction.payee),
        joinedload(models.Transaction.location),
        joinedload(models.Transaction.project),
    )
    lines = query.filter(
        models.Transaction.split_group_id == group_id
    ).order_by(models.Transaction.id.asc()).all()

    if not lines and line_ids:
        lines = query.filter(
            models.Transaction.id.in_(line_ids)
        ).order_by(models.Transaction.id.asc()).all()
        if lines:
            group_id = lines[0].id

    if not lines:
        raise HTTPException(status_code=404, detail="Split transaction not found")

    head = lines[0]
    last = lines[-1]
    return {
        "split_group_id": group_id,
        "date": head.date.isoformat() if hasattr(head.date, "isoformat") else str(head.date),
        "account_id": head.account_id,
        "account_name": head.account.name if head.account else None,
        "currency": head.currency or "GBP",
        "payee_id": head.payee_id,
        "payee_name": head.payee.name if head.payee else None,
        "location_id": head.location_id,
        "location_name": head.location.name if head.location else None,
        "amount": round(sum(float(line.amount or 0.0) for line in lines), 2),
        "account_balance_after": last.account_balance_after,
        "total_balance_after": last.total_balance_after,
        "lines": [
            {
                "id": line.id,
                "amount": float(line.amount) if line.amount is not None else 0.0,
                "category_id": line.category_id,
                "category_name": line.category.name if line.category else None,
                "category_parent": line.category.parent if line.category else None,
                "project_id": line.project_id,
                "project_name": line.project.name if line.project else None,
                "note": line.note,
            }
            for line in lines
        ],
    }


@app.post("/transactions/split", response_model=schemas.SplitTransactionResponse)
def create_split_transaction(
    payload: schemas.SplitTransactionCreate,
    skip_recalculation: bool = Query(False),
    db: Session = Depends(get_db)
):
    """Create a split: one row per line, all sharing a new group id."""
    if len(payload.lines) < 2:
        raise HTTPException(status_code=400,
                            detail="A split transaction needs at least two lines")
    rows = []
    for line in payload.lines:
        row = models.Transaction(
            date=payload.date,
            amount=line.amount,
            currency=payload.currency or "GBP",
            note=line.note,
            account_id=payload.account_id,
            category_id=line.category_id,
            payee_id=payload.payee_id,
            location_id=payload.location_id,
            project_id=line.project_id,
        )
        db.add(row)
        rows.append(row)

    try:
        db.flush()
        group_id = min(row.id for row in rows)
        for row in rows:
            row.split_group_id = group_id
        db.flush()
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error during insert: {str(e)}")

    if not skip_recalculation:
        try:
            recalculate_balances_from_transaction(db, group_id)
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Calculation error: {str(e)}")
    db.commit()

    return _serialise_split(db, group_id)


@app.get("/transactions/split/{group_id}", response_model=schemas.SplitTransactionResponse)
def get_split_transaction(group_id: int, db: Session = Depends(get_db)):
    """Retrieve a split as a single entry with its breakdown."""
    return _serialise_split(db, group_id)


@app.put("/transactions/split/{group_id}", response_model=schemas.SplitTransactionResponse)
def update_split_transaction(
    group_id: int,
    payload: schemas.SplitTransactionCreate,
    db: Session = Depends(get_db)
):
    """
    Replace a split's lines. A line sent with an ``id`` is updated in place (so
    it keeps its position in the ledger), a line without one is added, and any
    existing line the payload leaves out is deleted.

    Pointing this at a plain transaction's id splits it: the row becomes the
    first line of a new group keyed on itself, which is how the UI turns a
    transaction it is already editing into a split without a delete-and-recreate.
    """
    existing = {line.id: line for line in _split_lines(db, group_id)}
    if not existing:
        plain = db.query(models.Transaction).filter(
            models.Transaction.id == group_id,
            models.Transaction.split_group_id.is_(None),
        ).first()
        if plain is None:
            raise HTTPException(status_code=404, detail="Split transaction not found")
        plain.split_group_id = group_id
        existing = {plain.id: plain}

    head = next(iter(existing.values()))
    old_account_id = head.account_id
    old_date = head.date

    kept = set()
    touched = []
    for line in payload.lines:
        row = existing.get(line.id) if line.id else None
        if row is None:
            row = models.Transaction(split_group_id=group_id)
            db.add(row)
        else:
            kept.add(row.id)
        touched.append(row)
        row.date = payload.date
        row.amount = line.amount
        row.currency = payload.currency or "GBP"
        row.note = line.note
        row.account_id = payload.account_id
        row.category_id = line.category_id
        row.payee_id = payload.payee_id
        row.location_id = payload.location_id
        row.project_id = line.project_id
        row.updated_at = datetime.utcnow()

    for line_id, row in existing.items():
        if line_id not in kept:
            db.delete(row)

    db.flush()
    surviving_ids = [row.id for row in touched]
    # Deleting the line the group was keyed on moves the key to the next one;
    # coming down to a single line dissolves the split altogether.
    group_id = _reanchor_split(db, group_id) or group_id
    db.flush()

    affected = list({old_account_id, payload.account_id})
    _recalculate_from_date(db, min(old_date, payload.date), affected)
    db.commit()

    return _serialise_split(db, group_id, line_ids=surviving_ids)


@app.delete("/transactions/split/{group_id}")
def delete_split_transaction(group_id: int, db: Session = Depends(get_db)):
    """Delete every line of a split in one go."""
    lines = _split_lines(db, group_id)
    if not lines:
        raise HTTPException(status_code=404, detail="Split transaction not found")

    account_ids = list({line.account_id for line in lines})
    earliest = min(line.date for line in lines)
    for line in lines:
        db.delete(line)
    db.flush()

    _recalculate_from_date(db, earliest, account_ids)
    db.commit()
    return {"message": f"Split transaction deleted ({len(lines)} lines)"}


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

        if not exists:
            # A split is stored one row per line, so a statement line for the
            # whole purchase matches the group's total, never a single row.
            # Without this, importing the bank's CSV would duplicate every
            # transaction the user had already split by hand.
            totals = db.query(
                func.sum(Transaction.amount).label("total")
            ).filter(
                func.date(Transaction.date) == parsed_date.date(),
                Transaction.account_id == duplicate_check.account_id,
                Transaction.split_group_id.isnot(None),
            ).group_by(Transaction.split_group_id).all()
            target = round(float(duplicate_check.amount), 2)
            exists = any(round(float(t.total or 0), 2) == target for t in totals)

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
            Transaction.account_id,
            Transaction.split_group_id
        ).filter(
            Transaction.account_id.in_(account_ids),
            func.date(Transaction.date) >= min_date.isoformat(),
            func.date(Transaction.date) <= max_date.isoformat()
        ).all()

        # Build a set of (date_str, amount, account_id) tuples for O(1) lookup
        # Convert date to YYYY-MM-DD string format for consistent comparison
        existing_set = set()
        split_totals = {}
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

            # A split is one purchase spread over several rows. The bank knows
            # only the total, so index that too — otherwise re-importing the
            # statement would duplicate everything the user had split by hand.
            if tx.split_group_id:
                key = (tx_date_str, tx.account_id, tx.split_group_id)
                split_totals[key] = split_totals.get(key, 0.0) + float(tx.amount)

        for (tx_date_str, account_id, _), total in split_totals.items():
            existing_set.add((tx_date_str, round(total, 2), account_id))

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
            db.commit()  # Commit after recalculation
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

    # Do NOT commit here — that happens after the recalculation
    db.flush()  # Flush so the changes are visible to the following queries

    # Recalculate balances from the EARLIEST date for both accounts
    affected_account_ids = list(set([old_account_id, transaction.account_id]))
    earliest_date = min(old_date, db_transaction.date)

    _recalculate_from_date(db, earliest_date, affected_account_ids)
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
    split_group_id = db_transaction.split_group_id

    # Delete the transaction
    db.delete(db_transaction)
    db.flush()  # Flush instead of commit to keep the transaction open

    # Deleting one line of a split leaves the rest of the group standing.
    _reanchor_split(db, split_group_id)
    db.flush()

    # Find the next transaction after the deleted one to trigger recalculation
    next_transaction = db.query(models.Transaction).filter(
        models.Transaction.account_id == affected_account_id,
        models.Transaction.date >= transaction_date
    ).order_by(models.Transaction.date.asc(), models.Transaction.id.asc()).first()

    if next_transaction:
        recalculate_balances_from_transaction(db, next_transaction.id, [affected_account_id])
    else:
        # If no transactions remain for this account, reset current_balance
        account = db.query(models.Account).filter(
            models.Account.id == affected_account_id
        ).first()
        if account:
            account.current_balance = account.initial_balance
        # Still need to recalculate total_balance_after for all other transactions
        recalculate_balances_for_accounts(db, [])
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
    affected_splits = set()
    deleted_count = 0
    not_found = []

    for tx_id in transaction_ids:
        tx = db.query(models.Transaction).filter(
            models.Transaction.id == tx_id
        ).first()

        if tx:
            affected_accounts.add(tx.account_id)
            if tx.split_group_id:
                affected_splits.add(tx.split_group_id)
            db.delete(tx)
            deleted_count += 1
        else:
            not_found.append(tx_id)

    # Flush deletions before recalculating
    db.flush()

    # Re-key any split that lost lines, and dissolve one-line leftovers.
    for group_id in affected_splits:
        _reanchor_split(db, group_id)
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
    Recalculate balances for specific accounts and total portfolio balance.
    """
    from backend.helpers import get_latest_rates, get_base_currency, convert_to_base_currency

    # Step 1: Recalculate account balances for affected accounts
    for account_id in account_ids:
        account = db.query(models.Account).filter(
            models.Account.id == account_id
        ).first()

        if not account:
            continue

        transactions = db.query(models.Transaction).filter(
            models.Transaction.account_id == account_id
        ).order_by(
            models.Transaction.date.asc(),
            models.Transaction.id.asc()
        ).all()

        running_balance = float(account.initial_balance) if account.initial_balance else 0.0

        for tx in transactions:
            if tx.amount is not None:
                running_balance += float(tx.amount)
            tx.account_balance_after = round(running_balance, 2)

        account.current_balance = round(running_balance, 2)

    # Step 2: Recalculate total portfolio balance across all accounts
    rates = get_latest_rates(db)
    base_currency = get_base_currency(db)

    all_transactions = db.query(models.Transaction).order_by(
        models.Transaction.date.asc(), models.Transaction.id.asc()
    ).all()

    total_balance = 0.0
    for tx in all_transactions:
        converted = convert_to_base_currency(
            float(tx.amount or 0.0), tx.currency, base_currency, rates
        )
        total_balance += converted
        tx.total_balance_after = round(total_balance, 2)


class RecalculateBalancesRequest(BaseModel):
    """Schema for recalculate balances request."""
    account_ids: List[int]
    since: Optional[str] = None  # ISO datetime — recalculate only from this date forward


@app.post("/admin/recalculate-balances-for-accounts")
def recalculate_balances_for_accounts_endpoint(
    request: RecalculateBalancesRequest,
    db: Session = Depends(get_db)
):
    """
    Recalculate balances for specific accounts.
    If `since` is provided, only recalculates from that date forward (incremental).
    """
    if not request.account_ids:
        return {"message": "No accounts to recalculate", "accounts_processed": 0}

    try:
        if request.since:
            # Incremental: find the earliest transaction at or after `since` and use optimised path
            since_dt = datetime.fromisoformat(request.since)
            trigger = db.query(models.Transaction).filter(
                models.Transaction.account_id.in_(request.account_ids),
                models.Transaction.date >= since_dt
            ).order_by(models.Transaction.date.asc(), models.Transaction.id.asc()).first()
            if trigger:
                recalculate_balances_from_transaction(db, trigger.id, request.account_ids)
            # If no transactions found from that date, nothing to recalculate
        else:
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

    from backend.helpers import get_base_currency

    return {
        "base_currency": get_base_currency(db),
        "rates": rates_dict,
        "last_updated": rates_query[0].date.isoformat() if rates_query else None
    }


@app.post("/exchange-rates/update")
def trigger_exchange_rate_update(db: Session = Depends(get_db)):
    """
    Manually trigger an exchange rate update.
    """
    try:
        from backend.update_exchange_rates import update_exchange_rates
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
    """Get summary counts for the dashboard. Balance KPIs are driven by the networth endpoint."""
    base_currency = get_base_currency(db)

    total_transactions = db.query(sql_func.count(Transaction.id)).scalar()
    total_accounts = db.query(sql_func.count(Account.id)).filter(
        Account.is_active == 1
    ).scalar()
    total_categories = db.query(sql_func.count(Category.id)).scalar()

    return {
        "total_transactions": total_transactions,
        "total_accounts": total_accounts,
        "total_categories": total_categories,
        "base_currency": base_currency,
        "rates_available": len(get_latest_rates(db)) > 0
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
            "base_currency": get_base_currency(db)
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

    # Accounts that never appear in a transaction still hold money, and their
    # currency may not be used anywhere else — without its rate the conversion
    # below would quietly fall back to 1.0.
    untouched_accounts = []
    if not date_from:
        touched_ids = {t.account_id for t in transactions}
        untouched_q = db.query(Account).filter(Account.is_active == 1)
        if touched_ids:
            untouched_q = untouched_q.filter(~Account.id.in_(touched_ids))
        if excluded_ids:
            untouched_q = untouched_q.filter(~Account.id.in_(excluded_ids))
        untouched_accounts = [a for a in untouched_q.all() if a.initial_balance]

    # Add account currencies for baseline calculation
    if date_from:
        account_currencies = db.query(Account.currency).distinct().all()
        for c in account_currencies:
            if c[0] and c[0] not in currencies:
                currencies.append(c[0])
    else:
        for acc in untouched_accounts:
            if acc.currency and acc.currency not in currencies:
                currencies.append(acc.currency)

    # Load historical exchange rates (BULK)
    historical_rates = get_rates_bulk(db, currencies, min_trans_date, max_trans_date)

    base_currency = get_base_currency(db)

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

    # Pre-load initial balances and currencies for accounts that have transactions
    account_initial = {}
    if not date_from:
        account_ids_in_range = set(t.account_id for t in transactions)
        for acc in db.query(Account).filter(Account.id.in_(account_ids_in_range)).all():
            if acc.initial_balance:
                account_initial[acc.id] = (float(acc.initial_balance), acc.currency)

        # An untouched account holds its opening balance for the whole timeline,
        # so it is seeded here rather than "on first appearance" — it never
        # appears. Without this, "all time" reported a smaller total than any
        # dated range over the very same data.
        opening_rates = historical_rates.get(min_trans_date, {}) or {}
        opening_base_rate = opening_rates.get(base_currency, 1.0)
        for acc in untouched_accounts:
            acc_rate = opening_rates.get(acc.currency, 1.0)
            account_balances[acc.id] = float(acc.initial_balance) * (opening_base_rate / acc_rate)

    # Process transactions with HISTORICAL rates
    for trans in transactions:
        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})

        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted_amount = trans.amount * (base_rate / trans_rate)

        if trans.account_id not in account_balances:
            # Include initial_balance on first appearance (all-time mode only)
            init_bal = 0.0
            if trans.account_id in account_initial:
                init_native, init_currency = account_initial[trans.account_id]
                init_rate = rates_for_day.get(init_currency, 1.0)
                init_bal = init_native * (base_rate / init_rate)
            account_balances[trans.account_id] = init_bal
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
            if month_key not in monthly_data or point['date'] >= monthly_data[month_key]['date']:
                monthly_data[month_key] = point
        aggregated_data = sorted(monthly_data.values(), key=lambda x: x['date'])
    elif period == "weekly":
        weekly_data = {}
        for point in all_balance_points:
            if point['date'] is None:
                continue
            week_start = point['date'] - timedelta(days=point['date'].weekday())
            week_key = week_start.strftime('%Y-%m-%d')
            if week_key not in weekly_data or point['date'] >= weekly_data[week_key]['date']:
                weekly_data[week_key] = point
        aggregated_data = sorted(weekly_data.values(), key=lambda x: x['date'])
    else:  # daily — keep last point per day (highest cumulative balance accuracy)
        daily_data = {}
        for point in all_balance_points:
            if point['date'] is None:
                continue
            day_key = point['date'].strftime('%Y-%m-%d')
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
    base_currency = get_base_currency(db)

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

def _parse_year_month(year_month: str):
    """Parse 'YYYY-MM' into the first and last calendar dates of that month."""
    try:
        year, month = map(int, year_month.split('-'))
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        return start_date, end_date
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid year_month format. Use YYYY-MM")


def _collect_month_expenses(db: Session, start_date, end_date):
    """Collect every expense transaction in [start_date, end_date], converted to
    the base currency (GBP) with that day's historical rate and excluding
    transfers. Returns (expenses, base_currency); each expense is a dict whose
    `amount`/`original_amount` are positive and unrounded."""
    base_currency = get_base_currency(db)

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
        # A transaction with no location must still count: SQL evaluates
        # "NOT IN" as NULL, not true, when the column itself is NULL.
        filters.append(or_(Transaction.location_id.is_(None),
                           ~Transaction.location_id.in_(transfer_ids)))

    transactions = db.query(Transaction).filter(and_(*filters)).all()
    if not transactions:
        return [], base_currency

    currencies = list(set([t.currency for t in transactions if t.currency]))
    historical_rates = get_rates_bulk(db, currencies, start_date, end_date)

    expenses = []
    for trans in transactions:
        trans_date = _to_date(trans.date)
        rates_for_day = historical_rates.get(trans_date, {'GBP': 1.0})
        trans_rate = rates_for_day.get(trans.currency, 1.0)
        base_rate = rates_for_day.get(base_currency, 1.0)
        converted = trans.amount * (base_rate / trans_rate)

        if converted > 0:
            continue  # income

        cat_name = trans.category.name if trans.category else "Uncategorised"
        parent_name = trans.category.parent if (trans.category and trans.category.parent) else cat_name

        expenses.append({
            "id": trans.id,
            "date": trans_date.isoformat(),
            "amount": abs(converted),
            "original_amount": abs(trans.amount),
            "currency": trans.currency,
            "category": cat_name,
            "parent_category": parent_name,
            "payee": trans.payee.name if trans.payee else "Unknown",
            "account": trans.account.name if trans.account else None,
            "location": trans.location.name if trans.location else None,
            "project": trans.project.name if trans.project else None,
            "note": trans.note,
        })

    return expenses, base_currency


@app.get("/dashboard/categories/breakdown/{year_month}")
def get_monthly_category_breakdown(
    year_month: str,
    view_mode: str = Query("top", description="View mode: 'top' for top expenses, 'category' for parent categories, 'subcategory' for full category names"),
    db: Session = Depends(get_db)
):
    """
    Get category breakdown for a month with historical exchange rates.
    """
    start_date, end_date = _parse_year_month(year_month)
    all_expenses, base_currency = _collect_month_expenses(db, start_date, end_date)

    if not all_expenses:
        return {
            "month": year_month,
            "categories": [],
            "top_expenses": [],
            "summary": {
                "total_spent": 0,
                "num_categories": 0,
                "num_transactions": 0
            },
            "base_currency": base_currency
        }

    category_data = {}
    total_expenses = 0

    for e in all_expenses:
        total_expenses += e["amount"]
        cat_key = e["parent_category"] if view_mode == "category" else e["category"]
        bucket = category_data.setdefault(cat_key, {"name": cat_key, "amount": 0, "transaction_count": 0})
        bucket["amount"] += e["amount"]
        bucket["transaction_count"] += 1

    categories = sorted(category_data.values(), key=lambda x: x["amount"], reverse=True)[:20]

    for category in categories:
        category["percentage"] = round((category["amount"] / total_expenses * 100), 1) if total_expenses > 0 else 0
        category["amount"] = round(category["amount"], 2)

    # Top 10, rounded for display (the full list is served by the /expenses endpoint).
    top_expenses = sorted(all_expenses, key=lambda x: x["amount"], reverse=True)[:10]
    top_expenses = [
        {**e, "amount": round(e["amount"], 2), "original_amount": round(e["original_amount"], 2)}
        for e in top_expenses
    ]

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


@app.get("/dashboard/categories/breakdown/{year_month}/expenses")
def get_monthly_category_expenses(
    year_month: str,
    db: Session = Depends(get_db)
):
    """
    Full list of the month's expense transactions (converted to base currency,
    transfers excluded), sorted by amount descending. Powers the per-category
    detail modal on the dashboard; the client filters by category/parent.
    """
    start_date, end_date = _parse_year_month(year_month)
    all_expenses, base_currency = _collect_month_expenses(db, start_date, end_date)
    for e in all_expenses:
        e["amount"] = round(e["amount"], 2)
        e["original_amount"] = round(e["original_amount"], 2)
    all_expenses.sort(key=lambda x: x["amount"], reverse=True)
    return {
        "month": year_month,
        "expenses": all_expenses,
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
            "base_currency": get_base_currency(db)
        }

    currencies = list(set([t.currency for t in transactions if t.currency]))
    historical_rates = get_rates_bulk(db, currencies, start_date, end_date)
    base_currency = get_base_currency(db)

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
        return {"payees": [], "base_currency": get_base_currency(db)}

    min_date = _to_date(min(t.date for t in transactions))
    max_date = _to_date(max(t.date for t in transactions))

    currencies = list(set([t.currency for t in transactions if t.currency]))
    historical_rates = get_rates_bulk(db, currencies, min_date, max_date)
    base_currency = get_base_currency(db)

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
        # A transaction with no location must still count: SQL evaluates
        # "NOT IN" as NULL, not true, when the column itself is NULL.
        filters.append(or_(Transaction.location_id.is_(None),
                           ~Transaction.location_id.in_(transfer_ids)))

    # Get transactions
    transactions = db.query(Transaction).filter(and_(*filters)).all()

    if not transactions:
        return {"locations": [], "base_currency": get_base_currency(db)}

    # Date range
    min_date = _to_date(min(t.date for t in transactions))
    max_date = _to_date(max(t.date for t in transactions))

    # Get currencies
    currencies = list(set([t.currency for t in transactions if t.currency]))

    # Load historical rates
    historical_rates = get_rates_bulk(db, currencies, min_date, max_date)
    base_currency = get_base_currency(db)

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

    # Query for unique year-month combinations (with transaction counts)
    query = db.query(
        sql_func.strftime('%Y-%m', Transaction.date).label('month'),
        sql_func.count(Transaction.id).label('count')
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
            "month": int(month),
            "count": row.count
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
        return {"items": [], "base_currency": get_base_currency(db)}

    # Take top by absolute amount
    transactions = transactions[:limit * 2]  # Get extra to ensure we have enough after conversion

    # Date range
    min_date = _to_date(min(t.date for t in transactions))
    max_date = _to_date(max(t.date for t in transactions))

    # Get currencies
    currencies = list(set([t.currency for t in transactions if t.currency]))

    # Load historical rates
    historical_rates = get_rates_bulk(db, currencies, min_date, max_date)
    base_currency = get_base_currency(db)

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

@app.get("/loans/account-ids")
def get_loan_account_ids(db: Session = Depends(get_db)):
    """Return the IDs of accounts detected as loans (not credit cards)."""
    CREDIT_CARD_PAYEE_THRESHOLD = 3
    transfer_locations = db.query(Location.id).filter(
        Location.name.in_(["Transfer In", "Transfer Out"])
    ).all()
    transfer_location_ids = set(loc.id for loc in transfer_locations)

    declared_loan_accounts = {row[0] for row in db.query(Loan.account_id).all()}

    loan_ids = []
    for account in db.query(Account).all():
        if account.id in declared_loan_accounts:
            loan_ids.append(account.id)
            continue
        first_tx = db.query(Transaction).filter(
            Transaction.account_id == account.id
        ).order_by(Transaction.date, Transaction.id).first()
        if not first_tx or first_tx.amount >= 0:
            continue
        payee_query = db.query(Transaction.payee_id).filter(
            Transaction.account_id == account.id,
            Transaction.payee_id != None,
        )
        if transfer_location_ids:
            # A transaction with no location must still count: SQL evaluates
            # "NOT IN" as NULL, not true, when the column itself is NULL.
            payee_query = payee_query.filter(or_(Transaction.location_id.is_(None),
                                                 ~Transaction.location_id.in_(transfer_location_ids)))
        unique_payees = set(p[0] for p in payee_query.distinct().all())
        if len(unique_payees) < CREDIT_CARD_PAYEE_THRESHOLD:
            loan_ids.append(account.id)
    return {"loan_account_ids": loan_ids}


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
    
    base_currency = get_base_currency(db)
    
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

    declared_loan_accounts = {row[0] for row in db.query(Loan.account_id).all()}
    
    for account in all_accounts:
        # Get all transactions for this account, sorted chronologically
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account.id
        ).order_by(Transaction.date, Transaction.id).all()
        
        if not transactions:
            continue
        
        # An account with agreed terms is a loan because it was declared one.
        declared = account.id in declared_loan_accounts

        # Check if account starts with negative transaction (debt account)
        first_transaction = transactions[0]
        if first_transaction.amount >= 0 and not declared:
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
        is_credit_card = (not declared) and len(unique_payees) >= CREDIT_CARD_PAYEE_THRESHOLD

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
    
    base_currency = get_base_currency(db)
    
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

    # Agreed terms, where they have been entered. An account without them keeps
    # being estimated from its movements, exactly as before.
    loan_terms = {loan.account_id: loan for loan in db.query(Loan).all()}
    
    for account in all_accounts:
        # Get all transactions for this account, sorted chronologically
        transactions = db.query(Transaction).filter(
            Transaction.account_id == account.id
        ).order_by(Transaction.date, Transaction.id).all()
        
        # An account with agreed terms is a loan because it was declared one, so
        # the pattern-matching below never gets to overrule it.
        declared = loan_terms.get(account.id)

        if not transactions:
            continue

        # Check if account starts with negative transaction (debt account)
        first_transaction = transactions[0]
        if first_transaction.amount >= 0 and not declared:
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
        is_credit_card = (not declared) and len(unique_payees) >= CREDIT_CARD_PAYEE_THRESHOLD

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
        max_debt = 0  # Track maximum amount owed (most negative balance)

        tx_list = []
        for tx in transactions:
            # Work in original currency
            amount = tx.amount
            balance += amount

            # Track peak debt (most negative balance)
            if balance < -max_debt:
                max_debt = abs(balance)
            
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
            "max_debt": round(max_debt, 2),
            "is_completed": is_completed,
            "open_date": open_date.isoformat() if hasattr(open_date, 'isoformat') else str(open_date),
            "close_date": close_date.isoformat() if close_date and hasattr(close_date, 'isoformat') else None,
            "lender_name": lender_name,
            "unique_payees": len(unique_payees),
            "transactions": tx_list[::-1]
        }

        # With the contract in hand the schedule is arithmetic, so it replaces the
        # guesswork: the agreed rate and lender win over the ones inferred above.
        if declared:
            debt_data["terms"] = loan_engine.as_dict(declared)
            debt_data["schedule"] = loan_engine.summary(declared)
            if declared.lender:
                debt_data["lender_name"] = declared.lender.name

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


def _resolve_lender(db: Session, payload) -> Optional[int]:
    """
    The lender as an ordinary payee, created if the name is new, so repayments can
    be matched to it like any other spending.
    """
    if payload.lender_payee_id:
        return payload.lender_payee_id
    name = (payload.lender_name or "").strip()
    if not name:
        return None
    lender = db.query(Payee).filter(Payee.name == name).first()
    if not lender:
        lender = Payee(name=name)
        db.add(lender)
        db.flush()
    return lender.id


def _apply_terms(loan: Loan, payload) -> None:
    """Copy the agreed terms onto a loan row. Shared by creating and editing."""
    loan.name = payload.name.strip()
    loan.principal = payload.principal
    loan.annual_rate = payload.annual_rate or 0.0
    loan.open_date = payload.open_date
    loan.term_count = payload.term_count
    loan.term_unit = payload.term_unit
    loan.repayment_type = payload.repayment_type
    loan.interest_months = payload.interest_months
    loan.interest_unit = payload.interest_unit
    loan.payment_months = payload.payment_months
    loan.day_rule = payload.day_rule
    loan.day_ordinal = payload.day_ordinal
    loan.day_of_month = payload.day_of_month
    loan.opening_fee = payload.opening_fee or 0.0
    loan.fee_treatment = payload.fee_treatment
    loan.recurring_fee = payload.recurring_fee or 0.0
    loan.recurring_fee_months = payload.recurring_fee_months
    loan.early_repayment_fee_pct = max(0.0, payload.early_repayment_fee_pct or 0.0)


@app.post("/loans")
def create_loan(payload: schemas.LoanCreate, db: Session = Depends(get_db)):
    """
    Record the terms of a loan.

    Opening a new loan does three things at once, because they are one event:
    the debt account is created, the drawdown is booked as a transfer into the
    account the money landed in, and the terms are stored so the amortisation can
    be computed. Passing ``account_id`` instead puts terms on a debt account that
    already exists, leaving its movements untouched.
    """
    if payload.principal <= 0:
        raise HTTPException(status_code=400, detail="The amount borrowed must be greater than zero")

    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="The loan needs a name")

    destination = None
    if payload.disbursement_account_id:
        destination = db.query(Account).filter(
            Account.id == payload.disbursement_account_id
        ).first()
        if not destination:
            raise HTTPException(status_code=404, detail="Account the money was paid into not found")
    elif not payload.account_id:
        # A brand-new loan account with no drawdown would be an account with no
        # movements — invisible on this page and impossible to reconcile.
        raise HTTPException(
            status_code=400,
            detail="Choose the account the money was paid into",
        )

    currency = payload.currency or (destination.currency if destination else get_base_currency(db))

    # Either attach to an existing debt account, or open one for the loan.
    if payload.account_id:
        account = db.query(Account).filter(Account.id == payload.account_id).first()
        if not account:
            raise HTTPException(status_code=404, detail="Account not found")
        if db.query(Loan).filter(Loan.account_id == account.id).first():
            raise HTTPException(status_code=400, detail=f"'{account.name}' already has loan terms")
        currency = account.currency or currency
        created_account = False
    else:
        if db.query(Account).filter(Account.name == name).first():
            raise HTTPException(status_code=400, detail=f"An account called '{name}' already exists")
        account = Account(name=name, type="LIABILITY", currency=currency, initial_balance=0.0)
        db.add(account)
        db.flush()
        created_account = True

    lender_id = _resolve_lender(db, payload)

    loan = Loan(
        account_id=account.id,
        currency=currency,
        lender_payee_id=lender_id,
        disbursement_account_id=destination.id if destination else None,
    )
    _apply_terms(loan, payload)
    db.add(loan)
    db.flush()

    # The drawdown: the debt account goes negative by the capital, the account it
    # was paid into goes up by the same. Only ever booked for an account opened
    # here — an existing one already carries its own history.
    if created_account and payload.create_disbursement and destination:
        transfer_out_loc = db.query(Location).filter(Location.name == "Transfer Out").first()
        if not transfer_out_loc:
            transfer_out_loc = Location(name="Transfer Out")
            db.add(transfer_out_loc)
            db.flush()
        transfer_in_loc = db.query(Location).filter(Location.name == "Transfer In").first()
        if not transfer_in_loc:
            transfer_in_loc = Location(name="Transfer In")
            db.add(transfer_in_loc)
            db.flush()

        note = f"Loan drawdown — {name}"
        out_tx = Transaction(
            date=payload.open_date, amount=-abs(payload.principal), currency=currency,
            account_id=account.id, location_id=transfer_out_loc.id, note=note,
        )
        in_tx = Transaction(
            date=payload.open_date, amount=abs(payload.principal), currency=destination.currency,
            account_id=destination.id, location_id=transfer_in_loc.id, note=note,
        )
        db.add(out_tx)
        db.add(in_tx)
        db.flush()
        booked = [out_tx.id, in_tx.id]

        # The arrangement fee is a charge in its own right, never part of the
        # transfer: the drawdown moves the capital, the fee is what it cost to
        # get it. Where it is charged is what tells the two treatments apart —
        # added to the debt, or taken out of the money received.
        fee = round(payload.opening_fee or 0.0, 2)
        if fee > 0:
            capitalised = payload.fee_treatment == "capitalised"
            fee_account = account if capitalised else destination
            fee_category = db.query(Category).filter(Category.name == "Loan fees").first()
            if not fee_category:
                fee_category = Category(name="Loan fees", type="expense")
                db.add(fee_category)
                db.flush()
            fee_tx = Transaction(
                date=payload.open_date, amount=-fee, currency=fee_account.currency,
                account_id=fee_account.id, category_id=fee_category.id, payee_id=lender_id,
                note=f"Arrangement fee — {name}",
            )
            db.add(fee_tx)
            db.flush()
            booked.append(fee_tx.id)

        recalculate_balances_from_transaction(
            db, min(booked), [account.id, destination.id]
        )

    db.commit()
    db.refresh(loan)

    return {
        "loan": loan_engine.as_dict(loan),
        "schedule": loan_engine.summary(loan),
        "account_id": account.id,
        "account_created": created_account,
    }


@app.put("/loans/{loan_id}")
def update_loan(loan_id: int, payload: schemas.LoanUpdate, db: Session = Depends(get_db)):
    """
    Correct the terms of a loan.

    Only the terms change. The account and the movements booked when the loan was
    opened stay as they are — see ``schemas.LoanUpdate`` for why. The schedule is
    recomputed from the new terms on the spot.
    """
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    if payload.principal <= 0:
        raise HTTPException(status_code=400, detail="The amount borrowed must be greater than zero")
    if not (payload.name or "").strip():
        raise HTTPException(status_code=400, detail="The loan needs a name")

    _apply_terms(loan, payload)
    loan.lender_payee_id = _resolve_lender(db, payload)
    db.commit()
    db.refresh(loan)

    return {"loan": loan_engine.as_dict(loan), "schedule": loan_engine.summary(loan)}


@app.delete("/loans/{loan_id}")
def delete_loan(loan_id: int, db: Session = Depends(get_db)):
    """
    Forget the terms of a loan.

    The account and every transaction on it survive: what goes is the contract,
    not the debt. The loan reverts to being estimated from its movements, which is
    how it was tracked before any terms were entered.
    """
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    name = loan.name
    db.delete(loan)
    db.commit()
    return {"message": f"Terms for '{name}' removed. The account and its transactions are untouched."}


@app.get("/loans/{loan_id}/schedule")
def get_loan_schedule(loan_id: int, db: Session = Depends(get_db)):
    """The full amortisation table the loan's terms imply."""
    loan = db.query(Loan).filter(Loan.id == loan_id).first()
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    return {
        "loan": loan_engine.as_dict(loan),
        "summary": loan_engine.summary(loan),
        "rows": loan_engine.schedule(loan),
    }

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
        
        # PHASE 2: Calculate total_balance_after using HISTORICAL exchange rates
        print("--- CALCULATING TOTAL BALANCE AFTER (historical rates) ---")

        # The same column is written by the incremental paths in helpers.py and by
        # recalculate_balances_for_accounts, both of which use the display currency.
        # Hardcoding GBP here made a full recalculation silently rewrite every
        # figure in a different currency from the one that produced it.
        BASE_CURRENCY = get_base_currency(db)

        # Get ALL transactions ordered globally by date and ID
        all_transactions = db.query(models.Transaction).order_by(
            models.Transaction.date.asc(),
            models.Transaction.id.asc()
        ).all()

        if all_transactions:
            # Get all currencies used by accounts
            all_currencies = list(set(acc.currency for acc in accounts if acc.currency and acc.currency != BASE_CURRENCY))

            # Load historical rates for the full date range
            min_date = _to_date(all_transactions[0].date)
            max_date = _to_date(all_transactions[-1].date)
            historical_rates = get_rates_bulk(db, all_currencies, min_date, max_date) if all_currencies else {}

            # Track converted balances per account (same logic as networth endpoint)
            account_converted_balances = {}

            # Seed the accounts that never appear in a transaction: they hold their
            # opening balance throughout, so leaving them out made this running
            # total disagree with the dashboard, which counts them.
            touched_ids = {t.account_id for t in all_transactions}
            opening_rates = historical_rates.get(min_date, {}) or {}
            opening_base_rate = opening_rates.get(BASE_CURRENCY, 1.0)
            for acc in accounts:
                if acc.id in touched_ids or not acc.initial_balance:
                    continue
                acc_rate = opening_rates.get(acc.currency or BASE_CURRENCY, 1.0)
                account_converted_balances[acc.id] = (
                    float(acc.initial_balance) * (opening_base_rate / acc_rate)
                )

            # Initialise with initial_balance converted at first transaction's rate
            account_initial_added = set()

            # Process transactions in global order
            for t in all_transactions:
                if t is None:
                    continue

                trans_date = _to_date(t.date)
                rates_for_day = historical_rates.get(trans_date, {BASE_CURRENCY: 1.0})
                base_rate = rates_for_day.get(BASE_CURRENCY, 1.0)

                # On first appearance of account, add initial_balance converted at this date's rate
                if t.account_id not in account_converted_balances:
                    acc = accounts_map.get(t.account_id)
                    init_bal = 0.0
                    if acc and acc.initial_balance:
                        acc_rate = rates_for_day.get(acc.currency or BASE_CURRENCY, 1.0)
                        init_bal = float(acc.initial_balance) * (base_rate / acc_rate)
                    account_converted_balances[t.account_id] = init_bal

                # Convert this transaction's amount with historical rate
                amount = float(t.amount) if t.amount is not None else 0.0
                acc = accounts_map.get(t.account_id)
                currency = acc.currency if acc else BASE_CURRENCY
                trans_rate = rates_for_day.get(currency, 1.0)
                converted_amount = amount * (base_rate / trans_rate)

                account_converted_balances[t.account_id] += converted_amount
                t.total_balance_after = round(sum(account_converted_balances.values()), 2)
        
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
    """Send visitors straight to the app (so http://host:8422 just works)."""
    return RedirectResponse(url="/app/index.html")


@app.get("/api")
def api_info():
    """API information (the human UI lives at /app/index.html)."""
    return {
        "message": "Welcome to Delfin API",
        "docs": "/docs",
        "app": "/app/index.html",
        "version": "1.0.0"
    }


@app.get("/api/currencies")
def list_currencies():
    """Canonical list of currencies the app can price (code + name)."""
    from backend.currencies import currency_options
    return {"currencies": currency_options()}


@app.get("/settings/maintenance")
def get_maintenance_settings():
    """Current app settings (schedule time, backup retention, display currency)."""
    from backend import settings_store
    s = settings_store.get_settings()
    return {
        "maintenance_time": s["maintenance_time"],
        "backup_retention": s["backup_retention"],
        "display_currency": s["display_currency"],
        "retention_options": list(settings_store.RETENTION_DAYS.keys()),
    }


@app.put("/settings/maintenance")
def update_maintenance_settings(payload: schemas.MaintenanceSettingsUpdate,
                                db: Session = Depends(get_db)):
    """Update the maintenance schedule, backup retention and/or display currency."""
    from backend import settings_store
    try:
        settings = settings_store.update_settings(
            maintenance_time=payload.maintenance_time,
            backup_retention=payload.backup_retention,
            display_currency=payload.display_currency,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # If the chosen display currency has no rates yet, backfill now so dashboard
    # totals convert correctly without waiting for the nightly maintenance run.
    dc = settings.get("display_currency")
    if dc and dc != "auto":
        from backend.update_exchange_rates import get_currencies_with_rates, update_exchange_rates
        if dc not in get_currencies_with_rates(db):
            try:
                update_exchange_rates()
            except Exception as e:
                print(f"Exchange-rate backfill after display-currency change failed: {e}")

    return settings


@app.get("/maintenance/status")
def maintenance_status():
    """Backup + schedule status for the Tools → Maintenance panel."""
    from backend import backup as db_backup, settings_store
    s = settings_store.get_settings()
    st = db_backup.status()
    st.update({
        "maintenance_time": s["maintenance_time"],
        "backup_retention": s["backup_retention"],
        "last_maintenance": maintenance.last_run_date(),
        "running": maintenance.is_running(),
    })
    return st


@app.post("/maintenance/enable-backups")
def enable_backups():
    """Switch backups on (creates the sentinel in the backup folder)."""
    from backend import backup as db_backup
    try:
        db_backup.enable()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    return db_backup.status()


@app.post("/maintenance/disable-backups")
def disable_backups():
    """Switch backups off (removes the sentinel)."""
    from backend import backup as db_backup
    db_backup.disable()
    return db_backup.status()


@app.post("/maintenance/run")
def run_maintenance_now():
    """Run the full maintenance job now (synchronously) and return what it did."""
    return maintenance.run_maintenance(trigger="manual")


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

        # Consistent, WAL-safe snapshot (a raw copy could miss uncheckpointed data)
        db_backup.make_snapshot(backup_path)

        # Return the file as a download
        return FileResponse(
            path=backup_path,
            filename=backup_filename,
            media_type='application/octet-stream',
            background=None  # Keep file after sending
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to create backup: {str(e)}")


@app.post("/tools/restore-database")
async def restore_database(file: UploadFile = File(...)):
    """Restore the database from a Delfin .db backup (replaces ALL current data).
    The uploaded file is validated first, and a safety backup of the current
    database is taken before the swap."""
    import sqlcipher3.dbapi2 as sqlcipher
    data_dir = "./data"
    live = database.DB_PATH
    tmp = os.path.join(data_dir, ".restore_upload.tmp")
    enc = tmp + ".enc"
    dek = database.get_dek_hex()

    def _cleanup():
        for p in (tmp, enc):
            try: os.remove(p)
            except OSError: pass

    # 1. Stream the upload to a temp file (next to the live DB, same filesystem)
    try:
        with open(tmp, "wb") as out:
            shutil.copyfileobj(file.file, out)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Could not read the uploaded file: {e}")

    # 2. Validate. A backup from THIS app is SQLCipher-encrypted (no plaintext
    #    header); a pre-encryption backup is plaintext. Accept both.
    try:
        is_plaintext = open(tmp, "rb").read(16) == b"SQLite format 3\x00"
        con = sqlcipher.connect(tmp)
        if not is_plaintext and dek:
            con.execute(f"PRAGMA key = \"x'{dek}'\"")
        try:
            if con.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError("Could not read the backup — it's corrupt, or it was "
                                 "encrypted on a different installation (different key).")
            tables = {r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = {"accounts", "transactions"} - tables
            if missing:
                raise ValueError(
                    f"This doesn't look like a Delfin backup — missing tables: "
                    f"{', '.join(sorted(missing))}.")
            tx_count = con.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
            acc_count = con.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
        finally:
            con.close()
    except ValueError as e:
        _cleanup()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        _cleanup()
        raise HTTPException(status_code=400, detail=f"Could not open the backup file: {e}")

    # 3. Safety backup of the current database (encrypted)
    safety = None
    if os.path.exists(live):
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        safety = f"finance_pre_restore_{ts}.db"
        db_backup.make_snapshot(os.path.join(data_dir, safety))

    # 4. If the upload is plaintext, encrypt it with the current key before installing.
    to_install = tmp
    if is_plaintext and dek:
        try:
            if os.path.exists(enc):
                os.remove(enc)
            c = sqlcipher.connect(tmp)
            c.execute(f"ATTACH DATABASE '{enc}' AS e KEY \"x'{dek}'\"")
            c.execute("SELECT sqlcipher_export('e')")
            c.execute("DETACH DATABASE e")
            c.close()
            to_install = enc
        except Exception as e:
            _cleanup()
            raise HTTPException(status_code=500, detail=f"Could not encrypt the restored data: {e}")

    # 5. Swap. Close pooled connections, atomically replace the file, drop stale WAL/SHM.
    try:
        eng = database.get_engine()
        if eng is not None:
            eng.dispose()
        os.replace(to_install, live)
        for suffix in ("-wal", "-shm"):
            p = live + suffix
            if os.path.exists(p):
                try: os.remove(p)
                except OSError: pass
        if eng is not None:
            models.Base.metadata.create_all(bind=eng)  # add any tables a newer build expects
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Restore failed during swap: {e}")
    finally:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass

    return {
        "message": "Database restored.",
        "transactions": tx_count,
        "accounts": acc_count,
        "safety_backup": safety,
    }


# ============================================
# FINANCISTO IMPORT / EXPORT (integrated tools)
# ============================================

def _create_safety_backup() -> Optional[str]:
    """Copy the live database to a timestamped file before a destructive op."""
    source_db = "./data/finance.db"
    if not os.path.exists(source_db):
        return None
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"finance_pre_import_{timestamp}.db"
    db_backup.make_snapshot(f"./data/{backup_filename}")
    return backup_filename


@app.post("/tools/financisto/import")
async def financisto_import(
    file: UploadFile = File(...),
    mode: str = Form("analyze"),
    db: Session = Depends(get_db),
):
    """
    Import a Financisto database into Delfin.

    Formats: native ``.backup`` (gzipped) or Financisto CSV export — auto-detected.

    Modes:
        * ``analyze`` — dry run. Parses and maps the file WITHOUT writing
          anything, returning a preview summary and a compatibility report so
          the user can review data-loss before committing.
        * ``merge``   — add the imported data to the existing database
          (duplicates skipped).
        * ``replace`` — wipe the importable tables and restore from the file.

    For ``merge``/``replace`` a safety backup of the current database is taken
    first and its filename returned.
    """
    from backend.integrations import financisto

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    filename = file.filename or ""

    if mode == "analyze":
        try:
            return financisto.analyze_import(raw, filename)
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Could not parse file: {str(e)}")

    if mode not in ("merge", "replace"):
        raise HTTPException(status_code=400, detail="mode must be analyze, merge or replace")

    safety_backup = _create_safety_backup()
    try:
        result = financisto.run_import(db, raw, filename, mode)
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Import failed: {str(e)}. Your data is unchanged"
                   + (f" (safety backup: {safety_backup})" if safety_backup else "") + ".",
        )

    result["safety_backup"] = safety_backup
    return result


@app.get("/tools/financisto/export")
def financisto_export(
    format: str = Query("backup", pattern="^(backup|csv)$"),
    db: Session = Depends(get_db),
):
    """
    Export the whole Delfin database in Financisto format.

    ``format=backup`` produces a native gzipped ``.backup`` that Financisto can
    restore directly; ``format=csv`` produces the Financisto CSV layout.
    """
    from backend.integrations import financisto

    try:
        data, filename, media_type = financisto.export_database(db, format)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Export failed: {str(e)}")

    return Response(
        content=data,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/tools/financisto/export/notes")
def financisto_export_notes():
    """List what a Financisto export cannot carry (for UI transparency)."""
    from backend.integrations import financisto
    return {"notes": financisto.export_notes()}


# ============================================
# CSV IMPORT PROFILES (per-bank column mappings)
# ============================================
@app.get("/tools/import-profiles")
def list_import_profiles():
    """Return all saved bank CSV import profiles."""
    from backend import profiles_store
    return {"profiles": profiles_store.list_profiles()}


@app.post("/tools/import-profiles")
def save_import_profile(profile: dict):
    """Create or update (by name) a bank CSV import profile."""
    from backend import profiles_store
    try:
        profiles = profiles_store.save_profile(profile)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"profiles": profiles}


@app.delete("/tools/import-profiles/{name}")
def delete_import_profile(name: str):
    """Delete a bank CSV import profile by name."""
    from backend import profiles_store
    return {"profiles": profiles_store.delete_profile(name)}


# ============================================
# LEARNED IMPORT RULES (normalised description -> payee)
# ============================================
@app.get("/tools/import-rules")
def list_import_rules():
    """Return all learned description->payee import rules."""
    from backend import rules_store
    return {"rules": rules_store.get_rules()}


@app.post("/tools/import-rules")
def merge_import_rules(payload: dict):
    """Upsert learned description->payee rules. Body: {"rules": {desc: payee, ...}}."""
    from backend import rules_store
    try:
        rules = rules_store.merge_rules(payload.get("rules", {}))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"rules": rules}


# ============================================
# CATEGORY DEDUPLICATION (integrated maintenance tool)
# ============================================

def _find_duplicate_categories(db: Session):
    """
    Group categories by (name, parent) and return those with more than one row.
    Each group: {"name", "parent", "ids" (sorted), "keep_id" (lowest), "count"}.
    """
    rows = db.query(
        models.Category.name,
        models.Category.parent,
        sql_func.group_concat(models.Category.id).label("ids"),
    ).group_by(
        models.Category.name, models.Category.parent
    ).having(sql_func.count(models.Category.id) > 1).all()

    groups = []
    for name, parent, ids_str in rows:
        ids = sorted(int(x) for x in ids_str.split(","))
        groups.append({
            "name": name,
            "parent": parent,
            "ids": ids,
            "keep_id": ids[0],
            "count": len(ids),
        })
    return groups


@app.get("/tools/categories/duplicates")
def get_duplicate_categories(db: Session = Depends(get_db)):
    """Detect categories that share the same name and parent."""
    groups = _find_duplicate_categories(db)
    return {
        "groups": groups,
        "total_groups": len(groups),
        "total_duplicates": sum(g["count"] - 1 for g in groups),
    }


@app.post("/tools/categories/merge-duplicates")
def merge_duplicate_categories(db: Session = Depends(get_db)):
    """
    Merge every set of duplicate categories (same name + parent): keep the
    lowest id, repoint all references to it, then delete the extras.

    References updated: transactions, recurring expenses, planned expenses and
    cached payee statistics — so no dangling category_id is left behind.
    """
    groups = _find_duplicate_categories(db)
    if not groups:
        return {"groups_merged": 0, "categories_deleted": 0, "transactions_reassigned": 0}

    try:
        categories_deleted = 0
        transactions_reassigned = 0

        for g in groups:
            keep_id = g["keep_id"]
            dup_ids = [i for i in g["ids"] if i != keep_id]
            if not dup_ids:
                continue

            transactions_reassigned += db.query(models.Transaction).filter(
                models.Transaction.category_id.in_(dup_ids)
            ).update({models.Transaction.category_id: keep_id}, synchronize_session=False)

            db.query(models.RecurringExpense).filter(
                models.RecurringExpense.category_id.in_(dup_ids)
            ).update({models.RecurringExpense.category_id: keep_id}, synchronize_session=False)

            db.query(models.PlannedExpense).filter(
                models.PlannedExpense.category_id.in_(dup_ids)
            ).update({models.PlannedExpense.category_id: keep_id}, synchronize_session=False)

            db.query(models.Payee).filter(
                models.Payee.most_common_category_id.in_(dup_ids)
            ).update({models.Payee.most_common_category_id: keep_id}, synchronize_session=False)

            categories_deleted += db.query(models.Category).filter(
                models.Category.id.in_(dup_ids)
            ).delete(synchronize_session=False)

        db.commit()
        return {
            "groups_merged": len(groups),
            "categories_deleted": categories_deleted,
            "transactions_reassigned": transactions_reassigned,
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Deduplication failed: {str(e)}")


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


# ============================================
# BUDGET ENDPOINTS
# ============================================

@app.get("/budgets")
def get_budgets(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db)
):
    """Get all budgets, ordered by year_month descending."""
    budgets = db.query(Budget).order_by(Budget.year_month.desc()).offset(skip).limit(limit).all()
    return budgets


@app.get("/budgets/current")
def get_current_budget(db: Session = Depends(get_db)):
    """Get the budget for the current month."""
    current_year_month = datetime.now().strftime("%Y-%m")
    budget = db.query(Budget).filter(Budget.year_month == current_year_month).first()
    if not budget:
        return None
    return budget


@app.get("/budgets/{year_month}")
def get_budget(year_month: str, db: Session = Depends(get_db)):
    """Get budget for a specific month."""
    budget = db.query(Budget).filter(Budget.year_month == year_month).first()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    return budget


@app.post("/budgets")
def create_or_update_budget(
    budget_data: schemas.BudgetCreate,
    db: Session = Depends(get_db)
):
    """Create or update a budget for a specific month."""
    existing = db.query(Budget).filter(Budget.year_month == budget_data.year_month).first()

    if existing:
        existing.amount = budget_data.amount
        existing.currency = budget_data.currency
        db.commit()
        db.refresh(existing)
        return existing
    else:
        new_budget = Budget(
            year_month=budget_data.year_month,
            amount=budget_data.amount,
            currency=budget_data.currency
        )
        db.add(new_budget)
        db.commit()
        db.refresh(new_budget)
        return new_budget


@app.delete("/budgets/{year_month}")
def delete_budget(year_month: str, db: Session = Depends(get_db)):
    """Delete a budget for a specific month."""
    budget = db.query(Budget).filter(Budget.year_month == year_month).first()
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")

    db.delete(budget)
    db.commit()
    return {"message": "Budget deleted successfully"}


@app.get("/budgets/{year_month}/progress")
def get_budget_progress(year_month: str, db: Session = Depends(get_db)):
    """
    The whole budget month: headline figures, the three card lists, the sinking
    funds and the day-by-day calendar.

    The monthly target is no longer a number the user types — it is the sum of
    the fixed and planned lines materialised for that month.
    """
    try:
        return budget_engine.month_snapshot(db, year_month)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid year_month format. Use YYYY-MM")


# ============================================
# BUDGET ITEMS (definitions behind each month)
# ============================================

def _item_payload(item: models.BudgetItem) -> dict:
    return {
        "id": item.id,
        "kind": item.kind,
        "name": item.name,
        "amount": item.amount,
        "currency": item.currency,
        "is_estimated": bool(item.is_estimated),
        "first_date": item.first_date.date().isoformat() if item.first_date else None,
        "interval_count": item.interval_count,
        "interval_unit": item.interval_unit,
        "day_rule": item.day_rule or "exact",
        "day_ordinal": item.day_ordinal,
        "payee_id": item.payee_id,
        "payee_name": item.payee.name if item.payee else None,
        "set_aside_account_id": item.set_aside_account_id,
        "set_aside_account_name": item.set_aside_account.name if item.set_aside_account else None,
        "account_ids": [a.account_id for a in item.accounts],
        "category_ids": [c.category_id for c in item.categories],
        "starts_ym": item.starts_ym,
        "ends_ym": item.ends_ym,
        "is_active": item.is_active,
        "period_months": budget_engine.period_months(item.interval_count, item.interval_unit),
    }


def _apply_item_payload(db: Session, item: models.BudgetItem, data: schemas.BudgetItemCreate) -> None:
    """Write a create/update payload onto an item, including its links."""
    item.kind = data.kind
    item.name = data.name.strip()
    item.amount = data.amount
    item.currency = data.currency or get_base_currency(db)
    item.is_estimated = 1 if data.is_estimated else 0
    item.interval_count = data.interval_count
    item.interval_unit = data.interval_unit
    item.day_rule = data.day_rule
    item.day_ordinal = data.day_ordinal
    item.payee_id = data.payee_id
    item.set_aside_account_id = data.set_aside_account_id

    # Editing a definition never moves the month it started in. A new one may
    # start in a closed month — changes are effective-dated from the month the
    # user is looking at, and that month is allowed to be in the past.
    if item.starts_ym is None:
        item.starts_ym = data.starts_ym or budget_engine.current_ym()

    # Without a first date, the item starts on the first day of its first month.
    # A one-off gets pinned there too when it has no date of its own: a planned
    # expense never asks for one, and a one-off has to land in some month — its
    # first month is the one the user was looking at when they made the change.
    if data.first_date:
        item.first_date = data.first_date
    elif item.first_date is None or data.interval_unit == "once":
        year, month = budget_engine.parse_ym(item.starts_ym)
        item.first_date = datetime(year, month, 1)

    if item.id is not None:
        # Rebuild the links, flushing the removals so the unique indexes free up.
        item.accounts.clear()
        item.categories.clear()
        db.flush()
    for account_id in dict.fromkeys(data.account_ids or []):
        item.accounts.append(models.BudgetItemAccount(account_id=account_id))
    for category_id in dict.fromkeys(data.category_ids or []):
        item.categories.append(models.BudgetItemCategory(category_id=category_id))


def _edit_target_month(effective_ym: Optional[str], scope: str) -> tuple[str, str]:
    """Validate the month an edit is made from and the reach it should have."""
    if scope not in ("month", "forward"):
        raise HTTPException(status_code=400, detail="scope must be month or forward")
    ym = effective_ym or budget_engine.current_ym()
    try:
        budget_engine.parse_ym(ym)
    except ValueError:
        raise HTTPException(status_code=400, detail="effective_ym must look like 2026-07")
    return ym, scope


@app.get("/budget/items")
def list_budget_items(
    kind: Optional[str] = Query(None, description="fixed | income | planned"),
    include_inactive: bool = False,
    db: Session = Depends(get_db)
):
    """The budget definitions themselves, for editing."""
    query = db.query(models.BudgetItem)
    if kind:
        query = query.filter(models.BudgetItem.kind == kind)
    if not include_inactive:
        query = query.filter(models.BudgetItem.is_active == 1)
    items = query.order_by(models.BudgetItem.amount.desc()).all()
    return [_item_payload(i) for i in items]


@app.post("/budget/items")
def create_budget_item(data: schemas.BudgetItemCreate, db: Session = Depends(get_db)):
    """Create a definition and materialise it from the month it starts in."""
    item = models.BudgetItem()
    _apply_item_payload(db, item, data)
    db.add(item)
    db.commit()
    db.refresh(item)
    item.series_id = item.id  # a new definition is the first of its series
    db.commit()
    budget_engine.apply_item_change(db, item, item.starts_ym)
    return _item_payload(item)


@app.put("/budget/items/{item_id}")
def update_budget_item(
    item_id: int,
    data: schemas.BudgetItemCreate,
    effective_ym: Optional[str] = Query(None, description="Month the edit is made from, e.g. 2026-10"),
    scope: str = Query("forward", description="month = that month only | forward = that month on"),
    db: Session = Depends(get_db),
):
    """
    Edit a definition from a given month on. The months before it keep the
    values they were budgeted with: rather than rewriting the definition, it
    is split in two at that month, so the old and new figures each own their
    own stretch of history.
    """
    item = db.query(models.BudgetItem).filter(models.BudgetItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Budget item not found")
    ym, scope = _edit_target_month(effective_ym, scope)

    target = budget_engine.prepare_item_edit(db, item, ym, scope)
    _apply_item_payload(db, target, data)
    db.commit()
    db.refresh(target)
    budget_engine.apply_item_change(db, target, ym)
    return _item_payload(target)


@app.delete("/budget/items/{item_id}")
def delete_budget_item(
    item_id: int,
    effective_ym: Optional[str] = Query(None, description="Month the removal starts from, e.g. 2026-10"),
    scope: str = Query("forward", description="month = that month only | forward = that month on"),
    db: Session = Depends(get_db),
):
    """
    Stop a definition from a given month, either for that month alone or from
    there on. Earlier months keep their lines, so the history of what was
    budgeted stays intact.
    """
    item = db.query(models.BudgetItem).filter(models.BudgetItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Budget item not found")
    ym, scope = _edit_target_month(effective_ym, scope)

    if scope == "month":
        budget_engine.retire_item_for_month(db, item, ym)
        return {"message": f"Budget item removed from {ym}"}
    budget_engine.retire_item(db, item, ym)
    return {"message": f"Budget item retired from {ym} on"}


@app.patch("/budget/lines/{line_id}")
def update_budget_line(line_id: int, data: schemas.BudgetLineUpdate, db: Session = Depends(get_db)):
    """
    Correct one month's line without touching its definition — used to override
    payment detection, and to fix a figure in a month that is already closed.
    """
    line = db.query(models.BudgetMonthLine).filter(models.BudgetMonthLine.id == line_id).first()
    if not line:
        raise HTTPException(status_code=404, detail="Budget line not found")

    if data.name is not None:
        line.name = data.name.strip()
    if data.amount is not None:
        line.amount = data.amount
    if data.clear_paid_override:
        line.paid_override = None
    elif data.paid is not None:
        line.paid_override = 1 if data.paid else 0
    line.source = "manual"
    db.commit()
    return {"message": "Budget line updated"}


@app.get("/budget/history")
def get_budget_history(months: int = Query(12, ge=1, le=36), db: Session = Depends(get_db)):
    """Budgeted vs actual for recent months, most recent first."""
    return {"months": budget_engine.month_history(db, months)}


@app.get("/budget/suggestions")
def suggest_budget_items(
    kind: str = Query("fixed", description="fixed | income"),
    min_occurrences: int = 3,
    max_variance: float = 0.3,
    months_to_look_back: int = 12,
    db: Session = Depends(get_db)
):
    """
    Suggest budget items from the transaction history: for fixed expenses, repeat
    charges to the same payee and repeat transfers into the same account (loans,
    savings); for income, money arriving regularly from the same payer.
    """
    if kind not in ("fixed", "income"):
        raise HTTPException(status_code=400, detail="kind must be fixed or income")
    return budget_engine.suggest_candidates(
        db, kind, min_occurrences, max_variance, months_to_look_back)


# ============================================
# KAKEIBO BUCKETS
# ============================================

@app.get("/budget/buckets")
def get_category_buckets(db: Session = Depends(get_db)):
    """
    The category tree with its kakeibo mapping, grouped so that classifying a
    parent covers its subcategories. Each entry carries the bucket set on it
    (``bucket``, null when it just inherits) and the one that actually applies
    (``effective``).
    """
    own = budget_engine.explicit_buckets(db)
    effective = budget_engine.bucket_map(db)
    categories = db.query(Category).order_by(Category.name).all()

    # Only spending categories are worth classifying.
    spending = [c for c in categories
                if not (c.type and c.type.lower() in ("income", "ingreso"))]

    def entry(category):
        return {
            "category_id": category.id,
            "name": category.name,
            "bucket": own.get(category.id),
            "effective": effective.get(category.id),
        }

    # Group by parent name. A CSV import records the parent as a plain string
    # without ever creating a row for it, so the group head may not exist as a
    # category — it still gets a group, just one that cannot own a bucket and so
    # sets its children's instead.
    norm = budget_engine.normalise_name
    rows_by_key = {norm(c.name): c for c in categories}
    children_of = {}
    for category in spending:
        if category.parent:
            children_of.setdefault(norm(category.parent), []).append(category)

    head_keys = set(children_of)
    groups = []

    for key, children in children_of.items():
        row = rows_by_key.get(key)
        head = entry(row) if row is not None else {
            "category_id": None, "name": children[0].parent, "bucket": None, "effective": None,
        }
        head["inherits"] = row is not None
        head["children"] = [entry(c) for c in
                            sorted(children, key=lambda c: c.name)
                            if norm(c.name) not in head_keys]
        groups.append(head)

    # Categories that are neither a child nor already a head stand on their own.
    for category in spending:
        if category.parent or norm(category.name) in head_keys:
            continue
        groups.append({**entry(category), "inherits": True, "children": []})

    groups.sort(key=lambda g: g["name"].lower())

    return {
        "buckets": list(budget_engine.BUCKETS),
        "groups": groups,
        "unmapped_count": sum(1 for c in spending if c.id not in effective),
    }


@app.put("/budget/buckets")
def update_category_buckets(data: schemas.CategoryBucketUpdate, db: Session = Depends(get_db)):
    """Map categories to kakeibo buckets. A null bucket clears the mapping."""
    existing = {row.category_id: row for row in db.query(models.CategoryBucket).all()}
    for entry in data.mappings:
        if entry.bucket is None:
            row = existing.get(entry.category_id)
            if row:
                db.delete(row)
            continue
        if entry.bucket not in budget_engine.BUCKETS:
            raise HTTPException(
                status_code=400,
                detail=f"bucket must be one of {list(budget_engine.BUCKETS)}"
            )
        row = existing.get(entry.category_id)
        if row:
            row.bucket = entry.bucket
        else:
            db.add(models.CategoryBucket(category_id=entry.category_id, bucket=entry.bucket))
    db.commit()
    return {"message": "Buckets updated"}


# ============================================
# RECURRING EXPENSES ENDPOINTS
# ============================================

@app.get("/recurring")
def get_recurring_expenses(
    include_inactive: bool = False,
    db: Session = Depends(get_db)
):
    """Get all recurring expenses."""
    query = db.query(RecurringExpense)
    if not include_inactive:
        query = query.filter(RecurringExpense.is_active == 1)

    recurring = query.order_by(RecurringExpense.amount.desc()).all()

    result = []
    for rec in recurring:
        result.append({
            "id": rec.id,
            "name": rec.name,
            "payee_id": rec.payee_id,
            "payee_name": rec.payee.name if rec.payee else None,
            "category_id": rec.category_id,
            "category_name": rec.category.name if rec.category else None,
            "amount": rec.amount,
            "currency": rec.currency,
            "day_of_month": rec.day_of_month,
            "frequency": rec.frequency or "monthly",
            "start_month": rec.start_month,
            "is_active": rec.is_active,
            "created_at": rec.created_at,
            "updated_at": rec.updated_at
        })

    return result


@app.get("/recurring/detect")
def detect_recurring_expenses(
    min_occurrences: int = 3,
    max_variance: float = 0.3,
    months_to_look_back: int = 6,
    db: Session = Depends(get_db)
):
    """
    Detect potential recurring expenses from transaction history.
    Looks for payees that appear in multiple recent months with consistent amounts.
    Only considers transactions from the last N months and requires recent activity.
    """
    # Calculate date range - only look at recent transactions
    today = date.today()
    cutoff_date = today - timedelta(days=months_to_look_back * 31)

    # Recent months for checking if expense is still active (last 2 months)
    current_month = (today.year, today.month)
    prev_month = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    recent_months = {current_month, prev_month}

    # Get all existing recurring expense payee IDs to exclude
    existing_payee_ids = set(
        r.payee_id for r in db.query(RecurringExpense.payee_id)
        .filter(RecurringExpense.payee_id != None)
        .all()
    )

    # Get transfer location IDs to exclude
    transfer_ids = [
        r.id for r in db.query(Location.id)
        .filter(Location.name.in_(["Transfer In", "Transfer Out"]))
        .all()
    ]

    # Get transactions from recent months only
    filters = [
        Transaction.payee_id != None,
        Transaction.amount < 0,  # Only expenses
        Transaction.date >= datetime.combine(cutoff_date, time.min)
    ]
    if transfer_ids:
        # A transaction with no location must still count: SQL evaluates
        # "NOT IN" as NULL, not true, when the column itself is NULL.
        filters.append(or_(Transaction.location_id.is_(None),
                           ~Transaction.location_id.in_(transfer_ids)))

    transactions = db.query(Transaction).filter(and_(*filters)).all()

    # Group by payee
    payee_transactions = {}
    for tx in transactions:
        if tx.payee_id not in payee_transactions:
            payee_transactions[tx.payee_id] = []
        payee_transactions[tx.payee_id].append(tx)

    candidates = []

    for payee_id, txs in payee_transactions.items():
        # Skip if already in recurring expenses
        if payee_id in existing_payee_ids:
            continue

        # Get unique months and track data
        months = set()
        amounts = []
        days = []
        categories = {}

        for tx in txs:
            tx_date = tx.date.date() if isinstance(tx.date, datetime) else tx.date
            month_tuple = (tx_date.year, tx_date.month)
            months.add(month_tuple)
            amounts.append(abs(tx.amount))
            days.append(tx_date.day)

            cat_id = tx.category_id
            if cat_id:
                categories[cat_id] = categories.get(cat_id, 0) + 1

        # Check if appears in enough months
        if len(months) < min_occurrences:
            continue

        # IMPORTANT: Check if there's at least one transaction in recent months
        # This ensures we don't detect old recurring expenses that stopped
        has_recent_activity = bool(months & recent_months)
        if not has_recent_activity:
            continue

        # Check amount consistency (variance)
        avg_amount = sum(amounts) / len(amounts)
        max_diff = max(abs(a - avg_amount) for a in amounts)
        variance = max_diff / avg_amount if avg_amount > 0 else 1

        if variance > max_variance:
            continue

        # Get most common category
        most_common_cat_id = max(categories.keys(), key=lambda k: categories[k]) if categories else None
        most_common_cat = db.query(Category).filter(Category.id == most_common_cat_id).first() if most_common_cat_id else None

        # Get payee info
        payee = db.query(Payee).filter(Payee.id == payee_id).first()

        # Calculate average day of month
        avg_day = round(sum(days) / len(days))

        candidates.append({
            "payee_id": payee_id,
            "payee_name": payee.name if payee else "Unknown",
            "suggested_name": payee.name if payee else "Unknown",
            "average_amount": round(avg_amount, 2),
            "currency": txs[0].currency if txs else "GBP",
            "occurrences": len(months),
            "average_day": avg_day,
            "category_id": most_common_cat_id,
            "category_name": most_common_cat.name if most_common_cat else None,
            "variance_percent": round(variance * 100, 1)
        })

    # ============================================
    # PART 2: Detect recurring TRANSFERS (debt payments)
    # ============================================

    # Get Transfer Out location ID
    transfer_out_loc = db.query(Location).filter(Location.name == "Transfer Out").first()
    transfer_in_loc = db.query(Location).filter(Location.name == "Transfer In").first()

    if transfer_out_loc and transfer_in_loc:
        # Get all Transfer Out transactions from recent months
        transfer_filters = [
            Transaction.location_id == transfer_out_loc.id,
            Transaction.amount < 0,
            Transaction.date >= datetime.combine(cutoff_date, time.min)
        ]
        transfer_outs = db.query(Transaction).filter(and_(*transfer_filters)).all()

        # For each transfer out, find the matching transfer in to get destination account
        transfers_by_dest = {}  # destination_account_id -> list of (amount, date, from_account_id)

        for tx_out in transfer_outs:
            tx_date = tx_out.date.date() if isinstance(tx_out.date, datetime) else tx_out.date

            # Find matching Transfer In on the same day with similar amount
            matching_in = db.query(Transaction).filter(
                Transaction.location_id == transfer_in_loc.id,
                Transaction.amount > 0,
                func.date(Transaction.date) == tx_date,
                Transaction.amount >= abs(tx_out.amount) * 0.99,
                Transaction.amount <= abs(tx_out.amount) * 1.01
            ).first()

            if matching_in:
                dest_account_id = matching_in.account_id
                if dest_account_id not in transfers_by_dest:
                    transfers_by_dest[dest_account_id] = []
                transfers_by_dest[dest_account_id].append({
                    "amount": abs(tx_out.amount),
                    "date": tx_date,
                    "from_account_id": tx_out.account_id,
                    "currency": tx_out.currency
                })

        # Analyze each destination account for recurring patterns
        for dest_account_id, transfers in transfers_by_dest.items():
            dest_account = db.query(Account).filter(Account.id == dest_account_id).first()
            if not dest_account:
                continue

            # Get unique months
            months = set()
            amounts = []
            days = []

            for t in transfers:
                month_tuple = (t["date"].year, t["date"].month)
                months.add(month_tuple)
                amounts.append(t["amount"])
                days.append(t["date"].day)

            # Check minimum occurrences
            if len(months) < min_occurrences:
                continue

            # Check recent activity
            has_recent_activity = bool(months & recent_months)
            if not has_recent_activity:
                continue

            # Check amount consistency
            avg_amount = sum(amounts) / len(amounts)
            max_diff = max(abs(a - avg_amount) for a in amounts)
            variance = max_diff / avg_amount if avg_amount > 0 else 1

            if variance > max_variance:
                continue

            # Calculate average day
            avg_day = round(sum(days) / len(days))

            # Use the most common currency
            currency = transfers[0]["currency"] if transfers else "GBP"

            candidates.append({
                "payee_id": None,  # No payee for transfers
                "payee_name": f"Transfer to {dest_account.name}",
                "suggested_name": dest_account.name,
                "average_amount": round(avg_amount, 2),
                "currency": currency,
                "occurrences": len(months),
                "average_day": avg_day,
                "category_id": None,
                "category_name": "Transfer / Debt Payment",
                "variance_percent": round(variance * 100, 1),
                "is_transfer": True,
                "destination_account_id": dest_account_id,
                "destination_account_name": dest_account.name
            })

    # Sort by occurrences (most frequent first)
    candidates.sort(key=lambda x: (-x["occurrences"], -x["average_amount"]))

    return candidates


@app.post("/recurring")
def create_recurring_expense(
    data: schemas.RecurringExpenseCreate,
    db: Session = Depends(get_db)
):
    """Create a new recurring expense."""
    new_recurring = RecurringExpense(
        name=data.name,
        payee_id=data.payee_id,
        category_id=data.category_id,
        amount=data.amount,
        currency=data.currency,
        day_of_month=data.day_of_month,
        frequency=data.frequency or "monthly",
        start_month=data.start_month,
        is_active=1
    )
    db.add(new_recurring)
    db.commit()
    db.refresh(new_recurring)

    # Create initial history record for this amount
    today = date.today()
    first_of_month = date(today.year, today.month, 1)
    history = RecurringExpenseHistory(
        recurring_expense_id=new_recurring.id,
        amount=data.amount,
        currency=data.currency,
        effective_from=datetime.combine(first_of_month, time.min),
        created_at=datetime.utcnow()
    )
    db.add(history)
    db.commit()

    return {
        "id": new_recurring.id,
        "name": new_recurring.name,
        "payee_id": new_recurring.payee_id,
        "payee_name": new_recurring.payee.name if new_recurring.payee else None,
        "category_id": new_recurring.category_id,
        "category_name": new_recurring.category.name if new_recurring.category else None,
        "amount": new_recurring.amount,
        "currency": new_recurring.currency,
        "day_of_month": new_recurring.day_of_month,
        "frequency": new_recurring.frequency,
        "start_month": new_recurring.start_month,
        "is_active": new_recurring.is_active
    }


@app.put("/recurring/{recurring_id}")
def update_recurring_expense(
    recurring_id: int,
    data: schemas.RecurringExpenseCreate,
    db: Session = Depends(get_db)
):
    """Update a recurring expense."""
    recurring = db.query(RecurringExpense).filter(RecurringExpense.id == recurring_id).first()
    if not recurring:
        raise HTTPException(status_code=404, detail="Recurring expense not found")

    # Check if amount or currency changed - if so, create history record
    amount_changed = (
        round(data.amount, 2) != round(recurring.amount, 2) or
        data.currency != recurring.currency
    )

    if amount_changed:
        # Create history record for the new amount starting from today
        # (the first day of current month, so it applies to this month onwards)
        today = date.today()
        first_of_month = date(today.year, today.month, 1)
        history = RecurringExpenseHistory(
            recurring_expense_id=recurring_id,
            amount=data.amount,
            currency=data.currency,
            effective_from=datetime.combine(first_of_month, time.min),
            created_at=datetime.utcnow()
        )
        db.add(history)

    recurring.name = data.name
    recurring.payee_id = data.payee_id
    recurring.category_id = data.category_id
    recurring.amount = data.amount
    recurring.currency = data.currency
    recurring.day_of_month = data.day_of_month
    recurring.frequency = data.frequency or "monthly"
    recurring.start_month = data.start_month

    db.commit()
    db.refresh(recurring)

    return {
        "id": recurring.id,
        "name": recurring.name,
        "payee_id": recurring.payee_id,
        "payee_name": recurring.payee.name if recurring.payee else None,
        "category_id": recurring.category_id,
        "category_name": recurring.category.name if recurring.category else None,
        "amount": recurring.amount,
        "currency": recurring.currency,
        "day_of_month": recurring.day_of_month,
        "frequency": recurring.frequency,
        "start_month": recurring.start_month,
        "is_active": recurring.is_active
    }


@app.delete("/recurring/{recurring_id}")
def delete_recurring_expense(
    recurring_id: int,
    db: Session = Depends(get_db)
):
    """Delete a recurring expense."""
    recurring = db.query(RecurringExpense).filter(RecurringExpense.id == recurring_id).first()
    if not recurring:
        raise HTTPException(status_code=404, detail="Recurring expense not found")

    db.delete(recurring)
    db.commit()
    return {"message": "Recurring expense deleted successfully"}


@app.patch("/recurring/{recurring_id}/toggle")
def toggle_recurring_expense(
    recurring_id: int,
    db: Session = Depends(get_db)
):
    """Toggle active/inactive status of a recurring expense."""
    recurring = db.query(RecurringExpense).filter(RecurringExpense.id == recurring_id).first()
    if not recurring:
        raise HTTPException(status_code=404, detail="Recurring expense not found")

    recurring.is_active = 0 if recurring.is_active == 1 else 1
    db.commit()
    db.refresh(recurring)

    return {
        "id": recurring.id,
        "name": recurring.name,
        "is_active": recurring.is_active
    }


@app.patch("/recurring/{recurring_id}/toggle-paid/{year_month}")
def toggle_recurring_paid(
    recurring_id: int,
    year_month: str,
    db: Session = Depends(get_db)
):
    """Toggle skip status for a recurring expense in a given month (won't pay this month)."""
    recurring = db.query(RecurringExpense).filter(RecurringExpense.id == recurring_id).first()
    if not recurring:
        raise HTTPException(status_code=404, detail="Recurring expense not found")

    existing = db.query(RecurringExpensePayment).filter(
        RecurringExpensePayment.recurring_expense_id == recurring_id,
        RecurringExpensePayment.year_month == year_month
    ).first()

    if existing:
        db.delete(existing)
        db.commit()
        return {"skipped": False}
    else:
        db.add(RecurringExpensePayment(
            recurring_expense_id=recurring_id,
            year_month=year_month
        ))
        db.commit()
        return {"skipped": True}


# ============================================
# PLANNED EXPENSES ENDPOINTS
# ============================================

@app.get("/planned/{year_month}")
def get_planned_expenses(
    year_month: str,
    db: Session = Depends(get_db)
):
    """Get all planned expenses for a specific month."""
    planned = db.query(PlannedExpense).filter(
        PlannedExpense.year_month == year_month
    ).order_by(PlannedExpense.amount.desc()).all()

    result = []
    for p in planned:
        result.append({
            "id": p.id,
            "year_month": p.year_month,
            "name": p.name,
            "amount": p.amount,
            "currency": p.currency,
            "category_id": p.category_id,
            "category_name": p.category.name if p.category else None,
            "is_paid": p.is_paid,
            "created_at": p.created_at,
            "updated_at": p.updated_at
        })

    return result


@app.post("/planned")
def create_planned_expense(
    data: schemas.PlannedExpenseCreate,
    db: Session = Depends(get_db)
):
    """Create a new planned expense for a specific month."""
    new_planned = PlannedExpense(
        year_month=data.year_month,
        name=data.name,
        amount=data.amount,
        currency=data.currency,
        category_id=data.category_id,
        is_paid=0
    )
    db.add(new_planned)
    db.commit()
    db.refresh(new_planned)

    return {
        "id": new_planned.id,
        "year_month": new_planned.year_month,
        "name": new_planned.name,
        "amount": new_planned.amount,
        "currency": new_planned.currency,
        "category_id": new_planned.category_id,
        "category_name": new_planned.category.name if new_planned.category else None,
        "is_paid": new_planned.is_paid
    }


@app.put("/planned/{planned_id}")
def update_planned_expense(
    planned_id: int,
    data: schemas.PlannedExpenseCreate,
    db: Session = Depends(get_db)
):
    """Update a planned expense."""
    planned = db.query(PlannedExpense).filter(PlannedExpense.id == planned_id).first()
    if not planned:
        raise HTTPException(status_code=404, detail="Planned expense not found")

    planned.name = data.name
    planned.amount = data.amount
    planned.currency = data.currency
    planned.category_id = data.category_id

    db.commit()
    db.refresh(planned)

    return {
        "id": planned.id,
        "year_month": planned.year_month,
        "name": planned.name,
        "amount": planned.amount,
        "currency": planned.currency,
        "category_id": planned.category_id,
        "category_name": planned.category.name if planned.category else None,
        "is_paid": planned.is_paid
    }


@app.delete("/planned/{planned_id}")
def delete_planned_expense(
    planned_id: int,
    db: Session = Depends(get_db)
):
    """Delete a planned expense."""
    planned = db.query(PlannedExpense).filter(PlannedExpense.id == planned_id).first()
    if not planned:
        raise HTTPException(status_code=404, detail="Planned expense not found")

    db.delete(planned)
    db.commit()
    return {"message": "Planned expense deleted successfully"}


@app.patch("/planned/{planned_id}/toggle-paid")
def toggle_planned_expense_paid(
    planned_id: int,
    db: Session = Depends(get_db)
):
    """Toggle paid/unpaid status of a planned expense."""
    planned = db.query(PlannedExpense).filter(PlannedExpense.id == planned_id).first()
    if not planned:
        raise HTTPException(status_code=404, detail="Planned expense not found")

    planned.is_paid = 0 if planned.is_paid == 1 else 1
    db.commit()
    db.refresh(planned)

    return {
        "id": planned.id,
        "name": planned.name,
        "is_paid": planned.is_paid
    }

# ============================================
# SERVE FRONTEND (must be last)
# ============================================

@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/app/index.html")

# Root-level Apple touch icons. iOS falls back to fetching these at the domain
# root when adding to the Home Screen, so serve the 180px icon there too.
@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
def apple_touch_icon():
    return FileResponse("frontend/icons/icon-180.png", media_type="image/png")

app.mount("/app", StaticFiles(directory="frontend"), name="frontend")