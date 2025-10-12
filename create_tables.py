from backend.database import engine, Base
from backend.models import Account, Category, Payee, Location, Project, Transaction

def create_tables():
    """
    Creates all database tables based on the models.
    """
    print("Creating database tables...")
    Base.metadata.create_all(bind=engine)
    print("✅ Tables created successfully!")

if __name__ == "__main__":
    create_tables()