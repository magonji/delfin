"""
Canonical list of supported currencies.

These are exactly the currencies published in the European Central Bank
``eurofxref`` reference-rate feed (see ``update_exchange_rates.py``), which is
the only source from which Delfin can derive historical exchange rates. GBP is
added explicitly because it is the internal base currency (rate 1.0) and is not
itself quoted in the EUR-based feed.

This module is the single source of truth: the ``/api/currencies`` endpoint, the
display-currency setting validation, and every currency dropdown in the
frontend are all driven from here, so the UI can never offer a currency we
cannot price.
"""
from typing import Dict, List

# code -> human-readable name. Ordered roughly by how commonly the app's users
# are expected to need them; the frontend keeps this order in its dropdowns.
SUPPORTED_CURRENCIES: Dict[str, str] = {
    "GBP": "Pound Sterling",
    "EUR": "Euro",
    "USD": "US Dollar",
    "CHF": "Swiss Franc",
    "JPY": "Japanese Yen",
    "AUD": "Australian Dollar",
    "CAD": "Canadian Dollar",
    "NZD": "New Zealand Dollar",
    "CNY": "Chinese Yuan Renminbi",
    "HKD": "Hong Kong Dollar",
    "SGD": "Singapore Dollar",
    "INR": "Indian Rupee",
    "KRW": "South Korean Won",
    "IDR": "Indonesian Rupiah",
    "MYR": "Malaysian Ringgit",
    "PHP": "Philippine Peso",
    "THB": "Thai Baht",
    "ILS": "Israeli Shekel",
    "TRY": "Turkish Lira",
    "ZAR": "South African Rand",
    "BRL": "Brazilian Real",
    "MXN": "Mexican Peso",
    "SEK": "Swedish Krona",
    "NOK": "Norwegian Krone",
    "DKK": "Danish Krone",
    "ISK": "Icelandic Krona",
    "PLN": "Polish Zloty",
    "CZK": "Czech Koruna",
    "HUF": "Hungarian Forint",
    "RON": "Romanian Leu",
    "BGN": "Bulgarian Lev",
}


def is_supported(code: str) -> bool:
    """True if ``code`` is a currency we can price."""
    return code in SUPPORTED_CURRENCIES


def currency_options() -> List[Dict[str, str]]:
    """List of ``{"code", "name"}`` in display order, for API / dropdowns."""
    return [{"code": code, "name": name} for code, name in SUPPORTED_CURRENCIES.items()]
