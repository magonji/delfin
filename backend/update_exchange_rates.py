import requests
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from sqlalchemy.orm import Session
from sqlalchemy import func
from .database import SessionLocal
from .models import ExchangeRate, Transaction
from typing import Dict, List, Tuple


def get_first_transaction_date(db: Session) -> date:
    """
    Get the date of the first transaction in the database.
    Returns today's date if no transactions exist.
    """
    first_transaction = db.query(Transaction).order_by(Transaction.date.asc()).first()
    if first_transaction:
        # Convert datetime to date if necessary
        if isinstance(first_transaction.date, datetime):
            return first_transaction.date.date()
        return first_transaction.date
    return date.today()


def get_last_exchange_rate_date(db: Session) -> date:
    """
    Get the most recent date for which we have exchange rates.
    Returns None if no exchange rates exist.
    """
    last_rate = db.query(func.max(ExchangeRate.date)).scalar()
    if last_rate:
        if isinstance(last_rate, datetime):
            return last_rate.date()
        return last_rate
    return None


def get_currencies_in_use(db: Session) -> List[str]:
    """
    Get all unique currencies currently used in transactions.
    """
    currencies = db.query(Transaction.currency).distinct().all()
    return [c[0] for c in currencies if c[0] and c[0] != 'GBP']


def fetch_ecb_historical_rates() -> Dict[date, Dict[str, float]]:
    """
    Fetch historical exchange rates from European Central Bank XML.
    Returns a dictionary with dates as keys and currency:rate dictionaries as values.
    
    The ECB provides rates with EUR as base currency.
    """
    try:
        url = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml'
        print(f"📡 Fetching historical rates from ECB...")
        
        response = requests.get(url, timeout=30)
        
        if response.status_code != 200:
            print(f"❌ ECB request failed with status code: {response.status_code}")
            return None
        
        # Parse XML
        root = ET.fromstring(response.content)
        
        # Namespace for ECB XML
        ns = {
            'gesmes': 'http://www.gesmes.org/xml/2002-09-01',
            'ecb': 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'
        }
        
        historical_rates = {}
        
        # Navigate through XML structure
        # Structure: Envelope > Cube > Cube (with date) > Cube (with currency and rate)
        for day_cube in root.findall('.//ecb:Cube[@time]', ns):
            date_str = day_cube.get('time')
            rates_date = datetime.strptime(date_str, '%Y-%m-%d').date()
            
            daily_rates = {}
            for rate_cube in day_cube.findall('ecb:Cube[@currency]', ns):
                currency = rate_cube.get('currency')
                rate = float(rate_cube.get('rate'))
                daily_rates[currency] = rate
            
            if daily_rates:
                historical_rates[rates_date] = daily_rates
        
        print(f"✅ Fetched {len(historical_rates)} days of historical rates")
        return historical_rates
        
    except requests.exceptions.RequestException as e:
        print(f"❌ Network error fetching ECB rates: {e}")
        return None
    except ET.ParseError as e:
        print(f"❌ Error parsing ECB XML: {e}")
        return None
    except Exception as e:
        print(f"❌ Unexpected error fetching ECB rates: {e}")
        return None


def calculate_gbp_rate(eur_rates: Dict[str, float], currency: str) -> float:
    """
    Calculate exchange rate from GBP to another currency.
    
    ECB provides EUR as base, so:
    - For GBP itself: We need the inverse of EUR/GBP (which gives us GBP/EUR)
    - For other currencies: Rate_CURRENCY_GBP = Rate_CURRENCY_EUR / Rate_GBP_EUR
    
    Args:
        eur_rates: Dictionary with ECB rates (EUR as base)
        currency: Target currency code
    
    Returns:
        Exchange rate from GBP to target currency
    """
    if currency == 'GBP':
        return 1.0
    
    if currency == 'EUR':
        # For EUR, we need the inverse of the GBP rate from ECB
        # ECB gives us EUR/GBP, we need GBP/EUR
        gbp_eur_rate = eur_rates.get('GBP')
        if gbp_eur_rate:
            return 1.0 / gbp_eur_rate
        return None
    
    # For other currencies
    currency_eur_rate = eur_rates.get(currency)
    gbp_eur_rate = eur_rates.get('GBP')
    
    if currency_eur_rate and gbp_eur_rate:
        # Rate_CURRENCY_GBP = Rate_CURRENCY_EUR / Rate_GBP_EUR
        return currency_eur_rate / gbp_eur_rate
    
    return None


def filter_new_dates(historical_rates: Dict[date, Dict[str, float]], 
                     start_date: date, 
                     last_stored_date: date = None) -> Dict[date, Dict[str, float]]:
    """
    Filter historical rates to only include dates that need to be added.
    
    Args:
        historical_rates: All historical rates from ECB
        start_date: First transaction date (earliest date we care about)
        last_stored_date: Most recent date we already have in DB
    
    Returns:
        Filtered dictionary with only new dates
    """
    filtered_rates = {}
    
    for rates_date, rates in historical_rates.items():
        # Skip dates before first transaction
        if rates_date < start_date:
            continue
        
        # Skip dates we already have
        if last_stored_date and rates_date <= last_stored_date:
            continue
        
        filtered_rates[rates_date] = rates
    
    return filtered_rates


