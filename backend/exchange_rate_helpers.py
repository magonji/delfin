"""
Helper functions for working with historical exchange rates.

This module provides utilities to fetch exchange rates for specific dates
and convert amounts using historical rates.
"""

from datetime import date, datetime, timedelta
from typing import Dict, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from backend.models import ExchangeRate


def get_rate_for_date(db: Session, currency: str, target_date: date) -> Optional[float]:
    """
    Get the exchange rate for a specific currency on a specific date.
    
    If the exact date doesn't exist (e.g., weekend), it looks backward 
    up to 7 days to find the most recent available rate.
    
    Args:
        db: Database session
        currency: Currency code (e.g., "USD", "EUR")
        target_date: The date for which to get the rate
    
    Returns:
        Exchange rate (GBP as base), or None if not found
    """
    # GBP is always 1.0
    if currency == 'GBP':
        return 1.0
    
    # Convert datetime to date if necessary
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    
    # Try to find exact date first
    rate = db.query(ExchangeRate).filter(
        ExchangeRate.currency == currency,
        func.date(ExchangeRate.date) == target_date
    ).first()
    
    if rate:
        return rate.rate
    
    # If not found, look backward up to 7 days (for weekends/holidays)
    for days_back in range(1, 8):
        check_date = target_date - timedelta(days=days_back)
        rate = db.query(ExchangeRate).filter(
            ExchangeRate.currency == currency,
            func.date(ExchangeRate.date) == check_date
        ).first()
        
        if rate:
            return rate.rate
    
    # If still not found, return None
    return None


def get_rates_for_date(db: Session, target_date: date) -> Dict[str, float]:
    """
    Get all available exchange rates for a specific date.
    
    Args:
        db: Database session
        target_date: The date for which to get rates
    
    Returns:
        Dictionary mapping currency codes to rates (GBP as base)
    """
    # Convert datetime to date if necessary
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    
    # Get rates for exact date
    rates = db.query(ExchangeRate).filter(
        func.date(ExchangeRate.date) == target_date
    ).all()
    
    rates_dict = {rate.currency: rate.rate for rate in rates}
    
    # If no rates found for this date, look backward up to 7 days
    if not rates_dict:
        for days_back in range(1, 8):
            check_date = target_date - timedelta(days=days_back)
            rates = db.query(ExchangeRate).filter(
                func.date(ExchangeRate.date) == check_date
            ).all()
            
            if rates:
                rates_dict = {rate.currency: rate.rate for rate in rates}
                break
    
    # Always include GBP
    rates_dict['GBP'] = 1.0
    
    return rates_dict


def get_rates_bulk(db: Session, currencies: list, date_from: date, date_to: date) -> Dict[date, Dict[str, float]]:
    """
    Get exchange rates for multiple currencies across a date range.
    More efficient than calling get_rates_for_date multiple times.
    
    Args:
        db: Database session
        currencies: List of currency codes
        date_from: Start date
        date_to: End date
    
    Returns:
        Nested dictionary: {date: {currency: rate}}
    """
    # Convert datetime to date if necessary
    if isinstance(date_from, datetime):
        date_from = date_from.date()
    if isinstance(date_to, datetime):
        date_to = date_to.date()
    
    # Query all rates in the date range for specified currencies
    rates = db.query(ExchangeRate).filter(
        and_(
            ExchangeRate.currency.in_(currencies),
            func.date(ExchangeRate.date) >= date_from,
            func.date(ExchangeRate.date) <= date_to
        )
    ).order_by(ExchangeRate.date).all()
    
    # Organize by date
    rates_by_date = {}
    for rate in rates:
        rate_date = rate.date.date() if isinstance(rate.date, datetime) else rate.date
        if rate_date not in rates_by_date:
            rates_by_date[rate_date] = {'GBP': 1.0}
        rates_by_date[rate_date][rate.currency] = rate.rate
    
    # Fill in missing dates by using the previous available rate
    all_dates = []
    current_date = date_from
    while current_date <= date_to:
        all_dates.append(current_date)
        current_date += timedelta(days=1)
    
    complete_rates = {}
    last_rates = {'GBP': 1.0}
    
    for current_date in all_dates:
        if current_date in rates_by_date:
            # Update with new rates for this date
            last_rates.update(rates_by_date[current_date])
        # Use last known rates (carry forward)
        complete_rates[current_date] = last_rates.copy()
    
    return complete_rates


def convert_amount(amount: float, from_currency: str, to_currency: str, 
                   rate_from: float, rate_to: float) -> float:
    """
    Convert an amount from one currency to another using exchange rates.
    
    Args:
        amount: Amount to convert
        from_currency: Source currency code
        to_currency: Target currency code
        rate_from: Exchange rate for source currency (GBP base)
        rate_to: Exchange rate for target currency (GBP base)
    
    Returns:
        Converted amount
    
    Example:
        # Convert 100 USD to EUR
        # rate_from (USD) = 1.27 (1 GBP = 1.27 USD)
        # rate_to (EUR) = 1.18 (1 GBP = 1.18 EUR)
        # First convert to GBP: 100 / 1.27 = 78.74 GBP
        # Then convert to EUR: 78.74 * 1.18 = 92.91 EUR
    """
    if from_currency == to_currency:
        return amount
    
    # Convert to GBP first (base currency)
    amount_in_gbp = amount / rate_from if rate_from != 0 else 0
    
    # Then convert to target currency
    amount_in_target = amount_in_gbp * rate_to
    
    return amount_in_target


def get_latest_rates(db: Session) -> Dict[str, float]:
    """
    Get the most recent exchange rates for all currencies.
    This is the old behavior - kept for backwards compatibility.
    
    Returns:
        Dictionary with currency codes as keys and rates as values.
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