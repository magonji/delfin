"""
Migration script to add most common statistics columns to payees table.
Run this once to update the database schema and populate initial values.
"""

from sqlalchemy import text
from backend.database import engine, SessionLocal

def migrate_database():
    """
    Add new columns to payees table and populate with initial statistics.
    """
    db = SessionLocal()
    
    try:
        print("Starting database migration...")
        
        # Add new columns to payees table
        print("Adding new columns to payees table...")
        
        columns_to_add = [
            ("most_common_category_id", "INTEGER"),
            ("most_common_location_id", "INTEGER"),
            ("most_common_project_id", "INTEGER"),
            ("updated_at", "DATETIME")  # No DEFAULT in ALTER TABLE for SQLite
        ]
        
        for column_name, column_type in columns_to_add:
            try:
                with engine.begin() as conn:
                    conn.execute(text(
                        f"ALTER TABLE payees ADD COLUMN {column_name} {column_type}"
                    ))
                print(f"  ✓ Added column: {column_name}")
            except Exception as e:
                if "duplicate column name" in str(e).lower():
                    print(f"  ✓ Column '{column_name}' already exists, skipping...")
                else:
                    print(f"  ✗ Error adding column '{column_name}': {str(e)}")
                    raise
        
        # Initialize updated_at for existing rows
        print("\nInitializing updated_at column for existing payees...")
        with engine.begin() as conn:
            conn.execute(text(
                "UPDATE payees SET updated_at = created_at WHERE updated_at IS NULL"
            ))
        print("  ✓ Updated_at column initialized")
        
        # Now that columns exist, import the models
        from backend.models import Payee, Transaction
        
        # Refresh the session to pick up new columns
        db.close()
        db = SessionLocal()
        
        # Calculate and populate statistics for all payees using raw SQL
        print("\nCalculating statistics for all payees...")
        
        # Get all payees using raw SQL to avoid schema issues
        payees_result = db.execute(text("SELECT id, name FROM payees")).fetchall()
        payees = [{'id': row[0], 'name': row[1]} for row in payees_result]
        
        updated_count = 0
        for payee in payees:
            payee_id = payee['id']
            
            # Get all transactions for this payee using raw SQL
            transactions_result = db.execute(text(
                "SELECT category_id, location_id, project_id FROM transactions WHERE payee_id = :payee_id"
            ), {'payee_id': payee_id}).fetchall()
            
            if not transactions_result:
                continue
            
            # Count occurrences
            category_counts = {}
            location_counts = {}
            project_counts = {}
            
            for row in transactions_result:
                category_id, location_id, project_id = row
                
                if category_id:
                    category_counts[category_id] = category_counts.get(category_id, 0) + 1
                if location_id:
                    location_counts[location_id] = location_counts.get(location_id, 0) + 1
                if project_id:
                    project_counts[project_id] = project_counts.get(project_id, 0) + 1
            
            # Get most common values
            most_common_category = max(category_counts, key=category_counts.get) if category_counts else None
            most_common_location = max(location_counts, key=location_counts.get) if location_counts else None
            most_common_project = max(project_counts, key=project_counts.get) if project_counts else None
            
            # Update using raw SQL
            db.execute(text("""
                UPDATE payees 
                SET most_common_category_id = :cat_id,
                    most_common_location_id = :loc_id,
                    most_common_project_id = :proj_id,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :payee_id
            """), {
                'cat_id': most_common_category,
                'loc_id': most_common_location,
                'proj_id': most_common_project,
                'payee_id': payee_id
            })
            
            updated_count += 1
            
            if updated_count % 10 == 0:
                print(f"  Processed {updated_count}/{len(payees)} payees...")
        
        db.commit()
        print(f"✓ Statistics calculated for {updated_count} payees")
        
        print("\n✓ Migration completed successfully!")
        print("\nNext steps:")
        print("1. Restart your FastAPI server")
        print("2. Test the import CSV functionality in tools.html")
        print("3. Use 'Update All Payee Statistics' button in Manage Payees when needed")
        
    except Exception as e:
        print(f"\n✗ Migration failed: {str(e)}")
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    migrate_database()