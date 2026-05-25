"""
Delfin -> Financisto export.

Produces either the native ``.backup`` (gzipped entity dump that Financisto can
restore directly) or the Financisto CSV export layout.

The two structural inversions of the importer:
    * Delfin's two-transaction transfers (paired by "Transfer In"/"Transfer Out"
      locations) are collapsed back into a single Financisto transfer row.
    * Delfin's (parent, name) categories are rebuilt into a Financisto nested set.

Delfin-only data (its own budgets, recurring/planned expenses and the
GBP-based ECB exchange rates) has no Financisto target and is not exported.
``export_notes`` lists exactly what is omitted so the UI can be transparent.
"""
from __future__ import annotations

import csv
import io
from typing import Dict, List, Tuple

from sqlalchemy.orm import Session

from backend import models
from backend.integrations.financisto import backup_format, model as fz
from backend.integrations.financisto.importer import TRANSFER_IN, TRANSFER_OUT

# Minimal symbol table; unknown currencies fall back to their code.
_SYMBOLS = {"GBP": "£", "EUR": "€", "USD": "$", "JPY": "¥"}


def export_notes() -> List[str]:
    """Human-readable list of what a Financisto export cannot carry."""
    return [
        "Delfin budgets, recurring expenses and planned expenses are not part "
        "of the Financisto format and are not included.",
        "Exchange rates are not exported (Financisto recomputes its own; Delfin "
        "refreshes ECB rates automatically).",
        "Running balances are recomputed by Financisto on restore.",
    ]


# ---------------------------------------------------------------------------
# Native .backup
# ---------------------------------------------------------------------------
def export_backup(db: Session, gzip_output: bool = True) -> bytes:
    entities: List[backup_format.Entity] = []

    # -- currencies ---------------------------------------------------------
    codes = _distinct_currencies(db)
    base = _base_currency(db, codes)
    currency_id: Dict[str, int] = {}
    for i, code in enumerate(codes, start=1):
        currency_id[code] = i
        entities.append((fz.T_CURRENCY, {
            "_id": i,
            "name": code,
            "title": code,
            "symbol": _SYMBOLS.get(code, code),
            "is_default": "1" if code == base else "0",
            "decimals": "2",
            "decimal_separator": "'.'",
            "group_separator": "''",
            "symbol_format": "RS",
        }))

    # -- accounts -----------------------------------------------------------
    account_fin_id: Dict[int, int] = {}
    for i, acc in enumerate(db.query(models.Account).order_by(models.Account.id).all(), start=1):
        account_fin_id[acc.id] = i
        entities.append((fz.T_ACCOUNT, {
            "_id": i,
            "title": acc.name,
            "creation_date": fz.datetime_to_epoch_ms(acc.created_at),
            "currency_id": currency_id.get(acc.currency or base, currency_id.get(base, 1)),
            "total_amount": fz.major_to_minor(acc.current_balance),
            "type": fz.account_type_to_financisto(acc.type),
            "sort_order": i,
            "is_active": 1 if (acc.is_active is None or acc.is_active) else 0,
            "is_include_into_totals": 1,
            "last_category_id": 0,
            "last_account_id": 0,
            "total_limit": 0,
            "closing_day": 0,
            "payment_day": 0,
            "last_transaction_date": 0,
        }))

    # -- categories (nested set) -------------------------------------------
    category_fin_id, category_entities = _build_category_entities(db)
    entities.extend(category_entities)

    # -- payees -------------------------------------------------------------
    payee_fin_id: Dict[int, int] = {}
    for i, p in enumerate(db.query(models.Payee).order_by(models.Payee.id).all(), start=1):
        payee_fin_id[p.id] = i
        entities.append((fz.T_PAYEE, {"_id": i, "title": p.name}))

    # -- projects -----------------------------------------------------------
    project_fin_id: Dict[int, int] = {}
    for i, pr in enumerate(db.query(models.Project).order_by(models.Project.id).all(), start=1):
        project_fin_id[pr.id] = i
        entities.append((fz.T_PROJECT, {"_id": i, "title": pr.name, "is_active": 1}))

    # -- locations (excluding transfer markers) ----------------------------
    location_fin_id: Dict[int, int] = {}
    i = 0
    for loc in db.query(models.Location).order_by(models.Location.id).all():
        if loc.name in (TRANSFER_IN, TRANSFER_OUT):
            continue
        i += 1
        location_fin_id[loc.id] = i
        entities.append((fz.T_LOCATIONS, {
            "_id": i, "name": loc.name, "title": loc.name,
            "datetime": 0, "is_payee": 0,
        }))

    # -- transactions (with transfer reconstruction) -----------------------
    entities.extend(_build_transaction_entities(
        db, account_fin_id, category_fin_id, payee_fin_id,
        project_fin_id, location_fin_id,
    ))

    return backup_format.serialize(entities, gzip_output=gzip_output)


