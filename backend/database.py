"""
Database configuration and session management.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./data/finance.db"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    connect_args={"check_same_thread": False}
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")       # Faster concurrent reads
    cursor.execute("PRAGMA synchronous=NORMAL")      # Faster writes (safe with WAL)
    cursor.execute("PRAGMA cache_size=-64000")        # 64MB cache (default is 2MB)
    cursor.execute("PRAGMA mmap_size=268435456")      # Memory-map 256MB of the DB
    cursor.execute("PRAGMA temp_store=MEMORY")        # Temp tables in RAM
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Create a database session for each request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()