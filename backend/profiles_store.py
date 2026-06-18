"""
Persistent CSV import profiles (column mappings per bank).

Stored as JSON in the data volume rather than in the database — these are user
config, not financial data, so keeping them out of the (encrypted) DB avoids
interfering with the backup change-detection (which inspects the database) and
mirrors how ``settings_store.py`` works. Survives restarts via the same
``data/`` volume as the live DB.

A profile describes how to read one bank's CSV export::

    {
      "name": "BBVA",
      "signature": ["fecha", "concepto", "importe"],  # header row, lowercased
      "delimiter": ";",
      "encoding": "auto",          # auto | utf-8 | windows-1252 | iso-8859-1
      "decimal": ",",
      "thousands": ".",
      "dateFormat": "DD/MM/YYYY",
      "columns": {
        "date": "Fecha",
        "description": ["Concepto"],
        "amountMode": "single",    # single | debitcredit
        "amount": "Importe",
        "debit": None, "credit": None,
        "amountSign": "asis",      # asis | flip
        "note": None
      }
    }
"""
import json
import os
import threading
from typing import Dict, List, Optional

PROFILES_PATH = "./data/import_profiles.json"

_lock = threading.Lock()


def _read() -> List[Dict]:
    try:
        with open(PROFILES_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [p for p in data if isinstance(p, dict) and p.get("name")]


def _write(profiles: List[Dict]) -> None:
    os.makedirs(os.path.dirname(PROFILES_PATH), exist_ok=True)
    tmp = PROFILES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(profiles, f, indent=2, ensure_ascii=False)
    os.replace(tmp, PROFILES_PATH)


def list_profiles() -> List[Dict]:
    with _lock:
        return _read()


def _validate(profile: Dict) -> None:
    """Raise ValueError if the profile is missing the fields needed to parse."""
    if not isinstance(profile, dict):
        raise ValueError("profile must be an object")
    if not (profile.get("name") or "").strip():
        raise ValueError("profile name is required")
    columns = profile.get("columns")
    if not isinstance(columns, dict):
        raise ValueError("profile.columns is required")
    if not columns.get("date"):
        raise ValueError("a date column must be mapped")
    mode = columns.get("amountMode", "single")
    if mode == "debitcredit":
        if not (columns.get("debit") or columns.get("credit")):
            raise ValueError("a debit and/or credit column must be mapped")
    else:
        if not columns.get("amount"):
            raise ValueError("an amount column must be mapped")


def save_profile(profile: Dict) -> List[Dict]:
    """Validate and upsert a profile by name (case-insensitive). Returns the full list."""
    _validate(profile)
    name = profile["name"].strip()
    profile["name"] = name
    with _lock:
        profiles = _read()
        profiles = [p for p in profiles if p.get("name", "").lower() != name.lower()]
        profiles.append(profile)
        _write(profiles)
        return profiles


def delete_profile(name: str) -> List[Dict]:
    """Remove a profile by name (case-insensitive). Returns the remaining list."""
    with _lock:
        profiles = _read()
        profiles = [p for p in profiles if p.get("name", "").lower() != (name or "").lower()]
        _write(profiles)
        return profiles
