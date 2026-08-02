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
    * Split transactions -> one Delfin split: each Financisto child becomes a
      line of it, and the parent envelope (category_id = -1) supplies the
      shared date, account, payee and location.
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
    # Transactions sharing a split key are lines of one split transaction.
    split_key: Optional[str] = None


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
        splits = {t.split_key for t in self.transactions if t.split_key}
        return {
            "accounts": len(self.accounts),
            "categories": len(self.categories),
            "payees": len(self.payees),
            "locations": len(self.locations),
            "projects": len(self.projects),
            "transactions": len(self.transactions),
            "transfers": transfers,
            "splits": len(splits),
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
    # A split is a parent envelope plus the children pointing at it through
    # ``parent_id``. Index the rows first so a child can read the shared payee
    # and location off its parent, and so the envelopes can be recognised even
    # if one lacks the usual category_id = -1 marker.
    txn_rows = by_table.get(fz.T_TRANSACTIONS, [])
    txn_by_id = {str(t.get("_id")): t for t in txn_rows if _as_int(t.get("_id"), 0) > 0}
    split_parent_ids = {
        str(_as_int(t.get("parent_id"), 0))
        for t in txn_rows if _as_int(t.get("parent_id"), 0) > 0
    }
    for t in txn_rows:
        _normalize_backup_txn(t, data, report, accounts, cat_by_id, payees,
                              projects, locations, txn_by_id, split_parent_ids)

    # -- unsupported tables: count + report --------------------------------
    _report_unsupported(by_table, report)

    return data


def _normalize_backup_txn(t, data, report, accounts, cat_by_id, payees,
                          projects, locations, txn_by_id, split_parent_ids) -> None:
    if _as_int(t.get("_id"), 0) <= 0:
        return

    if _as_int(t.get("is_template"), 0) != 0:
        report.add("templates", Severity.SKIPPED, "Templates / scheduled entries skipped",
                   "Financisto transaction templates and scheduled (recurring) "
                   "definitions are not transactions and were not imported.")
        return

    parent_id = _as_int(t.get("parent_id"), 0)
    parent = txn_by_id.get(str(parent_id)) if parent_id > 0 else None

    dt = fz.epoch_ms_to_datetime(t.get("datetime"))
    if parent is not None:
        # Every line of a split happens when the purchase happened, whatever
        # the child row says — a shared date is what holds the split together.
        dt = fz.epoch_ms_to_datetime(parent.get("datetime")) or dt
    if dt is None:
        report.add("bad_date", Severity.SKIPPED, "Transaction with no valid date skipped")
        return

    note = (t.get("note") or "").strip() or None
    from_id = str(t.get("from_account_id"))
    to_id = str(t.get("to_account_id"))
    category_id = _as_int(t.get("category_id"), 0)

    # A split is paid from one account — the envelope's — so its lines follow it.
    if parent is not None and _as_int(parent.get("from_account_id"), 0) > 0:
        from_id = str(parent.get("from_account_id"))

    # original (foreign) currency info we cannot fully preserve
    if _as_int(t.get("original_currency_id"), 0) > 0:
        report.add("original_amount", Severity.INFO,
                   "Foreign original amounts simplified",
                   "Transactions entered in a different currency keep their "
                   "account-currency amount; the separate original foreign "
                   "amount is not stored separately in Delfin.")

    # --- transfer: one row -> two transactions -----------------------------
    if _as_int(t.get("to_account_id"), 0) != 0:
        if parent is not None:
            # Financisto lets one line of a split be a transfer to another
            # account. Delfin's transfers are a pair of transactions, which
            # cannot sit inside a split, so the leg stands on its own.
            report.add("split_transfer_line", Severity.PARTIAL,
                       "Transfer lines lifted out of their split",
                       "A split containing a transfer to another account was "
                       "imported with that line as a separate transfer; the "
                       "remaining lines stayed together as a split.")
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

    # --- split parent: the envelope itself is not a transaction ------------
    # Its amount is the sum of its children, so importing it too would double
    # the spending. What it does carry — payee, location, note — is read off it
    # by each child below.
    if category_id == -1 or str(t.get("_id")) in split_parent_ids:
        report.add("split_parent", Severity.INFO,
                   "Splits imported as split transactions",
                   "Each Financisto split became one Delfin split transaction: "
                   "its sub-items are the lines, and the parent's date, "
                   "account, payee and location are shared by all of them.")
        return

    # --- regular transaction, or one line of a split -----------------------
    acc = accounts.get(from_id)
    if not acc:
        report.add("txn_acct", Severity.SKIPPED, "Transaction with unknown account skipped")
        return
    acc_name, acc_code, acc_dec = acc
    amount = fz.minor_to_major(t.get("from_amount"), acc_dec)

    cat = cat_by_id.get(str(category_id)) if category_id > 0 else None
    cat_name = cat["title"] if cat else None
    cat_parent = (cat["parent_title"] if cat and cat["depth"] >= 2 else None)

    project_name = projects.get(str(t.get("project_id"))) if _as_int(t.get("project_id"), 0) > 0 else None

    # A split line falls back to the envelope for anything it does not set
    # itself: payee and location live on the parent in Financisto, and the
    # parent's note describes the purchase as a whole.
    def _own_or_parent(field):
        own = _as_int(t.get(field), 0)
        if own > 0:
            return own
        return _as_int(parent.get(field), 0) if parent is not None else 0

    payee_id = _own_or_parent("payee_id")
    location_id = _own_or_parent("location_id")
    payee_name = payees.get(str(payee_id)) if payee_id > 0 else None
    location_name = locations.get(str(location_id)) if location_id > 0 else None
    if note is None and parent is not None:
        note = (parent.get("note") or "").strip() or None

    data.transactions.append(NormalizedTxn(
        date=dt, amount=amount, currency=acc_code, note=note,
        account_name=acc_name, category_name=cat_name, category_parent=cat_parent,
        payee_name=payee_name, location_name=location_name, project_name=project_name,
        split_key=str(parent_id) if parent is not None else None,
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

    # Financisto writes a split as a parent row whose category is the literal
    # "SPLIT", carrying the total, immediately followed by one row per line
    # with "~" in the date column (its exporter zeroes the children's dates).
    # Those "~" rows are the breakdown, not broken data: collect them onto the
    # parent, and emit the lines rather than the envelope so the total is not
    # counted twice.
    pending: Optional[dict] = None
    split_seq = 0

    def flush_pending() -> None:
        nonlocal pending
        if pending is None:
            return
        head, lines = pending["head"], pending["lines"]
        pending = None

        if len(lines) >= 2:
            total = round(sum(l.amount for l in lines), 2)
            if abs(total - head["amount"]) >= 0.01:
                report.add("csv_split_sum", Severity.PARTIAL,
                           "Split lines did not add up to their total",
                           f"A split totalling {head['amount']:.2f} had lines adding "
                           f"up to {total:.2f}. The lines were imported as they were "
                           "written; check that transaction after importing.")
            for line in lines:
                _register_csv_entities(data, line)
                data.transactions.append(line)
            report.add("csv_split", Severity.INFO, "Splits rebuilt from the CSV",
                       "Financisto writes a split as a total row followed by its "
                       "sub-items; these were rejoined into Delfin split "
                       "transactions, and the duplicate total row was dropped.")
            return

        if len(lines) == 1:
            # One sub-item is not a split; keep it as an ordinary transaction.
            line = lines[0]
            line.split_key = None
            _register_csv_entities(data, line)
            data.transactions.append(line)
            return

        # The export was made with sub-items switched off, so only the total
        # survives. Keep the money right and drop Financisto's "SPLIT" marker,
        # which is not a real category.
        whole = NormalizedTxn(
            date=head["date"], amount=head["amount"], currency=head["currency"],
            note=head["note"], account_name=head["account"],
            payee_name=head["payee"], location_name=head["location"],
        )
        _register_csv_entities(data, whole)
        data.transactions.append(whole)
        report.add("csv_split_flat", Severity.PARTIAL,
                   "Split breakdown missing from the CSV",
                   "A split was exported as its total only, without its "
                   "sub-items, so it was imported as one uncategorised "
                   "transaction. Re-export with sub-items included, or use the "
                   ".backup format, to keep the breakdown.")

    for row in reader:
        raw_date = (row.get("date") or "").strip()

        # A "~" row continues the split above it.
        if raw_date == "~" and pending is not None:
            _append_csv_split_line(row, pending, report)
            continue

        flush_pending()

        dt = _parse_csv_datetime(row.get("date"), row.get("time"))
        if dt is None:
            report.add("bad_date", Severity.SKIPPED, "CSV row with no valid date skipped")
            continue

        if (row.get("category") or "").strip().upper() == "SPLIT":
            split_seq += 1
            pending = _start_csv_split(row, dt, split_seq)
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

    flush_pending()   # a split at the very end of the file
    return data


def _start_csv_split(row: dict, dt: datetime, seq: int) -> dict:
    """Hold a split's total row aside while its sub-item rows are read."""
    try:
        amount = float((row.get("amount") or "0").replace(",", "."))
    except ValueError:
        amount = 0.0
    return {
        "key": f"csv-split-{seq}",
        "lines": [],
        "head": {
            "date": dt,
            "amount": round(amount, 2),
            "currency": (row.get("currency") or "GBP").strip() or "GBP",
            "account": (row.get("account") or "").strip() or None,
            "payee": (row.get("payee") or "").strip() or None,
            "location": (row.get("location") or "").strip() or None,
            "note": (row.get("note") or "").strip() or None,
        },
    }


def _append_csv_split_line(row: dict, pending: dict, report: CompatibilityReport) -> None:
    """Turn one "~" row into a line of the split it follows."""
    head = pending["head"]
    try:
        amount = float((row.get("amount") or "0").replace(",", "."))
    except ValueError:
        report.add("bad_amount", Severity.SKIPPED, "CSV row with invalid amount skipped")
        return

    category = (row.get("category") or "").strip() or None
    # A line with no note of its own describes the purchase as a whole.
    note = (row.get("note") or "").strip() or head["note"]

    pending["lines"].append(NormalizedTxn(
        date=head["date"],
        amount=round(amount, 2),
        currency=head["currency"],
        note=note,
        account_name=head["account"],
        category_name=category,
        category_parent=((row.get("parent") or "").strip() or None) if category else None,
        payee_name=head["payee"],
        location_name=head["location"],
        project_name=(row.get("project") or "").strip() or None,
        split_key=pending["key"],
    ))


def _register_csv_entities(data: NormalizedData, txn: NormalizedTxn) -> None:
    """Make sure a normalised CSV row's accounts, categories etc. get created."""
    if txn.account_name:
        data.accounts.setdefault(txn.account_name,
                                 {"currency": txn.currency, "type": None})
    if txn.category_name:
        data.register_category(txn.category_parent, txn.category_name,
                               "income" if txn.amount > 0 else "expense")
    if txn.payee_name:
        data.payees.add(txn.payee_name)
    if txn.location_name:
        data.locations.add(txn.location_name)
    if txn.project_name:
        data.projects.add(txn.project_name)


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
    # Two lines of one split can legitimately be identical (same amount, same
    # empty note), so the key also carries the line's position within its
    # split. Position 0 with "not a split line" is an ordinary transaction.
    existing_keys = set()
    if mode == "merge":
        seen_in_group: Dict[int, int] = {}
        for acc_id, dt, amt, note, group_id in db.query(
            models.Transaction.account_id, models.Transaction.date,
            models.Transaction.amount, models.Transaction.note,
            models.Transaction.split_group_id,
        ).order_by(models.Transaction.id.asc()).all():
            line_no = 0
            if group_id:
                line_no = seen_in_group.get(group_id, 0)
                seen_in_group[group_id] = line_no + 1
            existing_keys.add(_txn_key(acc_id, dt, amt, note, bool(group_id), line_no))

    # Lines of an incoming split, collected so they can be keyed together once
    # the database has handed out their ids.
    split_rows: Dict[str, List[models.Transaction]] = {}
    incoming_line_no: Dict[str, int] = {}

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

        line_no = 0
        if ntx.split_key:
            line_no = incoming_line_no.get(ntx.split_key, 0)
            incoming_line_no[ntx.split_key] = line_no + 1

        key = _txn_key(account.id, ntx.date, ntx.amount, ntx.note,
                       bool(ntx.split_key), line_no)
        if mode == "merge" and key in existing_keys:
            duplicates += 1
            report.add("duplicate", Severity.INFO, "Duplicate transactions skipped",
                       "Transactions identical to existing ones (same account, "
                       "date, amount and note) were not re-imported.")
            continue
        existing_keys.add(key)

        row = models.Transaction(
            date=ntx.date,
            amount=round(ntx.amount, 2),
            currency=ntx.currency or "GBP",
            note=ntx.note,
            account_id=account.id,
            category_id=category.id if category else None,
            payee_id=payee.id if payee else None,
            location_id=location.id if location else None,
            project_id=project.id if project else None,
        )
        db.add(row)
        if ntx.split_key:
            split_rows.setdefault(ntx.split_key, []).append(row)
        inserted += 1

    db.flush()

    # Key each split on its lowest line id, the same anchor the API uses. A
    # split whose siblings were all skipped as duplicates is just a transaction.
    splits_imported = 0
    for rows in split_rows.values():
        if len(rows) < 2:
            continue
        group_id = min(r.id for r in rows)
        for r in rows:
            r.split_group_id = group_id
        splits_imported += 1
    db.flush()

    initialise_all_balances(db)
    db.commit()

    report.bump("transactions_imported", inserted)
    report.bump("duplicates_skipped", duplicates)
    return {
        "mode": mode,
        "transactions_imported": inserted,
        "duplicates_skipped": duplicates,
        "splits_imported": splits_imported,
        "accounts": len(acc_cache),
        "categories": len(cat_cache),
        "payees": len(payee_cache),
        "locations": len(loc_cache),
        "projects": len(proj_cache),
    }


def _txn_key(account_id, dt, amount, note, is_split_line=False, line_no=0):
    iso = dt.isoformat() if isinstance(dt, datetime) else str(dt)
    return (account_id, iso, round(float(amount or 0), 2), (note or "").strip(),
            bool(is_split_line), line_no)


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
