"""
ECB exchange rate fetcher and updater.
Fetches historical rates from the European Central Bank and stores them with GBP as base.
"""
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from backend.database import SessionLocal
from backend.models import ExchangeRate, Transaction


def get_first_transaction_date(db: Session) -> date:
    """Get the date of the first transaction, or today if none exist."""
    first = db.query(Transaction).order_by(Transaction.date.asc()).first()
    if first:
        return first.date.date() if isinstance(first.date, datetime) else first.date
    return date.today()


def get_last_exchange_rate_date(db: Session) -> Optional[date]:
    """Get the most recent date with exchange rates, or None if none exist."""
    last = db.query(func.max(ExchangeRate.date)).scalar()
    if last:
        return last.date() if isinstance(last, datetime) else last
    return None


def get_currencies_in_use(db: Session) -> List[str]:
    """Get all unique currencies used in transactions (excluding GBP)."""
    currencies = db.query(Transaction.currency).distinct().all()
    return [c[0] for c in currencies if c[0] and c[0] != 'GBP']


def fetch_ecb_historical_rates() -> Optional[Dict[date, Dict[str, float]]]:
    """
    Fetch historical exchange rates from ECB XML feed.
    Returns dictionary: {date: {currency: rate_vs_eur}}
    """
    try:
        url = 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.xml'
        print("Fetching historical rates from ECB...")
        
        response = requests.get(url, timeout=30)
        if response.status_code != 200:
            print(f"ECB request failed: {response.status_code}")
            return None
        
        root = ET.fromstring(response.content)
        ns = {
            'gesmes': 'http://www.gesmes.org/xml/2002-09-01',
            'ecb': 'http://www.ecb.int/vocabulary/2002-08-01/eurofxref'
        }
        
        historical_rates = {}
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
        
        print(f"Fetched {len(historical_rates)} days of historical rates")
        return historical_rates
        
    except Exception as e:
        print(f"Error fetching ECB rates: {e}")
        return None


def calculate_gbp_rate(eur_rates: Dict[str, float], currency: str) -> Optional[float]:
    """
    Calculate exchange rate from GBP to target currency.
    ECB provides EUR as base, so we convert via GBP.
    """
    if currency == 'GBP':
        return 1.0
    
    if currency == 'EUR':
        gbp_eur_rate = eur_rates.get('GBP')
        return 1.0 / gbp_eur_rate if gbp_eur_rate else None
    
    currency_eur_rate = eur_rates.get(currency)
    gbp_eur_rate = eur_rates.get('GBP')
    
    if currency_eur_rate and gbp_eur_rate:
        return currency_eur_rate / gbp_eur_rate
    return None


def store_rates_for_date(db: Session, rates_date: date, eur_rates: Dict[str, float], 
                         currencies_needed: List[str]) -> int:
    """Store exchange rates for a specific date. Returns count of rates stored."""
    stored_count = 0
    
    for currency in currencies_needed:
        gbp_rate = calculate_gbp_rate(eur_rates, currency)
        if gbp_rate is None:
            continue
        
        existing = db.query(ExchangeRate).filter(
            ExchangeRate.currency == currency,
            ExchangeRate.date == rates_date
        ).first()
        
        if existing:
            existing.rate = gbp_rate
            existing.created_at = datetime.utcnow()
        else:
            db.add(ExchangeRate(
                currency=currency,
                rate=gbp_rate,
                date=datetime.combine(rates_date, datetime.min.time()),
                created_at=datetime.utcnow()
            ))
        
        stored_count += 1
    
    return stored_count


def update_exchange_rates():
    """
    Main function to fetch and store exchange rates.
    Only fetches rates from first transaction date onwards.
    Performs incremental updates (only adds new dates).
    """
    print("Starting exchange rate update...")
    print("=" * 50)
    
    db = SessionLocal()
    
    try:
        first_tx_date = get_first_transaction_date(db)
        last_stored_date = get_last_exchange_rate_date(db)
        
        print(f"First transaction: {first_tx_date}")
        if last_stored_date:
            print(f"Last stored rate: {last_stored_date}")
        
        currencies_needed = get_currencies_in_use(db)
        if not currencies_needed:
            print("No non-GBP currencies found. Nothing to update.")
            return
        
        print(f"Currencies needed: {', '.join(currencies_needed)}")
        
        historical_rates = fetch_ecb_historical_rates()
        if not historical_rates:
            print("Failed to fetch rates from ECB")
            return
        
        # Filter to new dates only
        rates_to_add = {
            d: rates for d, rates in historical_rates.items()
            if d >= first_tx_date and (not last_stored_date or d > last_stored_date)
        }
        
        if not rates_to_add:
            print("All exchange rates are up to date!")
            return
        
        print(f"Adding {len(rates_to_add)} new dates...")
        
        total_stored = 0
        for rates_date in sorted(rates_to_add.keys()):
            stored = store_rates_for_date(db, rates_date, rates_to_add[rates_date], currencies_needed)
            total_stored += stored
        
        db.commit()
        print("=" * 50)
        print(f"Stored {total_stored} exchange rates across {len(rates_to_add)} dates")
        
    except Exception as e:
        print(f"Error: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    update_exchange_rates()