def _distinct_currencies(db: Session) -> List[str]:
    codes = set()
    for (c,) in db.query(models.Account.currency).distinct().all():
        if c:
            codes.add(c)
    for (c,) in db.query(models.Transaction.currency).distinct().all():
        if c:
            codes.add(c)
    if not codes:
        codes.add("GBP")
    return sorted(codes)


def _base_currency(db: Session, codes: List[str]) -> str:
    from backend.helpers import get_base_currency
    base = get_base_currency(db)
    return base if base in codes else codes[0]


def _build_category_entities(db: Session) -> Tuple[Dict[int, int], List[backup_format.Entity]]:
    """
    Build a Financisto nested-set category dump from Delfin's (name, parent)
    rows. Returns (delfin_category_id -> financisto_id, entities).
    """
    cats = db.query(models.Category).order_by(models.Category.id).all()

    next_id = {"v": 0}

    def new_id() -> int:
        next_id["v"] += 1
        return next_id["v"]

    # Parent nodes keyed by parent title.
    parent_nodes: Dict[str, dict] = {}
    roots: List[dict] = []
    delfin_to_fin: Dict[int, int] = {}

    def ensure_parent(title: str) -> dict:
        node = parent_nodes.get(title)
        if node is None:
            node = {"id": new_id(), "title": title, "type": "expense", "children": []}
            parent_nodes[title] = node
            roots.append(node)
        return node

    # First pass: top-level categories (parent is None) become root nodes and
    # also serve as parent containers if other rows reference them by name.
    for c in cats:
        if not c.parent:
            node = parent_nodes.get(c.name)
            if node is None:
                node = {"id": new_id(), "title": c.name, "type": c.type or "expense", "children": []}
                parent_nodes[c.name] = node
                roots.append(node)
            else:
                node["type"] = c.type or node["type"]
            delfin_to_fin[c.id] = node["id"]

    # Second pass: child categories.
    for c in cats:
        if c.parent:
            parent = ensure_parent(c.parent)
            child = {"id": new_id(), "title": c.name, "type": c.type or "expense", "children": []}
            parent["children"].append(child)
            delfin_to_fin[c.id] = child["id"]

    flat = fz.build_nested_set(roots)
    entities: List[backup_format.Entity] = []
    for row in flat:
        entities.append((fz.T_CATEGORY, {
            "_id": row["_id"],
            "title": row["title"],
            "left": row["left"],
            "right": row["right"],
            "sort_order": 0,
            "type": row["type"],
            "last_location_id": 0,
            "last_project_id": 0,
        }))
    return delfin_to_fin, entities


def _build_transaction_entities(
    db, account_fin_id, category_fin_id, payee_fin_id, project_fin_id, location_fin_id,
) -> List[backup_format.Entity]:
    txns = db.query(models.Transaction).order_by(
        models.Transaction.date.asc(), models.Transaction.id.asc()
    ).all()

    out_loc = db.query(models.Location).filter(models.Location.name == TRANSFER_OUT).first()
    in_loc = db.query(models.Location).filter(models.Location.name == TRANSFER_IN).first()
    out_id = out_loc.id if out_loc else None
    in_id = in_loc.id if in_loc else None

    # Index incoming legs by day for pairing.
    transfers_in_by_day: Dict[str, List[models.Transaction]] = {}
    regular: List[models.Transaction] = []
    transfers_out: List[models.Transaction] = []
    for t in txns:
        if in_id and t.location_id == in_id:
            transfers_in_by_day.setdefault(_day(t.date), []).append(t)
        elif out_id and t.location_id == out_id:
            transfers_out.append(t)
        else:
            regular.append(t)

    entities: List[backup_format.Entity] = []
    fin_id = 0

    used_in_ids = set()
    # Match each outgoing leg with an incoming leg (same day, prefer same |amount|).
    for t_out in transfers_out:
        candidates = [c for c in transfers_in_by_day.get(_day(t_out.date), [])
                      if c.id not in used_in_ids and c.account_id != t_out.account_id]
        match = next((c for c in candidates if abs(c.amount) == abs(t_out.amount)), None)
        if match is None and candidates:
            match = candidates[0]
        fin_id += 1
        if match is not None:
            used_in_ids.add(match.id)
            entities.append((fz.T_TRANSACTIONS, _transfer_entity(
                fin_id, t_out, match, account_fin_id)))
        else:
            # Orphan outgoing leg -> regular outflow.
            entities.append((fz.T_TRANSACTIONS, _regular_entity(
                fin_id, t_out, account_fin_id, category_fin_id, payee_fin_id,
                project_fin_id, location_fin_id, skip_location=True)))

    # Any incoming legs that never matched -> regular inflow.
    for legs in transfers_in_by_day.values():
        for t_in in legs:
            if t_in.id in used_in_ids:
                continue
            fin_id += 1
            entities.append((fz.T_TRANSACTIONS, _regular_entity(
                fin_id, t_in, account_fin_id, category_fin_id, payee_fin_id,
                project_fin_id, location_fin_id, skip_location=True)))

    for t in regular:
        fin_id += 1
        entities.append((fz.T_TRANSACTIONS, _regular_entity(
            fin_id, t, account_fin_id, category_fin_id, payee_fin_id,
            project_fin_id, location_fin_id)))

    return entities


