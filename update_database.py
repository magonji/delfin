from backend.database import engine, Base
from backend.models import Account, Category, Payee, Location, Project, Transaction, ExchangeRate

def update_tables():
    """
    Creates any new database tables that don't exist yet.
    This won't modify existing tables.
    """
    print("🔄 Updating database schema...")
    Base.metadata.create_all(bind=engine)
    print("✅ Database updated successfully!")

if __name__ == "__main__":
    update_tables()