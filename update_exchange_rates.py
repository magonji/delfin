import requests
from datetime import datetime
from sqlalchemy.orm import Session
from backend.database import SessionLocal
from backend.models import ExchangeRate, Transaction
import time


def get_currencies_in_use(db: Session):
    """
    Get all unique currencies currently used in transactions.
    """
    currencies = db.query(Transaction.currency).distinct().all()
    return [c[0] for c in currencies if c[0]]


def fetch_exchange_rates_from_api(base_currency: str = "GBP"):
    """
    Fetch current exchange rates from free API.
    Using exchangerate-api.com which doesn't require API key for basic usage.
    """
    try:
        url = f"https://api.exchangerate-api.com/v4/latest/{base_currency}"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            return data['rates']
        else:
            print(f"❌ API request failed with status code: {response.status_code}")
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"❌ Error fetching exchange rates: {e}")
        return None


def update_exchange_rates():
    """
    Fetch latest exchange rates and store them in the database.
    """
    print("🔄 Updating exchange rates...")
    
    db = SessionLocal()
    
    try:
        # Get currencies we need to track
        currencies_in_use = get_currencies_in_use(db)
        
        if not currencies_in_use:
            print("⚠️  No currencies found in transactions.")
            return
        
        print(f"📊 Currencies in use: {', '.join(currencies_in_use)}")
        
        # Fetch latest rates
        rates = fetch_exchange_rates_from_api("GBP")
        
        if not rates:
            print("❌ Failed to fetch exchange rates from API")
            return
        
        # Current timestamp
        now = datetime.utcnow()
        
        # Store rates for each currency in use
        stored_count = 0
        for currency in currencies_in_use:
            if currency in rates:
                # Check if we already have a rate for today
                today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                existing = db.query(ExchangeRate).filter(
                    ExchangeRate.currency == currency,
                    ExchangeRate.date >= today_start
                ).first()
                
                if existing:
                    # Update existing rate
                    existing.rate = rates[currency]
                    existing.created_at = now
                    print(f"   ✏️  Updated {currency}: {rates[currency]}")
                else:
                    # Create new rate entry
                    new_rate = ExchangeRate(
                        currency=currency,
                        rate=rates[currency],
                        date=now
                    )
                    db.add(new_rate)
                    print(f"   ✅ Added {currency}: {rates[currency]}")
                
                stored_count += 1
            else:
                print(f"   ⚠️  Rate not available for {currency}")
        
        # Commit all changes
        db.commit()
        
        print(f"\n✅ Successfully updated {stored_count} exchange rates!")
        
    except Exception as e:
        print(f"❌ Error updating exchange rates: {e}")
        db.rollback()
    finally:
        db.close()


def get_latest_rates(db: Session):
    """
    Get the most recent exchange rates for all currencies.
    Returns a dictionary with currency codes as keys and rates as values.
    """
    from sqlalchemy import func
    
    # Get the most recent rate for each currency
    subquery = db.query(
        ExchangeRate.currency,
        func.max(ExchangeRate.date).label('max_date')
    ).group_by(ExchangeRate.currency).subquery()
    
    rates_query = db.query(ExchangeRate).join(
        subquery,
        (ExchangeRate.currency == subquery.c.currency) &
        (ExchangeRate.date == subquery.c.max_date)
    ).all()
    
    rates_dict = {rate.currency: rate.rate for rate in rates_query}
    
    # Always ensure GBP is 1.0 (base currency)
    rates_dict['GBP'] = 1.0
    
    return rates_dict


if __name__ == "__main__":
    update_exchange_rates()