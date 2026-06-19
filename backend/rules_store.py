"""
Learned import rules: normalised bank-description -> payee name.

When the user imports a statement and assigns a payee to a row, Delfin remembers
the association so future imports auto-fill the payee (and, through the payee's
stats, its usual category). Re-assigning a different payee overwrites the rule.

Stored as JSON in the data volume — user config, not financial data — mirroring
``settings_store.py`` / ``profiles_store.py`` so it stays out of the encrypted DB
and the backup change-detection. The key is the *normalised* description (dates,
times and digit runs stripped) so "CARD 15/03 MERCADONA" and "CARD 16/03
MERCADONA" map to the same rule.
"""
import json
import os
import threading
from typing import Dict

RULES_PATH = "./data/import_rules.json"

_lock = threading.Lock()


def _read() -> Dict[str, str]:
    try:
        with open(RULES_PATH) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if k and v}


def _write(rules: Dict[str, str]) -> None:
    os.makedirs(os.path.dirname(RULES_PATH), exist_ok=True)
    tmp = RULES_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rules, f, indent=2, ensure_ascii=False)
    os.replace(tmp, RULES_PATH)


def get_rules() -> Dict[str, str]:
    with _lock:
        return _read()


def merge_rules(new_rules: Dict[str, str]) -> Dict[str, str]:
    """Upsert each ``normalised_description -> payee_name`` pair. Returns the full map."""
    if not isinstance(new_rules, dict):
        raise ValueError("rules must be an object of description -> payee")
    with _lock:
        rules = _read()
        for k, v in new_rules.items():
            k = str(k).strip()
            v = str(v).strip()
            if k and v:
                rules[k] = v       # overwrite = the user editing the rule
            elif k and not v:
                rules.pop(k, None)  # empty payee removes the rule
        _write(rules)
        return rules
