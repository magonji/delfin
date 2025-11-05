"""
Utility to clean duplicate categories from the database.

This script identifies and merges duplicate categories (same name and parent)
by reassigning all transactions to a single category and removing duplicates.
"""

from backend.database import SessionLocal
from backend.models import Category, Transaction
from sqlalchemy import func


def find_duplicate_categories(db):
    """
    Find categories with duplicate name+parent combinations.
    Returns a list of tuples: (name, parent, [list of category IDs])
    """
    # Group categories by name and parent
    duplicates = db.query(
        Category.name,
        Category.parent,
        func.group_concat(Category.id).label('ids')
    ).group_by(
        Category.name,
        Category.parent
    ).having(
        func.count(Category.id) > 1
    ).all()
    
    duplicate_groups = []
    for name, parent, ids_str in duplicates:
        category_ids = [int(id_val) for id_val in ids_str.split(',')]
        duplicate_groups.append((name, parent, category_ids))
    
    return duplicate_groups


def merge_duplicate_categories(db, name, parent, category_ids):
    """
    Merge duplicate categories by:
    1. Keeping the first category (lowest ID)
    2. Reassigning all transactions to the first category
    3. Deleting duplicate categories
    """
    # Keep the first category (lowest ID)
    primary_category_id = min(category_ids)
    duplicate_ids = [cat_id for cat_id in category_ids if cat_id != primary_category_id]
    
    print(f"\n📋 Category: '{name}' (parent: {parent or 'None'})")
    print(f"   Keeping ID {primary_category_id}")
    print(f"   Merging IDs: {duplicate_ids}")
    
    # Reassign all transactions from duplicate categories to primary
    transactions_updated = 0
    for duplicate_id in duplicate_ids:
        result = db.query(Transaction).filter(
            Transaction.category_id == duplicate_id
        ).update({Transaction.category_id: primary_category_id})
        transactions_updated += result
    
    print(f"   ✏️  Reassigned {transactions_updated} transactions")
    
    # Delete duplicate categories
    for duplicate_id in duplicate_ids:
        category = db.query(Category).filter(Category.id == duplicate_id).first()
        if category:
            db.delete(category)
    
    print(f"   🗑️  Deleted {len(duplicate_ids)} duplicate categories")


def main():
    """
    Main routine to clean duplicate categories.
    """
    print("\n" + "="*60)
    print("DELFIN - CATEGORY DEDUPLICATION UTILITY")
    print("="*60)
    
    db = SessionLocal()
    
    try:
        # Find duplicates
        print("\n🔍 Searching for duplicate categories...")
        duplicate_groups = find_duplicate_categories(db)
        
        if not duplicate_groups:
            print("\n✅ No duplicate categories found!")
            print("Your database is clean.")
            return
        
        print(f"\n⚠️  Found {len(duplicate_groups)} sets of duplicate categories")
        
        # Ask for confirmation
        response = input("\nProceed with deduplication? (yes/no): ").strip().lower()
        
        if response not in ['yes', 'y']:
            print("\n❌ Deduplication cancelled")
            return
        
        # Process each group
        print("\n🔄 Processing duplicates...")
        
        for name, parent, category_ids in duplicate_groups:
            merge_duplicate_categories(db, name, parent, category_ids)
        
        # Commit all changes
        db.commit()
        
        print("\n" + "="*60)
        print("✅ DEDUPLICATION COMPLETE!")
        print("="*60)
        print(f"\nMerged {len(duplicate_groups)} sets of duplicate categories")
        print("\nYour category list should now be clean.")
        
    except Exception as e:
        print(f"\n❌ Error during deduplication: {e}")
        db.rollback()
        print("Changes have been rolled back.")
    finally:
        db.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Deduplication cancelled by user")