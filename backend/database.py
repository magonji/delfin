"""
Database configuration and session management.

The database is encrypted with SQLCipher. There is **no engine until the app is
unlocked** with the data key (DEK), which only happens after a successful login
(see backend/security.py). Until then the app is "locked": get_db() raises 401
and protected routes are refused. This is what makes the at-rest encryption real
— without the password (which unwraps the DEK) the file cannot be opened.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

DB_PATH = "./data/finance.db"

Base = declarative_base()

# Set on unlock(), cleared on lock(). Modules must read these dynamically
# (e.g. database.SessionLocal()), never import them once at module load.
engine = None
SessionLocal = None
_dek_hex = None


def is_unlocked() -> bool:
    return engine is not None


def get_engine():
    return engine


def get_dek_hex():
    """Current SQLCipher key (hex), or None when locked. Needed for keyed backups."""
    return _dek_hex


def _apply_pragmas(dbapi_connection):
    cur = dbapi_connection.cursor()
    # The key MUST be set first, before any other access on the connection.
    cur.execute(f"PRAGMA key = \"x'{_dek_hex}'\"")
    cur.execute("PRAGMA journal_mode=WAL")       # Faster concurrent reads
    cur.execute("PRAGMA synchronous=NORMAL")      # Faster writes (safe with WAL)
    cur.execute("PRAGMA cache_size=-64000")        # 64MB cache
    cur.execute("PRAGMA temp_store=MEMORY")        # Temp tables in RAM
    cur.close()


# Columns added to a table after it first shipped. ``create_all`` builds missing
# tables but never alters existing ones, so a database created by an older build
# would quietly lack these. Appending a column is all SQLite needs to do here.
_ADDED_COLUMNS = {
    "budget_items": {
        "day_rule": "VARCHAR DEFAULT 'exact'",
        "day_ordinal": "INTEGER",
        "series_id": "INTEGER",
    },
    "transactions": {
        # Lines of a split transaction, sharing the id of the first line.
        "split_group_id": "INTEGER",
    },
    "loans": {
        # What the loan cost to arrange, and whether it was paid at the outset
        # or added to the debt.
        "opening_fee": "FLOAT DEFAULT 0.0",
        "fee_treatment": "VARCHAR DEFAULT 'upfront'",
        # A standing charge for having the loan, and the price of ending it early.
        "recurring_fee": "FLOAT DEFAULT 0.0",
        "recurring_fee_months": "INTEGER DEFAULT 1",
        "early_repayment_fee_pct": "FLOAT DEFAULT 0.0",
        # Whether interest accrues by the month or by the day.
        "interest_unit": "VARCHAR DEFAULT 'month'",
        # When the first instalment falls, when it is not a whole period after
        # the drawdown. NULL keeps the derived date, so old rows are unaffected.
        "first_payment_date": "DATETIME",
    },
}

# Indexes on tables that already existed. ``create_all`` skips a table it finds,
# indexes included, so an index added later has to be created explicitly.
_ADDED_INDEXES = (
    ("transactions", "CREATE INDEX IF NOT EXISTS ix_transactions_split_group_id "
                     "ON transactions (split_group_id)"),
    ("transactions", "CREATE INDEX IF NOT EXISTS idx_transaction_split_group "
                     "ON transactions (split_group_id, id)"),
)

# Run after the columns exist, to give the new ones a sensible value on rows that
# predate them. Each must be safe to run on every start.
_BACKFILLS = (
    # Every item that has never been versioned is a series of one, keyed by itself.
    ("budget_items", "series_id",
     "UPDATE budget_items SET series_id = id WHERE series_id IS NULL"),
)


def _ensure_columns(eng) -> None:
    """Append any column a newer build expects on an already-created table."""
    with eng.connect() as c:
        for table, columns in _ADDED_COLUMNS.items():
            present = {row[1] for row in c.exec_driver_sql(f'PRAGMA table_info("{table}")')}
            if not present:
                continue  # table doesn't exist yet — create_all builds it complete
            for name, ddl in columns.items():
                if name not in present:
                    c.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}')
        for table, sql in _ADDED_INDEXES:
            if {row[1] for row in c.exec_driver_sql(f'PRAGMA table_info("{table}")')}:
                c.exec_driver_sql(sql)
        for table, column, sql in _BACKFILLS:
            present = {row[1] for row in c.exec_driver_sql(f'PRAGMA table_info("{table}")')}
            if column in present:
                c.exec_driver_sql(sql)
        c.commit()


def unlock(dek_hex: str) -> None:
    """Open the encrypted DB with the given key and ensure the schema exists.
    Raises if the key cannot open the file. No-op if already unlocked."""
    global engine, SessionLocal, _dek_hex
    if engine is not None:
        return
    import sqlcipher3.dbapi2 as sqlcipher
    _dek_hex = dek_hex
    eng = create_engine(
        f"sqlite:///{DB_PATH}",
        module=sqlcipher,
        connect_args={"check_same_thread": False},
    )
    event.listen(eng, "connect", lambda conn, rec: _apply_pragmas(conn))
    try:
        # Force a real read so a wrong key / non-encrypted file fails loudly here.
        with eng.connect() as c:
            c.exec_driver_sql("SELECT count(*) FROM sqlite_master")
    except Exception:
        eng.dispose()
        _dek_hex = None
        raise
    # First open of a brand-new DB file creates an empty encrypted DB; build tables.
    Base.metadata.create_all(bind=eng)
    _ensure_columns(eng)
    engine = eng
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=eng)


def lock() -> None:
    """Close the DB and forget the key (app becomes locked again)."""
    global engine, SessionLocal, _dek_hex
    if engine is not None:
        engine.dispose()
    engine = None
    SessionLocal = None
    _dek_hex = None


def get_db():
    """Create a database session per request. Raises 401 while the app is locked."""
    if SessionLocal is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Application is locked — please log in.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
