"""
Database configuration and session management.

The database is encrypted with SQLCipher. There is **no engine until the app is
unlocked** with the data key (DEK), which only happens after a successful login
(see backend/security.py). Until then the app is "locked": get_db() raises 401
and protected routes are refused. This is what makes the at-rest encryption real
— without the password (which unwraps the DEK) the file cannot be opened.
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base

DB_PATH = "./data/finance.db"

Base = declarative_base()

# Set on unlock(), cleared on lock(). Modules must read these dynamically
# (e.g. database.SessionLocal()), never import them once at module load.
engine = None
SessionLocal = None
_dek_hex = None


def is_unlocked() -> bool:
    return engine is not None


def get_engine():
    return engine


def get_dek_hex():
    """Current SQLCipher key (hex), or None when locked. Needed for keyed backups."""
    return _dek_hex


def _apply_pragmas(dbapi_connection):
    cur = dbapi_connection.cursor()
    # The key MUST be set first, before any other access on the connection.
    cur.execute(f"PRAGMA key = \"x'{_dek_hex}'\"")
    cur.execute("PRAGMA journal_mode=WAL")       # Faster concurrent reads
    cur.execute("PRAGMA synchronous=NORMAL")      # Faster writes (safe with WAL)
    cur.execute("PRAGMA cache_size=-64000")        # 64MB cache
    cur.execute("PRAGMA temp_store=MEMORY")        # Temp tables in RAM
    cur.close()


# Columns added to a table after it first shipped. ``create_all`` builds missing
# tables but never alters existing ones, so a database created by an older build
# would quietly lack these. Appending a column is all SQLite needs to do here.
_ADDED_COLUMNS = {
    "budget_items": {
        "day_rule": "VARCHAR DEFAULT 'exact'",
        "day_ordinal": "INTEGER",
        "series_id": "INTEGER",
    },
    "transactions": {
        # Lines of a split transaction, sharing the id of the first line.
        "split_group_id": "INTEGER",
        # The two legs of a transfer, sharing the id of the first leg. Which leg
        # belonged to which used to be worked out afresh on every read, from the
        # date, the amount and the accounts.
        "transfer_group_id": "INTEGER",
    },
    "loans": {
        # What the loan cost to arrange, and whether it was paid at the outset
        # or added to the debt.
        "opening_fee": "FLOAT DEFAULT 0.0",
        "fee_treatment": "VARCHAR DEFAULT 'upfront'",
        # A standing charge for having the loan, and the price of ending it early.
        "recurring_fee": "FLOAT DEFAULT 0.0",
        "recurring_fee_months": "INTEGER DEFAULT 1",
        "early_repayment_fee_pct": "FLOAT DEFAULT 0.0",
        # Whether interest accrues by the month or by the day.
        "interest_unit": "VARCHAR DEFAULT 'month'",
        # When the first instalment falls, when it is not a whole period after
        # the drawdown. NULL keeps the derived date, so old rows are unaffected.
        "first_payment_date": "DATETIME",
    },
}

# Indexes on tables that already existed. ``create_all`` skips a table it finds,
# indexes included, so an index added later has to be created explicitly.
_ADDED_INDEXES = (
    ("transactions", "CREATE INDEX IF NOT EXISTS ix_transactions_split_group_id "
                     "ON transactions (split_group_id)"),
    ("transactions", "CREATE INDEX IF NOT EXISTS idx_transaction_split_group "
                     "ON transactions (split_group_id, id)"),
    ("transactions", "CREATE INDEX IF NOT EXISTS ix_transactions_transfer_group_id "
                     "ON transactions (transfer_group_id)"),
)

def pair_transfer_legs(c) -> None:
    """
    Give every transfer leg a ``transfer_group_id``: the two legs of a transfer
    share one, and a leg whose partner cannot be found gets one of its own.

    Which leg belonged to which used to be worked out afresh on every read -- in
    the ledger, in the transfers endpoint, in the Financisto exporter and in the
    recurring-payment detector -- from the date, the amount and the accounts. This
    settles it once and writes it down.

    That every leg ends up with an id, partnered or not, is what lets the column
    stand alone: being a transfer leg is having one, so the "Transfer In" and
    "Transfer Out" locations are no longer needed to say so. Those legs are
    recognised here by either mark -- the location, for a database that predates
    the column, or the column, once the locations have gone.

    A leg left on its own is looked at again on the next start, so a pair still
    comes good if its other half is restored from a backup. Takes anything with
    ``exec_driver_sql``, so the schema check and the importer both reach it.
    """
    markers = dict(c.exec_driver_sql(
        "SELECT name, id FROM locations WHERE name IN ('Transfer In', 'Transfer Out')"
    ).fetchall())
    out_loc, in_loc = markers.get("Transfer Out"), markers.get("Transfer In")

    # Legs still looking for a partner: never seen, or standing alone in a group
    # of one. Anything already in a settled pair is left untouched.
    rows = c.exec_driver_sql(
        "SELECT id, date, amount, account_id, location_id, transfer_group_id "
        "FROM transactions "
        "WHERE (location_id IN (?, ?) OR transfer_group_id IS NOT NULL) "
        "  AND (transfer_group_id IS NULL OR transfer_group_id IN ("
        "        SELECT transfer_group_id FROM transactions "
        "        WHERE transfer_group_id IS NOT NULL "
        "        GROUP BY transfer_group_id HAVING COUNT(*) < 2)) "
        "ORDER BY date ASC, id ASC",
        (out_loc, in_loc),
    ).fetchall()
    if not rows:
        return

    def is_incoming(row):
        # The marker while it still exists; afterwards the sign, which is what
        # wrote the two legs in the first place. A transfer of nothing has no
        # direction to read, so the leg written first is taken as the outgoing one.
        _id, _date, amount, _account, location, _group = row
        if in_loc is not None and location == in_loc:
            return True
        if out_loc is not None and location == out_loc:
            return False
        return float(amount or 0.0) > 0.0

    incoming_by_date, outgoing = {}, []
    for row in rows:
        if is_incoming(row):
            incoming_by_date.setdefault(row[1], []).append(row)
        else:
            outgoing.append(row)

    taken, updates = set(), []
    for out_row in outgoing:
        out_id, out_date, out_amount, out_account, _, _ = out_row
        candidates = [
            r for r in incoming_by_date.get(out_date, [])
            if r[0] not in taken and r[3] != out_account
        ]
        if not candidates:
            continue
        # An equal figure settles it; otherwise the earliest unclaimed leg, which
        # is the order the pair was written in.
        wanted = abs(float(out_amount or 0.0))
        match = next((r for r in candidates if abs(float(r[2] or 0.0)) == wanted), candidates[0])
        taken.add(match[0])
        taken.add(out_id)
        updates.append((min(out_id, match[0]), out_id, match[0]))

    for group_id, leg_a, leg_b in updates:
        c.exec_driver_sql(
            "UPDATE transactions SET transfer_group_id = ? WHERE id IN (?, ?)",
            (group_id, leg_a, leg_b),
        )

    # Whatever is still alone keeps a group of its own, so that having an id is
    # what makes a row a transfer leg, with no second thing to consult.
    for row in rows:
        if row[0] in taken:
            continue
        if row[5] != row[0]:
            c.exec_driver_sql(
                "UPDATE transactions SET transfer_group_id = ? WHERE id = ?", (row[0], row[0]))


def retire_transfer_locations(c) -> None:
    """
    Take the "Transfer In" and "Transfer Out" locations out of service.

    They were how a transfer used to be recognised, in eight different places.
    Now that every leg carries a ``transfer_group_id`` and that is what every
    reader consults, the two rows say nothing that is not said better elsewhere,
    and they cost a real field: a leg could never record where the money was
    moved, because that slot was taken by a marker.

    Guarded twice over, because unlike everything else that runs at start-up this
    one takes something away. A leg is only unhitched from its marker once it has
    a group id to be known by, and a marker is only deleted once nothing points at
    it at all. Anything unaccounted for keeps both, and this simply runs again on
    the next start.
    """
    markers = dict(c.exec_driver_sql(
        "SELECT name, id FROM locations WHERE name IN ('Transfer In', 'Transfer Out')"
    ).fetchall())
    if not markers:
        return

    for marker_id in markers.values():
        c.exec_driver_sql(
            "UPDATE transactions SET location_id = NULL "
            "WHERE location_id = ? AND transfer_group_id IS NOT NULL",
            (marker_id,),
        )
        # A payee remembers where you usually shop; a marker was never an answer
        # to that, and the row is about to go.
        c.exec_driver_sql(
            "UPDATE payees SET most_common_location_id = NULL WHERE most_common_location_id = ?",
            (marker_id,),
        )
        still_used = c.exec_driver_sql(
            "SELECT 1 FROM transactions WHERE location_id = ? LIMIT 1", (marker_id,)
        ).fetchone()
        if still_used:
            continue
        c.exec_driver_sql("DELETE FROM locations WHERE id = ?", (marker_id,))


# Run after the columns exist, to give the new ones a sensible value on rows that
# predate them. Each is either a statement or a callable taking the connection,
# and each must be safe to run on every start.
_BACKFILLS = (
    # Every item that has never been versioned is a series of one, keyed by itself.
    ("budget_items", "series_id",
     "UPDATE budget_items SET series_id = id WHERE series_id IS NULL"),

    # An account's type used to be free text nobody could set: the app wrote the
    # literal 'Regular' on every account it created, and an import left it empty.
    # Now that it is chosen from a list and has consequences, those two stand-ins
    # have to become a real value. Agreed loan terms are the one thing that can be
    # read off with certainty, so those go first and the rest start at cash for
    # their owner to correct. Nothing is guessed from spending patterns — a type
    # is what you declared it to be.
    #
    # Both are idempotent: once run, no row matches the WHERE clause again. The
    # loans table is guaranteed to exist here, create_all having run first.
    ("accounts", "type",
     "UPDATE accounts SET type = 'LIABILITY' "
     "WHERE (type IS NULL OR type = 'Regular') "
     "AND id IN (SELECT account_id FROM loans WHERE account_id IS NOT NULL)"),
    ("accounts", "type",
     "UPDATE accounts SET type = 'CASH' WHERE type IS NULL OR type = 'Regular'"),

    # Which leg goes with which, recorded rather than re-derived on every read.
    ("transactions", "transfer_group_id", pair_transfer_legs),

    # And with that recorded, the two marker locations have nothing left to say.
    ("transactions", "transfer_group_id", retire_transfer_locations),
)


def _ensure_columns(eng) -> None:
    """Append any column a newer build expects on an already-created table."""
    with eng.connect() as c:
        for table, columns in _ADDED_COLUMNS.items():
            present = {row[1] for row in c.exec_driver_sql(f'PRAGMA table_info("{table}")')}
            if not present:
                continue  # table doesn't exist yet — create_all builds it complete
            for name, ddl in columns.items():
                if name not in present:
                    c.exec_driver_sql(f'ALTER TABLE "{table}" ADD COLUMN {name} {ddl}')
        for table, sql in _ADDED_INDEXES:
            if {row[1] for row in c.exec_driver_sql(f'PRAGMA table_info("{table}")')}:
                c.exec_driver_sql(sql)
        for table, column, action in _BACKFILLS:
            present = {row[1] for row in c.exec_driver_sql(f'PRAGMA table_info("{table}")')}
            if column not in present:
                continue
            # A backfill that a single statement cannot express brings its own.
            if callable(action):
                action(c)
            else:
                c.exec_driver_sql(action)
        c.commit()


def unlock(dek_hex: str) -> None:
    """Open the encrypted DB with the given key and ensure the schema exists.
    Raises if the key cannot open the file. No-op if already unlocked."""
    global engine, SessionLocal, _dek_hex
    if engine is not None:
        return
    import sqlcipher3.dbapi2 as sqlcipher
    _dek_hex = dek_hex
    eng = create_engine(
        f"sqlite:///{DB_PATH}",
        module=sqlcipher,
        connect_args={"check_same_thread": False},
    )
    event.listen(eng, "connect", lambda conn, rec: _apply_pragmas(conn))
    try:
        # Force a real read so a wrong key / non-encrypted file fails loudly here.
        with eng.connect() as c:
            c.exec_driver_sql("SELECT count(*) FROM sqlite_master")
    except Exception:
        eng.dispose()
        _dek_hex = None
        raise
    # First open of a brand-new DB file creates an empty encrypted DB; build tables.
    Base.metadata.create_all(bind=eng)
    _ensure_columns(eng)
    engine = eng
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=eng)


def lock() -> None:
    """Close the DB and forget the key (app becomes locked again)."""
    global engine, SessionLocal, _dek_hex
    if engine is not None:
        engine.dispose()
    engine = None
    SessionLocal = None
    _dek_hex = None


def get_db():
    """Create a database session per request. Raises 401 while the app is locked."""
    if SessionLocal is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Application is locked — please log in.")
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
