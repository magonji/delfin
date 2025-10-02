import pandas as pd
from datetime import datetime
from sqlalchemy.orm import Session
from backend.database import SessionLocal, engine
from backend.models import Account, Category, Payee, Location, Project, Transaction, Base
import questionary
import os


def get_or_create_account(db: Session, name: str, currency: str = "GBP"):
    """
    Gets an existing account or creates a new one.
    """
    account = db.query(Account).filter(Account.name == name).first()
    if not account:
        account = Account(name=name, currency=currency)
        db.add(account)
        db.flush()  # Changed from commit to flush
    return account


def get_or_create_category(db: Session, name: str, parent: str = None):
    """
    Gets an existing category or creates a new one.
    """
    if not name:
        return None
    
    category = db.query(Category).filter(Category.name == name, Category.parent == parent).first()
    if not category:
        category = Category(name=name, parent=parent)
        db.add(category)
        db.flush()  # Changed from commit to flush
    return category


def get_or_create_payee(db: Session, name: str):
    """
    Gets an existing payee or creates a new one.
    """
    if not name:
        return None
    
    payee = db.query(Payee).filter(Payee.name == name).first()
    if not payee:
        payee = Payee(name=name)
        db.add(payee)
        db.flush()  # Changed from commit to flush
    return payee


def get_or_create_location(db: Session, name: str):
    """
    Gets an existing location or creates a new one.
    """
    if not name:
        return None
    
    location = db.query(Location).filter(Location.name == name).first()
    if not location:
        location = Location(name=name)
        db.add(location)
        db.flush()  # Changed from commit to flush
    return location


def get_or_create_project(db: Session, name: str):
    """
    Gets an existing project or creates a new one.
    """
    if not name:
        return None
    
    project = db.query(Project).filter(Project.name == name).first()
    if not project:
        project = Project(name=name)
        db.add(project)
        db.flush()  # Changed from commit to flush
    return project


def import_financisto_csv(csv_path: str):
    """
    Imports transactions from Financisto CSV export.
    """
    print(f"Reading CSV file: {csv_path}")
    
    # Read CSV with pandas
    df = pd.read_csv(csv_path)
    
    print(f"Found {len(df)} transactions to import")
    
    # Create database session
    db = SessionLocal()
    
    try:
        imported = 0
        skipped = 0
        
        for index, row in df.iterrows():
            try:
                # Parse date and time
                date_str = f"{row['date']} {row['time']}"
                transaction_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
                
                # Get or create related entities
                account = get_or_create_account(db, row['account'], row['currency'])
                category = get_or_create_category(db, row['category'], row['parent']) if pd.notna(row['category']) else None
                payee = get_or_create_payee(db, row['payee']) if pd.notna(row['payee']) else None
                location = get_or_create_location(db, row['location']) if pd.notna(row['location']) else None
                project = get_or_create_project(db, row['project']) if pd.notna(row['project']) else None
                
                # Create transaction
                transaction = Transaction(
                    date=transaction_date,
                    amount=float(row['amount']),
                    currency=row['currency'],
                    note=row['note'] if pd.notna(row['note']) else None,
                    account_id=account.id,
                    category_id=category.id if category else None,
                    payee_id=payee.id if payee else None,
                    location_id=location.id if location else None,
                    project_id=project.id if project else None
                )
                
                db.add(transaction)
                imported += 1
                
                # Commit every 100 transactions for performance
                if imported % 100 == 0:
                    db.commit()
                    print(f"Imported {imported} transactions...")
                    
            except Exception as e:
                print(f"Error importing row {index}: {e}")
                skipped += 1
                continue
        
        # Final commit
        db.commit()
        
        print(f"\n✅ Import complete!")
        print(f"   Imported: {imported}")
        print(f"   Skipped: {skipped}")
        
    finally:
        db.close()


if __name__ == "__main__":
    
    # Ask user for the folder path
    folder_path = questionary.path(
        "Where is your CSV file located? (Enter folder path)",
        only_directories=True
    ).ask()
    
    if not folder_path:
        print("No folder selected.")
        exit(1)
    
    # Get list of CSV files in the specified folder
    try:
        csv_files = [f for f in os.listdir(folder_path) if f.endswith('.csv')]
    except FileNotFoundError:
        print(f"❌ Folder not found: {folder_path}")
        exit(1)
    
    if not csv_files:
        print(f"❌ No CSV files found in: {folder_path}")
        print("Please check the folder path.")
        exit(1)
    
    # Ask user to select a file
    csv_file = questionary.select(
        "Which CSV file would you like to import?",
        choices=csv_files
    ).ask()
    
    if csv_file:
        # Build full path
        full_path = os.path.join(folder_path, csv_file)
        
        # Confirm before importing
        confirm = questionary.confirm(
            f"Import '{csv_file}' from '{folder_path}'?\nThis will add transactions to the database."
        ).ask()
        
        if confirm:
            import_financisto_csv(full_path)
        else:
            print("Import cancelled.")
    else:
        print("No file selected.")