def _transfer_entity(fin_id, t_out, t_in, account_fin_id) -> dict:
    return {
        "_id": fin_id,
        "from_account_id": account_fin_id.get(t_out.account_id, 0),
        "to_account_id": account_fin_id.get(t_in.account_id, 0),
        "category_id": 0,
        "project_id": 0,
        "location_id": 0,
        "payee_id": 0,
        "note": t_out.note or t_in.note or "",
        "from_amount": fz.major_to_minor(t_out.amount),
        "to_amount": fz.major_to_minor(t_in.amount),
        "datetime": fz.datetime_to_epoch_ms(t_out.date),
        "is_template": 0,
        "status": "UR",
        "is_ccard_payment": 0,
        "parent_id": 0,
    }


def _regular_entity(fin_id, t, account_fin_id, category_fin_id, payee_fin_id,
                    project_fin_id, location_fin_id, skip_location=False) -> dict:
    return {
        "_id": fin_id,
        "from_account_id": account_fin_id.get(t.account_id, 0),
        "to_account_id": 0,
        "category_id": category_fin_id.get(t.category_id, 0) if t.category_id else 0,
        "project_id": project_fin_id.get(t.project_id, 0) if t.project_id else 0,
        "location_id": 0 if skip_location else (location_fin_id.get(t.location_id, 0) if t.location_id else 0),
        "payee_id": payee_fin_id.get(t.payee_id, 0) if t.payee_id else 0,
        "note": t.note or "",
        "from_amount": fz.major_to_minor(t.amount),
        "to_amount": 0,
        "datetime": fz.datetime_to_epoch_ms(t.date),
        "is_template": 0,
        "status": "UR",
        "is_ccard_payment": 0,
        "parent_id": 0,
    }


def _day(dt) -> str:
    return dt.strftime("%Y-%m-%d") if dt else ""


# ---------------------------------------------------------------------------
# CSV export (Financisto layout)
# ---------------------------------------------------------------------------
def export_csv(db: Session) -> bytes:
    from backend.integrations.financisto.importer import CSV_HEADERS

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_HEADERS)

    # Pre-resolve lookups for names.
    acc_name = {a.id: a.name for a in db.query(models.Account).all()}
    acc_currency = {a.id: a.currency for a in db.query(models.Account).all()}
    cat = {c.id: (c.name, c.parent) for c in db.query(models.Category).all()}
    payee = {p.id: p.name for p in db.query(models.Payee).all()}
    loc = {l.id: l.name for l in db.query(models.Location).all()}
    proj = {p.id: p.name for p in db.query(models.Project).all()}

    txns = db.query(models.Transaction).order_by(
        models.Transaction.date.asc(), models.Transaction.id.asc()
    ).all()

    for t in txns:
        dt = t.date
        date_str = dt.strftime("%Y-%m-%d") if dt else "~"
        time_str = dt.strftime("%H:%M:%S") if dt else ""
        cat_name, cat_parent = cat.get(t.category_id, (None, None))
        location_name = loc.get(t.location_id)
        payee_name = payee.get(t.payee_id)
        # Map Delfin transfer legs back to Financisto's CSV transfer payee marker.
        if location_name in (TRANSFER_IN, TRANSFER_OUT):
            payee_name = location_name
            location_name = None
        writer.writerow([
            date_str,
            time_str,
            acc_name.get(t.account_id, ""),
            f"{t.amount:.2f}",
            t.currency or acc_currency.get(t.account_id, ""),
            "",  # original amount
            "",  # original currency
            cat_name or "",
            cat_parent or "",
            payee_name or "",
            location_name or "",
            proj.get(t.project_id, "") or "",
            t.note or "",
        ])

    return buf.getvalue().encode("utf-8")
