"""
Financisto -> Delfin import.

Pipeline:
    raw bytes  --(parse)-->  Financisto entities / CSV rows
               --(normalize)--> NormalizedData (already in Delfin's shape)
               --(apply)-->     rows written to the Delfin database

``normalize_*`` never touches the database, so it powers the dry-run
("analyze") preview. ``apply_to_database`` is the only step that writes, and it
is wrapped by the API layer in an auto-backup + single transaction.

Design decisions (confirmed with the user):
    * Transfers (one Financisto row) -> two Delfin transactions paired by the
      "Transfer In"/"Transfer Out" location, matching Delfin's native model.
    * Split transactions -> each child becomes its own transaction; the parent
      envelope (category_id = -1) is dropped.
    * Category trees deeper than 2 levels are flattened to (parent, name); the
      flattening is reported, not silent.
    * Anything with no Delfin equivalent (attributes, geo, templates, budgets,
      Financisto exchange rates, SMS templates, card closing dates) is skipped
      and listed in the compatibility report.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from backend import models
from backend.helpers import initialise_all_balances
from backend.integrations.report import CompatibilityReport, Severity
from backend.integrations.financisto import backup_format, model as fz

TRANSFER_IN = "Transfer In"
TRANSFER_OUT = "Transfer Out"


# ---------------------------------------------------------------------------
# Normalised intermediate representation (already shaped like Delfin)
# ---------------------------------------------------------------------------
@dataclass
class NormalizedTxn:
    date: datetime
    amount: float
    currency: str
    note: Optional[str] = None
    account_name: Optional[str] = None
    category_name: Optional[str] = None
    category_parent: Optional[str] = None
    payee_name: Optional[str] = None
    location_name: Optional[str] = None
    project_name: Optional[str] = None


@dataclass
class NormalizedData:
    # name -> {"currency": str, "type": str|None}
    accounts: Dict[str, dict] = field(default_factory=dict)
    # (parent|None, name) -> type
    categories: Dict[Tuple[Optional[str], str], Optional[str]] = field(default_factory=dict)
    payees: set = field(default_factory=set)
    locations: set = field(default_factory=set)
    projects: set = field(default_factory=set)
    transactions: List[NormalizedTxn] = field(default_factory=list)

    def register_category(self, parent: Optional[str], name: str, ctype: Optional[str]) -> None:
        if not name:
            return
        key = (parent or None, name)
        # Keep the first non-empty type we see.
        if key not in self.categories or not self.categories[key]:
            self.categories[key] = ctype

    def summary(self) -> dict:
        transfers = sum(
            1 for t in self.transactions if t.location_name == TRANSFER_OUT
        )
        return {
            "accounts": len(self.accounts),
            "categories": len(self.categories),
            "payees": len(self.payees),
            "locations": len(self.locations),
            "projects": len(self.projects),
            "transactions": len(self.transactions),
            "transfers": transfers,
        }


def _as_int(v, default=0) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Normalisation: native .backup
# ---------------------------------------------------------------------------
def normalize_backup(
    entities: List[backup_format.Entity], report: CompatibilityReport
) -> NormalizedData:
    by_table: Dict[str, List[dict]] = {}
    for table, row in entities:
        by_table.setdefault(table, []).append(row)

    data = NormalizedData()

    # -- currencies: id -> (code, decimals) ---------------------------------
    currencies: Dict[str, Tuple[str, int]] = {}
    for c in by_table.get(fz.T_CURRENCY, []):
        cid = c.get("_id")
        code = (c.get("name") or c.get("title") or "GBP").strip() or "GBP"
        decimals = _as_int(c.get("decimals"), 2)
        if cid is not None:
            currencies[str(cid)] = (code, decimals)

    def currency_of(currency_id) -> Tuple[str, int]:
        return currencies.get(str(currency_id), ("GBP", 2))

    # -- accounts: id -> (name, code, decimals) -----------------------------
    accounts: Dict[str, Tuple[str, str, int]] = {}
    for a in by_table.get(fz.T_ACCOUNT, []):
        if _as_int(a.get("_id"), 0) <= 0:
            continue
        name = (a.get("title") or "").strip()
        if not name:
            continue
        code, decimals = currency_of(a.get("currency_id"))
        atype = a.get("type")
        accounts[str(a.get("_id"))] = (name, code, decimals)
        data.accounts[name] = {"currency": code, "type": atype}

    # -- categories: nested set -> (parent, name) ---------------------------
    cat_by_id, max_depth = fz.nested_set_to_parent_map(by_table.get(fz.T_CATEGORY, []))
    for cid, info in cat_by_id.items():
        name = info["title"]
        parent = info["parent_title"] if info["depth"] >= 2 else None
        ctype = fz.category_type_from_financisto(info["type"])
        data.register_category(parent, name, ctype)
    if max_depth > 2:
        report.add(
            "category_depth",
            Severity.PARTIAL,
            "Deep category hierarchy flattened",
            "Delfin supports a single parent level. Categories nested deeper "
            "than two levels were attached to their immediate parent; "
            "intermediate ancestors are not preserved as a chain.",
        )

    # -- payees / projects / locations --------------------------------------
    payees: Dict[str, str] = {}
    for p in by_table.get(fz.T_PAYEE, []):
        title = (p.get("title") or "").strip()
        if title:
            payees[str(p.get("_id"))] = title
            data.payees.add(title)

    projects: Dict[str, str] = {}
    for p in by_table.get(fz.T_PROJECT, []):
        if _as_int(p.get("_id"), 0) <= 0:
            continue
        title = (p.get("title") or "").strip()
        if title:
            projects[str(p.get("_id"))] = title
            data.projects.add(title)

    locations: Dict[str, str] = {}
    geo_count = 0
    for loc in by_table.get(fz.T_LOCATIONS, []):
        if _as_int(loc.get("_id"), 0) <= 0:
            continue
        name = (loc.get("title") or loc.get("name") or "").strip()
        if not name:
            continue
        locations[str(loc.get("_id"))] = name
        data.locations.add(name)
        if any(_as_int(loc.get(k), 0) != 0 for k in ("latitude", "longitude")):
            geo_count += 1
    if geo_count:
        for _ in range(geo_count):
            report.add(
                "location_geo",
                Severity.PARTIAL,
                "Location coordinates dropped",
                "Delfin locations are names only; latitude/longitude, address "
                "and accuracy from Financisto were not imported.",
            )

    # -- transactions -------------------------------------------------------
    for t in by_table.get(fz.T_TRANSACTIONS, []):
        _normalize_backup_txn(t, data, report, accounts, cat_by_id, payees,
                              projects, locations)

    # -- unsupported tables: count + report --------------------------------
    _report_unsupported(by_table, report)

    return data


def _normalize_backup_txn(t, data, report, accounts, cat_by_id, payees,
                          projects, locations) -> None:
    if _as_int(t.get("_id"), 0) <= 0:
        return

    if _as_int(t.get("is_template"), 0) != 0:
        report.add("templates", Severity.SKIPPED, "Templates / scheduled entries skipped",
                   "Financisto transaction templates and scheduled (recurring) "
                   "definitions are not transactions and were not imported.")
        return

    dt = fz.epoch_ms_to_datetime(t.get("datetime"))
    if dt is None:
        report.add("bad_date", Severity.SKIPPED, "Transaction with no valid date skipped")
        return

    note = (t.get("note") or "").strip() or None
    from_id = str(t.get("from_account_id"))
    to_id = str(t.get("to_account_id"))
    category_id = _as_int(t.get("category_id"), 0)

    # original (foreign) currency info we cannot fully preserve
    if _as_int(t.get("original_currency_id"), 0) > 0:
        report.add("original_amount", Severity.INFO,
                   "Foreign original amounts simplified",
                   "Transactions entered in a different currency keep their "
                   "account-currency amount; the separate original foreign "
                   "amount is not stored separately in Delfin.")

    # --- transfer: one row -> two transactions -----------------------------
    if _as_int(t.get("to_account_id"), 0) != 0:
        from_acc = accounts.get(from_id)
        to_acc = accounts.get(to_id)
        if not from_acc or not to_acc:
            report.add("transfer_acct", Severity.SKIPPED,
                       "Transfer with missing account skipped")
            return
        from_name, from_code, from_dec = from_acc
        to_name, to_code, to_dec = to_acc
        from_amt = fz.minor_to_major(t.get("from_amount"), from_dec)
        to_amt = fz.minor_to_major(t.get("to_amount"), to_dec)
        data.transactions.append(NormalizedTxn(
            date=dt, amount=-abs(from_amt), currency=from_code, note=note,
            account_name=from_name, location_name=TRANSFER_OUT,
        ))
        data.transactions.append(NormalizedTxn(
            date=dt, amount=abs(to_amt), currency=to_code, note=note,
            account_name=to_name, location_name=TRANSFER_IN,
        ))
        data.locations.add(TRANSFER_IN)
        data.locations.add(TRANSFER_OUT)
        report.add("transfers_expanded", Severity.INFO,
                   "Transfers expanded into transaction pairs",
                   "Each Financisto transfer became an outgoing + incoming "
                   "transaction, matching Delfin's transfer model.")
        return

    # --- split parent: drop envelope, children imported on their own -------
    if category_id == -1:
        report.add("split_parent", Severity.INFO,
                   "Split parents collapsed",
                   "Each split's sub-items were imported as individual "
                   "transactions; the parent envelope was dropped.")
        return

    # --- regular transaction (incl. split children) ------------------------
    acc = accounts.get(from_id)
    if not acc:
        report.add("txn_acct", Severity.SKIPPED, "Transaction with unknown account skipped")
        return
    acc_name, acc_code, acc_dec = acc
    amount = fz.minor_to_major(t.get("from_amount"), acc_dec)

    cat = cat_by_id.get(str(category_id)) if category_id > 0 else None
    cat_name = cat["title"] if cat else None
    cat_parent = (cat["parent_title"] if cat and cat["depth"] >= 2 else None)

    payee_name = payees.get(str(t.get("payee_id"))) if _as_int(t.get("payee_id"), 0) > 0 else None
    project_name = projects.get(str(t.get("project_id"))) if _as_int(t.get("project_id"), 0) > 0 else None
    location_name = locations.get(str(t.get("location_id"))) if _as_int(t.get("location_id"), 0) > 0 else None

    data.transactions.append(NormalizedTxn(
        date=dt, amount=amount, currency=acc_code, note=note,
        account_name=acc_name, category_name=cat_name, category_parent=cat_parent,
        payee_name=payee_name, location_name=location_name, project_name=project_name,
    ))


def _report_unsupported(by_table: Dict[str, List[dict]], report: CompatibilityReport) -> None:
    messages = {
        fz.T_TRANSACTION_ATTRIBUTE: ("Custom transaction attributes skipped",
            "Financisto per-transaction custom attributes have no Delfin equivalent."),
        fz.T_ATTRIBUTES: ("Attribute definitions skipped", ""),
        fz.T_CATEGORY_ATTRIBUTE: ("Category-attribute links skipped", ""),
        fz.T_BUDGET: ("Financisto budgets skipped",
            "Delfin uses its own monthly budget model; Financisto budgets were not imported."),
        fz.T_EXCHANGE_RATE: ("Financisto exchange rates skipped",
            "Delfin maintains its own GBP-based ECB rates, updated automatically."),
        fz.T_CCARD_CLOSING_DATE: ("Credit-card closing dates skipped", ""),
        fz.T_SMS_TEMPLATES: ("SMS templates skipped", ""),
    }
    for table, rows in by_table.items():
        if table in fz.UNSUPPORTED_TABLES and rows:
            title, detail = messages.get(table, (f"{table} skipped", ""))
            for _ in rows:
                report.add(f"unsupported_{table}", Severity.SKIPPED, title, detail)


# ---------------------------------------------------------------------------
# Normalisation: CSV export
# ---------------------------------------------------------------------------
CSV_HEADERS = [
    "date", "time", "account", "amount", "currency", "original amount",
    "original currency", "category", "parent", "payee", "location",
    "project", "note",
]


def normalize_csv(raw: bytes, report: CompatibilityReport) -> NormalizedData:
    text = backup_format.decompress(raw).decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    data = NormalizedData()

    for row in reader:
        dt = _parse_csv_datetime(row.get("date"), row.get("time"))
        if dt is None:
            report.add("bad_date", Severity.SKIPPED, "CSV row with no valid date skipped")
            continue

        account = (row.get("account") or "").strip()
        currency = (row.get("currency") or "GBP").strip() or "GBP"
        try:
            amount = float((row.get("amount") or "0").replace(",", "."))
        except ValueError:
            report.add("bad_amount", Severity.SKIPPED, "CSV row with invalid amount skipped")
            continue

        note = (row.get("note") or "").strip() or None
        category = (row.get("category") or "").strip() or None
        parent = (row.get("parent") or "").strip() or None
        payee = (row.get("payee") or "").strip() or None
        location = (row.get("location") or "").strip() or None
        project = (row.get("project") or "").strip() or None

        # Foreign original amount cannot be preserved separately.
        orig_cur = (row.get("original currency") or "").strip()
        if orig_cur and orig_cur != currency:
            report.add("original_amount", Severity.INFO, "Foreign original amounts simplified",
                       "Only the account-currency amount was kept.")

        # Transfers appear as two rows with payee "Transfer In/Out". Map them to
        # Delfin's transfer locations so the transfer view groups them. Best
        # effort: the CSV does not carry the counterpart account.
        if payee in (TRANSFER_IN, TRANSFER_OUT):
            location = payee
            payee = None
            data.locations.add(location)
            report.add("csv_transfer", Severity.PARTIAL, "CSV transfers reconstructed best-effort",
                       "Financisto CSV exports transfers as two rows without the "
                       "counterpart account; they were mapped to Delfin transfer "
                       "legs by direction. Use the .backup format for exact transfers.")

        if account:
            data.accounts.setdefault(account, {"currency": currency, "type": None})
        if category:
            ctype = "income" if amount > 0 else "expense"
            data.register_category(parent, category, ctype)
        if payee:
            data.payees.add(payee)
        if location:
            data.locations.add(location)
        if project:
            data.projects.add(project)

        data.transactions.append(NormalizedTxn(
            date=dt, amount=amount, currency=currency, note=note,
            account_name=account or None, category_name=category,
            category_parent=parent if category else None,
            payee_name=payee, location_name=location, project_name=project,
        ))

    return data


def _parse_csv_datetime(date_str: Optional[str], time_str: Optional[str]) -> Optional[datetime]:
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()
    if not date_str or date_str == "~":
        return None
    combined = f"{date_str} {time_str}".strip()
    fmts = [
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
        "%m/%d/%Y %H:%M:%S", "%m/%d/%Y",
    ]
    for fmt in fmts:
        try:
            return datetime.strptime(combined.strip(), fmt)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Apply to the Delfin database
# ---------------------------------------------------------------------------
def apply_to_database(
    db: Session, data: NormalizedData, mode: str, report: CompatibilityReport
) -> dict:
    """
    Write normalised data into Delfin. ``mode`` is "merge" or "replace".
    Caller is responsible for the safety backup. Commits on success.
    """
    if mode == "replace":
        _wipe_importable_tables(db)
        db.flush()

    # -- entity caches ------------------------------------------------------
    acc_cache: Dict[str, models.Account] = {}
    cat_cache: Dict[Tuple[Optional[str], str], models.Category] = {}
    payee_cache: Dict[str, models.Payee] = {}
    loc_cache: Dict[str, models.Location] = {}
    proj_cache: Dict[str, models.Project] = {}

    def get_account(name: str, currency: str, atype: Optional[str]) -> models.Account:
        if name in acc_cache:
            return acc_cache[name]
        acc = db.query(models.Account).filter(models.Account.name == name).first()
        if not acc:
            acc = models.Account(name=name, currency=currency or "GBP", type=atype)
            db.add(acc)
            db.flush()
        acc_cache[name] = acc
        return acc

    def get_category(parent: Optional[str], name: str, ctype: Optional[str]) -> models.Category:
        key = (parent or None, name)
        if key in cat_cache:
            return cat_cache[key]
        cat = db.query(models.Category).filter(
            models.Category.name == name, models.Category.parent == (parent or None)
        ).first()
        if not cat:
            cat = models.Category(name=name, parent=parent or None, type=ctype)
            db.add(cat)
            db.flush()
        cat_cache[key] = cat
        return cat

    def get_payee(name: str) -> models.Payee:
        if name in payee_cache:
            return payee_cache[name]
        p = db.query(models.Payee).filter(models.Payee.name == name).first()
        if not p:
            p = models.Payee(name=name)
            db.add(p)
            db.flush()
        payee_cache[name] = p
        return p

    def get_location(name: str) -> models.Location:
        if name in loc_cache:
            return loc_cache[name]
        loc = db.query(models.Location).filter(models.Location.name == name).first()
        if not loc:
            loc = models.Location(name=name)
            db.add(loc)
            db.flush()
        loc_cache[name] = loc
        return loc

    def get_project(name: str) -> models.Project:
        if name in proj_cache:
            return proj_cache[name]
        pr = db.query(models.Project).filter(models.Project.name == name).first()
        if not pr:
            pr = models.Project(name=name)
            db.add(pr)
            db.flush()
        proj_cache[name] = pr
        return pr

    # Pre-create entities (so empty accounts/categories survive an import).
    for name, meta in data.accounts.items():
        get_account(name, meta.get("currency") or "GBP", meta.get("type"))
    for (parent, name), ctype in data.categories.items():
        get_category(parent, name, ctype)
    for name in data.payees:
        get_payee(name)
    for name in data.locations:
        get_location(name)
    for name in data.projects:
        get_project(name)

    # -- duplicate detection (merge only) -----------------------------------
    existing_keys = set()
    if mode == "merge":
        for acc_id, dt, amt, note in db.query(
            models.Transaction.account_id, models.Transaction.date,
            models.Transaction.amount, models.Transaction.note,
        ).all():
            existing_keys.add(_txn_key(acc_id, dt, amt, note))

    inserted = 0
    duplicates = 0
    for ntx in data.transactions:
        account = get_account(
            ntx.account_name or "Imported",
            ntx.currency,
            data.accounts.get(ntx.account_name, {}).get("type"),
        ) if ntx.account_name else None
        if account is None:
            account = get_account("Imported", ntx.currency, None)

        category = None
        if ntx.category_name:
            category = get_category(ntx.category_parent, ntx.category_name, None)
        payee = get_payee(ntx.payee_name) if ntx.payee_name else None
        location = get_location(ntx.location_name) if ntx.location_name else None
        project = get_project(ntx.project_name) if ntx.project_name else None

        key = _txn_key(account.id, ntx.date, ntx.amount, ntx.note)
        if mode == "merge" and key in existing_keys:
            duplicates += 1
            report.add("duplicate", Severity.INFO, "Duplicate transactions skipped",
                       "Transactions identical to existing ones (same account, "
                       "date, amount and note) were not re-imported.")
            continue
        existing_keys.add(key)

        db.add(models.Transaction(
            date=ntx.date,
            amount=round(ntx.amount, 2),
            currency=ntx.currency or "GBP",
            note=ntx.note,
            account_id=account.id,
            category_id=category.id if category else None,
            payee_id=payee.id if payee else None,
            location_id=location.id if location else None,
            project_id=project.id if project else None,
        ))
        inserted += 1

    db.flush()
    initialise_all_balances(db)
    db.commit()

    report.bump("transactions_imported", inserted)
    report.bump("duplicates_skipped", duplicates)
    return {
        "mode": mode,
        "transactions_imported": inserted,
        "duplicates_skipped": duplicates,
        "accounts": len(acc_cache),
        "categories": len(cat_cache),
        "payees": len(payee_cache),
        "locations": len(loc_cache),
        "projects": len(proj_cache),
    }


def _txn_key(account_id, dt, amount, note):
    iso = dt.isoformat() if isinstance(dt, datetime) else str(dt)
    return (account_id, iso, round(float(amount or 0), 2), (note or "").strip())


def _wipe_importable_tables(db: Session) -> None:
    """
    For "replace" mode: clear the entities that an import owns, plus the
    Delfin-only planning tables that reference them (they would otherwise hold
    dangling foreign keys). Exchange rates are kept so currency conversion keeps
    working until the next automatic ECB refresh.
    """
    for model in (
        models.RecurringExpensePayment,
        models.RecurringExpenseHistory,
        models.RecurringExpense,
        models.PlannedExpense,
        models.Transaction,
        models.Payee,       # FK targets cleared after transactions
        models.Category,
        models.Location,
        models.Project,
        models.Account,
    ):
        db.query(model).delete(synchronize_session=False)