def store_rates_for_date(db: Session, rates_date: date, eur_rates: Dict[str, float], 
                         currencies_needed: List[str]) -> int:
    """
    Store exchange rates for a specific date.
    
    Args:
        db: Database session
        rates_date: Date for these rates
        eur_rates: ECB rates (EUR as base) for this date
        currencies_needed: List of currencies we need to store
    
    Returns:
        Number of rates successfully stored
    """
    stored_count = 0
    
    for currency in currencies_needed:
        gbp_rate = calculate_gbp_rate(eur_rates, currency)
        
        if gbp_rate is None:
            continue
        
        # Check if this rate already exists
        existing = db.query(ExchangeRate).filter(
            ExchangeRate.currency == currency,
            ExchangeRate.date == rates_date
        ).first()
        
        if existing:
            # Update existing rate
            existing.rate = gbp_rate
            existing.created_at = datetime.utcnow()
        else:
            # Create new rate entry
            new_rate = ExchangeRate(
                currency=currency,
                rate=gbp_rate,
                date=datetime.combine(rates_date, datetime.min.time()),
                created_at=datetime.utcnow()
            )
            db.add(new_rate)
        
        stored_count += 1
    
    return stored_count


def update_exchange_rates():
    """
    Fetch historical exchange rates from ECB and store them in the database.
    Only fetches rates from the first transaction date onwards.
    Only adds new dates (incremental update).
    """
    print("🔄 Starting exchange rate update from ECB...")
    print("=" * 60)
    
    db = SessionLocal()
    
    try:
        # Step 1: Determine date range
        first_transaction_date = get_first_transaction_date(db)
        last_stored_date = get_last_exchange_rate_date(db)
        
        print(f"📅 First transaction date: {first_transaction_date}")
        if last_stored_date:
            print(f"📅 Last stored rate date: {last_stored_date}")
            print(f"📅 Will fetch rates from: {last_stored_date + timedelta(days=1)} to today")
        else:
            print(f"📅 No existing rates found")
            print(f"📅 Will fetch rates from: {first_transaction_date} to today")
        
        # Step 2: Get currencies we need
        currencies_needed = get_currencies_in_use(db)
        
        if not currencies_needed:
            print("⚠️  No currencies found in transactions (other than GBP).")
            print("✅ Nothing to update!")
            return
        
        print(f"📊 Currencies in use: {', '.join(currencies_needed)}")
        print()
        
        # Step 3: Fetch historical rates from ECB
        historical_rates = fetch_ecb_historical_rates()
        
        if not historical_rates:
            print("❌ Failed to fetch exchange rates from ECB")
            return
        
        # Step 4: Filter to only new dates
        rates_to_add = filter_new_dates(historical_rates, first_transaction_date, last_stored_date)
        
        if not rates_to_add:
            print("✅ All exchange rates are up to date!")
            return
        
        print(f"📦 Found {len(rates_to_add)} new dates to add")
        print()
        
        # Step 5: Store rates for each date
        print("💾 Storing exchange rates...")
        total_stored = 0
        dates_processed = 0
        
        # Sort dates for cleaner progress output
        sorted_dates = sorted(rates_to_add.keys())
        
        for rates_date in sorted_dates:
            eur_rates = rates_to_add[rates_date]
            stored_count = store_rates_for_date(db, rates_date, eur_rates, currencies_needed)
            total_stored += stored_count
            dates_processed += 1
            
            # Show progress every 50 dates or at the end
            if dates_processed % 50 == 0 or dates_processed == len(sorted_dates):
                print(f"   📍 Processed {dates_processed}/{len(sorted_dates)} dates...")
        
        # Commit all changes
        db.commit()
        
        print()
        print("=" * 60)
        print(f"✅ Successfully stored {total_stored} exchange rates across {dates_processed} dates!")
        print(f"📊 Average of {total_stored / dates_processed:.1f} currencies per date")
        
        # Show summary of missing currencies if any
        sample_date = sorted_dates[-1]  # Check most recent date
        sample_eur_rates = rates_to_add[sample_date]
        missing_currencies = []
        
        for currency in currencies_needed:
            if currency not in sample_eur_rates and currency != 'EUR':
                missing_currencies.append(currency)
        
        if missing_currencies:
            print()
            print(f"⚠️  Warning: The following currencies are not available in ECB data:")
            print(f"   {', '.join(missing_currencies)}")
            print(f"   These currencies will not have exchange rates.")
        
    except Exception as e:
        print(f"❌ Error updating exchange rates: {e}")
        import traceback
        traceback.print_exc()
        db.rollback()
    finally:
        db.close()


def get_latest_rates(db: Session) -> Dict[str, float]:
    """
    Get the most recent exchange rates for all currencies.
    Returns a dictionary with currency codes as keys and rates as values.
    """
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