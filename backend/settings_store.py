"""
Persistent settings for the daily maintenance job (schedule time + backup
retention).

Stored as JSON in the data volume rather than in the database, so that changing
a setting never interferes with the backup change-detection (which inspects the
database). Survives restarts via the same ``data/`` volume as the live DB.
"""
import json
import os
import threading
from typing import Dict, Optional

SETTINGS_PATH = "./data/maintenance_settings.json"

DEFAULTS: Dict[str, str] = {
    "maintenance_time": "02:28",   # HH:MM, 24h, server local time
    "backup_retention": "1y",      # one of RETENTION_DAYS below
}

# Retention code -> max age in days (None = keep forever).
RETENTION_DAYS: Dict[str, Optional[int]] = {
    "1m": 30,
    "3m": 90,
    "6m": 180,
    "1y": 365,
    "2y": 730,
    "never": None,
}

_lock = threading.Lock()


def _read() -> Dict[str, str]:
    try:
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    merged = dict(DEFAULTS)
    if isinstance(data, dict):
        merged.update({k: v for k, v in data.items() if k in DEFAULTS})
    return merged


def get_settings() -> Dict[str, str]:
    with _lock:
        return _read()


def _valid_time(s: str) -> bool:
    try:
        hh, mm = s.split(":")
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except (ValueError, AttributeError):
        return False


def update_settings(maintenance_time: Optional[str] = None,
                    backup_retention: Optional[str] = None) -> Dict[str, str]:
    """Validate and persist settings. Raises ValueError on bad input."""
    with _lock:
        data = _read()
        if maintenance_time is not None:
            if not _valid_time(maintenance_time):
                raise ValueError("maintenance_time must be HH:MM (24h)")
            hh, mm = maintenance_time.split(":")
            data["maintenance_time"] = f"{int(hh):02d}:{int(mm):02d}"
        if backup_retention is not None:
            if backup_retention not in RETENTION_DAYS:
                raise ValueError(
                    f"backup_retention must be one of {list(RETENTION_DAYS)}"
                )
            data["backup_retention"] = backup_retention
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_PATH)
        return data


def retention_days(code: Optional[str] = None) -> Optional[int]:
    """Max backup age in days for the given (or current) retention code; None = forever."""
    if code is None:
        code = get_settings()["backup_retention"]
    return RETENTION_DAYS.get(code, RETENTION_DAYS["1y"])
