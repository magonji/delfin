"""
Financisto data-model constants and the structural converters that bridge
Financisto's representation and Delfin's.

The two genuinely different structures are:

* **Categories** — Financisto uses a *nested set* (``left``/``right`` bounds,
  unlimited depth). Delfin uses a single ``parent`` string (exactly two
  levels). ``nested_set_to_parent_map`` flattens the former; ``build_nested_set``
  reconstructs it for export.

* **Amounts / dates** — Financisto stores integer minor units and epoch
  milliseconds; Delfin stores float major units and ``datetime``.
"""
from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

# -- Financisto table names (as they appear in $ENTITY blocks) --------------
T_ACCOUNT = "account"
T_CATEGORY = "category"
T_CURRENCY = "currency"
T_TRANSACTIONS = "transactions"
T_PAYEE = "payee"
T_PROJECT = "project"
T_LOCATIONS = "locations"
T_ATTRIBUTES = "attributes"
T_CATEGORY_ATTRIBUTE = "category_attribute"
T_TRANSACTION_ATTRIBUTE = "transaction_attribute"
T_BUDGET = "budget"
T_EXCHANGE_RATE = "currency_exchange_rate"
T_CCARD_CLOSING_DATE = "ccard_closing_date"
T_SMS_TEMPLATES = "sms_template"

# Tables that have no representation in Delfin's model. Imported rows of these
# tables are counted and reported, never silently applied.
UNSUPPORTED_TABLES = {
    T_ATTRIBUTES,
    T_CATEGORY_ATTRIBUTE,
    T_TRANSACTION_ATTRIBUTE,
    T_BUDGET,
    T_EXCHANGE_RATE,
    T_CCARD_CLOSING_DATE,
    T_SMS_TEMPLATES,
}

# -- Category type --------------------------------------------------------
# Financisto category.type: 0 = expense, 1 = income.
def category_type_from_financisto(raw: str | None) -> str:
    return "income" if str(raw) == "1" else "expense"


def category_type_to_financisto(delfin_type: str | None) -> str:
    return "1" if (delfin_type or "").lower() == "income" else "0"


# -- Account type (free-text in Delfin; enum-ish in Financisto) -----------
# Imported as-is into Delfin's free-text ``type`` field; on export we pass a
# recognised Financisto type, defaulting to CASH for anything unknown.
KNOWN_FINANCISTO_ACCOUNT_TYPES = {
    "CASH", "BANK", "DEBIT_CARD", "CREDIT_CARD", "ASSET",
    "LIABILITY", "ELECTRONIC", "SAVINGS", "OTHER", "PAYPAL",
}


def account_type_to_financisto(delfin_type: str | None) -> str:
    t = (delfin_type or "").strip().upper().replace(" ", "_")
    return t if t in KNOWN_FINANCISTO_ACCOUNT_TYPES else "CASH"


# -- Time / money ---------------------------------------------------------
def epoch_ms_to_datetime(raw: str | int | None) -> datetime | None:
    """Financisto datetimes are epoch milliseconds (device-local)."""
    if raw is None or raw == "":
        return None
    try:
        ms = int(raw)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0)


def datetime_to_epoch_ms(dt: datetime | None) -> int:
    if dt is None:
        return 0
    return int(dt.timestamp() * 1000)


def minor_to_major(raw: str | int | None, decimals: int = 2) -> float:
    """Convert integer minor units (cents) to a signed major-unit float."""
    if raw is None or raw == "":
        return 0.0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return 0.0
    return value / (10 ** decimals)


def major_to_minor(amount: float | None, decimals: int = 2) -> int:
    if amount is None:
        return 0
    return int(round(float(amount) * (10 ** decimals)))


# -- Category hierarchy: nested set <-> (name, parent) --------------------
def nested_set_to_parent_map(
    categories: List[dict],
) -> Tuple[Dict[str, dict], int]:
    """
    Given Financisto category rows (each a dict with ``_id``, ``title``,
    ``left``, ``right``), compute, for each real category, its immediate
    parent title and depth.

    Returns ``(by_id, max_depth)`` where ``by_id[_id]`` is::

        {"title", "parent_title" (or None), "depth", "type"}

    Depth 1 == top level. Depth > 2 cannot be represented in Delfin's two-level
    model and is flagged by the caller (parent is set to the *immediate*
    parent's title, so the deepest two levels survive).

    The synthetic root category (``_id <= 0``) is skipped.
    """
    def as_int(v, default=0):
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    rows = []
    for c in categories:
        cid = as_int(c.get("_id"), 0)
        if cid <= 0:
            continue  # _id 0 == "no category" root container
        rows.append({
            "id": cid,
            "title": (c.get("title") or "").strip(),
            "left": as_int(c.get("left")),
            "right": as_int(c.get("right")),
            "type": c.get("type"),
        })

    # Sort by left bound; a stack of currently-open ancestors gives the parent.
    rows.sort(key=lambda r: r["left"])
    by_id: Dict[str, dict] = {}
    stack: List[dict] = []
    max_depth = 0

    for r in rows:
        # Pop ancestors that have closed before this node starts.
        while stack and stack[-1]["right"] < r["left"]:
            stack.pop()
        parent = stack[-1] if stack else None
        depth = len(stack) + 1
        max_depth = max(max_depth, depth)
        by_id[str(r["id"])] = {
            "title": r["title"],
            "parent_title": parent["title"] if parent else None,
            "depth": depth,
            "type": r["type"],
        }
        stack.append(r)

    return by_id, max_depth


def build_nested_set(tree: List[dict]) -> List[dict]:
    """
    Assign Financisto ``left``/``right`` bounds to a category forest for export.

    ``tree`` is a list of root nodes, each ``{"id", "title", "type", "children": [...]}``.
    Returns a flat list of ``{"_id", "title", "left", "right", "type"}`` dicts,
    wrapped in a synthetic ``_id 0`` root that Financisto expects.
    """
    flat: List[dict] = []
    counter = {"n": 0}

    def visit(node: dict) -> None:
        counter["n"] += 1
        left = counter["n"]
        for child in node.get("children", []):
            visit(child)
        counter["n"] += 1
        right = counter["n"]
        flat.append({
            "_id": node["id"],
            "title": node["title"],
            "left": left,
            "right": right,
            "type": category_type_to_financisto(node.get("type")),
        })

    # Synthetic root wraps everything (id 0).
    counter["n"] += 1  # root left = 1
    root_left = counter["n"]
    for root in tree:
        visit(root)
    counter["n"] += 1
    root_right = counter["n"]
    flat.append({
        "_id": 0,
        "title": "No category",
        "left": root_left - 1,  # root spans from 0
        "right": root_right,
        "type": "0",
    })
    # Normalise the synthetic root to start at 0 like Financisto's sample.
    flat[-1]["left"] = 0
    return flat
