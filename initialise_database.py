"""
Initialisation script for Delfin database.
Run this after importing your Financisto CSV for the first time.

This script will:
1. Update exchange rates from the API
2. Initialise balance calculations for all transactions
3. Calculate most common associations for all payees
"""

import sys
from backend.database import SessionLocal
from backend.models import Transaction, Payee
from backend.balance_calculator import initialise_all_balances
from sqlalchemy import func, text
from datetime import datetime


def update_exchange_rates():
    """
    Fetch and store the latest exchange rates.
    """
    print("\n" + "="*60)
    print("STEP 1: Updating Exchange Rates")
    print("="*60)
    
    try:
        from update_exchange_rates import update_exchange_rates as fetch_rates
        fetch_rates()
    except Exception as e:
        print(f"⚠️  Warning: Failed to update exchange rates: {e}")
        print("   You can update them manually later with: python update_exchange_rates.py")



def reorder_transactions():
    """
    Reorder all transactions chronologically in the database.
    """
    print("\n" + "="*60)
    print("STEP 2: Reordering Transactions Chronologically")
    print("="*60)
    
    db = SessionLocal()
    try:
        # Get total count
        total_transactions = db.query(func.count(Transaction.id)).scalar()
        
        if total_transactions == 0:
            print("ℹ️  No transactions to reorder")
            return
        
        print(f"📊 Found {total_transactions} transactions")
        print(f"⏳ Reordering by date and ID...")
        
        # Get raw connection
        connection = db.connection()
        
        # Create temporary table with ordered data
        connection.execute(text("""
            CREATE TABLE transactions_ordered AS
            SELECT * FROM transactions
            ORDER BY date ASC, id ASC
        """))
        
        # Drop original table
        connection.execute(text("DROP TABLE transactions"))
        
        # Rename temporary table
        connection.execute(text("ALTER TABLE transactions_ordered RENAME TO transactions"))
        
        # Recreate all indexes
        print("   ⏳ Recreating indexes...")
        indices = [
            "CREATE INDEX IF NOT EXISTS ix_transactions_date ON transactions(date)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_currency ON transactions(currency)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_account_id ON transactions(account_id)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_category_id ON transactions(category_id)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_payee_id ON transactions(payee_id)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_location_id ON transactions(location_id)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_project_id ON transactions(project_id)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_account_balance_after ON transactions(account_balance_after)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_total_balance_after ON transactions(total_balance_after)",
            "CREATE INDEX IF NOT EXISTS ix_transactions_created_at ON transactions(created_at)",
            "CREATE INDEX IF NOT EXISTS idx_transaction_account_date ON transactions(account_id, date)",
            "CREATE INDEX IF NOT EXISTS idx_transaction_currency_date ON transactions(currency, date)",
            "CREATE INDEX IF NOT EXISTS idx_transaction_date_amount ON transactions(date, amount)",
            "CREATE INDEX IF NOT EXISTS idx_transaction_category_date ON transactions(category_id, date)",
            "CREATE INDEX IF NOT EXISTS idx_transaction_payee_date ON transactions(payee_id, date)",
        ]
        
        for idx in indices:
            connection.execute(text(idx))
        
        db.commit()
        print("✅ Transactions reordered successfully!")
        
    except Exception as e:
        print(f"❌ Error reordering transactions: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


def initialise_balances():
    """
    Calculate and cache balance information for all transactions.
    """
    print("\n" + "="*60)
    print("STEP 3: Initialising Balance Calculations")
    print("="*60)
    
    db = SessionLocal()
    try:
        # Check if balances need initialisation
        total_transactions = db.query(func.count(Transaction.id)).scalar()
        uninitialised = db.query(func.count(Transaction.id)).filter(
            Transaction.account_balance_after == None
        ).scalar()
        
        if uninitialised == 0:
            print("✅ Balances already initialised")
            return
        
        print(f"📊 Found {total_transactions} transactions")
        print(f"🔄 Initialising balances for {uninitialised} transactions...")
        
        initialise_all_balances(db)
        print("✅ Balance calculations complete!")
        
    except Exception as e:
        print(f"❌ Error initialising balances: {e}")
        sys.exit(1)
    finally:
        db.close()


def calculate_payee_statistics():
    """
    Calculate most common category, location, and project for each payee.
    """
    print("\n" + "="*60)
    print("STEP 4: Calculating Payee Statistics")
    print("="*60)
    
    db = SessionLocal()
    try:
        payees = db.query(Payee).all()
        total_payees = len(payees)
        
        if total_payees == 0:
            print("ℹ️  No payees found")
            return
        
        print(f"📊 Processing {total_payees} payees...")
        updated_count = 0
        
        for index, payee in enumerate(payees, 1):
            # Progress indicator every 50 payees
            if index % 50 == 0:
                print(f"   Processed {index}/{total_payees} payees...")
            
            # Get all transactions for this payee
            transactions = db.query(Transaction).filter(
                Transaction.payee_id == payee.id
            ).all()
            
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
            has_changes = False
            
            new_category = max(category_counts, key=category_counts.get) if category_counts else None
            new_location = max(location_counts, key=location_counts.get) if location_counts else None
            new_project = max(project_counts, key=project_counts.get) if project_counts else None
            
            if payee.most_common_category_id != new_category:
                payee.most_common_category_id = new_category
                has_changes = True
            
            if payee.most_common_location_id != new_location:
                payee.most_common_location_id = new_location
                has_changes = True
            
            if payee.most_common_project_id != new_project:
                payee.most_common_project_id = new_project
                has_changes = True
            
            if has_changes:
                payee.updated_at = datetime.utcnow()
                updated_count += 1
        
        db.commit()
        print(f"✅ Updated statistics for {updated_count} payees")
        
    except Exception as e:
        print(f"❌ Error calculating payee statistics: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()


def main():
    """
    Main initialisation routine.
    """
    print("\n" + "🐬" * 30)
    print("DELFIN DATABASE INITIALISATION")
    print("🐬" * 30)
    
    db = SessionLocal()
    try:
        # Check if there's data to initialise
        transaction_count = db.query(func.count(Transaction.id)).scalar()
        
        if transaction_count == 0:
            print("\n⚠️  No transactions found in database!")
            print("Please import your Financisto CSV first:")
            print("   python import_financisto_csv.py")
            sys.exit(1)
        
        print(f"\n✅ Found {transaction_count} transactions in database")
        print("\nThis script will:")
        print("  1. Update exchange rates")
        print("  2. Reorder transactions chronologically")
        print("  3. Initialise balance calculations")
        print("  4. Calculate payee statistics")
        
    finally:
        db.close()
    # Run all initialisation steps
    update_exchange_rates()
    reorder_transactions()
    initialise_balances()
    calculate_payee_statistics()
    
    # Final summary
    print("\n" + "="*60)
    print("🎉 INITIALISATION COMPLETE!")
    print("="*60)
    print("\nYour database is now ready to use.")
    print("\nNext steps:")
    print("  1. Start the server: uvicorn backend.main:app --reload")
    print("  2. Open frontend/index.html in your browser")
    print("\nEnjoy Delfin! 🐬")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Initialisation cancelled by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Unexpected error: {e}")
        sys.exit(1)