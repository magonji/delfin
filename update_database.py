from backend.database import engine, Base, SessionLocal
from backend.models import Account, Category, Payee, Location, Project, Transaction, ExchangeRate
from backend.balance_calculator import initialise_all_balances
from sqlalchemy import func
from datetime import datetime

def recalculate_payee_statistics(db):
    """
    Recalculates the most common category, location, and project for all payees.
    This ensures payee statistics are up to date.
    """
    print("\n🔄 Recalculating payee statistics...")
    
    payees = db.query(Payee).all()
    updated_count = 0
    
    for payee in payees:
        # Get all transactions for this payee
        transactions = db.query(Transaction).filter(Transaction.payee_id == payee.id).all()
        
        if not transactions:
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
        
        # Update most common values
        old_category = payee.most_common_category_id
        old_location = payee.most_common_location_id
        old_project = payee.most_common_project_id
        
        payee.most_common_category_id = max(category_counts, key=category_counts.get) if category_counts else None
        payee.most_common_location_id = max(location_counts, key=location_counts.get) if location_counts else None
        payee.most_common_project_id = max(project_counts, key=project_counts.get) if project_counts else None
        payee.updated_at = datetime.utcnow()
        
        # Check if anything changed
        if (old_category != payee.most_common_category_id or 
            old_location != payee.most_common_location_id or 
            old_project != payee.most_common_project_id):
            updated_count += 1
    
    db.commit()
    print(f"   ✅ Updated statistics for {updated_count} payees")


def update_tables():
    """
    Creates any new database tables that don't exist yet and initialises
    calculated fields if there's existing data.
    This won't modify existing table structures.
    """
    print("🔄 Updating database schema...")
    Base.metadata.create_all(bind=engine)
    print("✅ Database schema updated successfully!")
    
    # Check if there's existing data that needs initialisation
    db = SessionLocal()
    try:
        transaction_count = db.query(func.count(Transaction.id)).scalar()
        
        if transaction_count > 0:
            print(f"\n📊 Found {transaction_count} existing transactions")
            
            # Check if balances are already initialised
            uninitialised_balances = db.query(func.count(Transaction.id)).filter(
                Transaction.account_balance_after == None
            ).scalar()
            
            if uninitialised_balances > 0:
                print(f"\n⚠️  Found {uninitialised_balances} transactions without balance data")
                print("🔄 Initialising balance calculations...")
                initialise_all_balances(db)
                print("✅ Balance calculations initialised")
            else:
                print("✅ Balance calculations already initialised")
            
            # Recalculate payee statistics
            recalculate_payee_statistics(db)
        else:
            print("\n📝 No existing transactions found - database is ready for import")
    
    finally:
        db.close()


if __name__ == "__main__":
    update_tables()