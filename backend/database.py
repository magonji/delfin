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
