"""
Off-disk database backup, taken as part of the daily maintenance job.

Writes a transactionally-consistent SQLite snapshot of the live database to a
separate backup directory (``DELFIN_BACKUP_DIR``, typically a different physical
disk than the live DB), but only when **user data has actually changed** since
the last backup. Old backups are pruned by age according to the retention
setting (see ``settings_store``).

Pure Python (stdlib only) so it runs identically on Linux, Windows and macOS.

Change detection deliberately ignores:
  * the ``exchange_rates`` table — daily rate refreshes are not user activity;
  * ``updated_at`` columns — the maintenance job bumps these on every run;
  * ``total_balance_after`` — a cached value derived from the latest rates, so it
    changes when rates change even with no user activity.
Real edits still alter other columns (amounts, account_balance_after, row
presence, notes, ...), so they are still detected.

A sentinel file (``.delfin-backup-enabled``) must exist in the backup directory
for backups to run. This opts the feature in and guards against writing to an
*unmounted* external disk: if the disk isn't mounted, the bind-mount target is an
empty folder with no sentinel, so the backup is skipped instead of landing on the
wrong disk.
"""
import os
import glob
import hashlib
import logging
import shutil
from datetime import datetime, timedelta
from typing import Optional

from backend import settings_store

logger = logging.getLogger("delfin")

LIVE_DB = "./data/finance.db"

# Where to write backups. Mount a separate disk here (e.g. /srv/storage/backups/delfin).
BACKUP_DIR = os.environ.get("DELFIN_BACKUP_DIR", "/app/backups").strip()

PREFIX = "finance-"
SUFFIX = ".db"
TS_FORMAT = "%Y-%m-%d_%H-%M-%S"
HASH_FILE = ".last-backup.sha256"
SENTINEL = ".delfin-backup-enabled"
TMP_NAME = ".snapshot.tmp"

# Excluded from change detection so a backup is taken only on real user activity.
EXCLUDE_TABLES = {"exchange_rates"}
EXCLUDE_COLUMNS = {"updated_at", "total_balance_after"}


def _copy_keyfile_to(dest_dir: str) -> None:
    from backend import security
    try:
        if os.path.exists(security.KEYFILE):
            shutil.copy2(security.KEYFILE, os.path.join(dest_dir, os.path.basename(security.KEYFILE)))
    except OSError as e:
        logger.warning(f"Could not copy keyfile to backup dir: {e}")


def _connect(path: str):
    """Open a SQLCipher DB applying the current data key, so encrypted DBs (the
    live one and the snapshots) can be read/written."""
    import sqlcipher3.dbapi2 as sqlcipher
    from backend import database
    con = sqlcipher.connect(path)
    dek = database.get_dek_hex()
    if dek:
        con.execute(f"PRAGMA key = \"x'{dek}'\"")
    return con


def _snapshot_to(path: str) -> None:
    """Write a consistent, encrypted copy of the live DB via SQLCipher's online
    backup API (keyed with the same data key). Safe with WAL and concurrent writes."""
    src = _connect(LIVE_DB)
    try:
        dst = _connect(path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()


def make_snapshot(dest_path: str) -> None:
    """Public: write a consistent, WAL-safe snapshot of the live DB to dest_path.
    Use this for any backup copy instead of a raw file copy."""
    _snapshot_to(dest_path)


def _activity_hash(db_path: str) -> str:
    """Hash of user-facing data, ignoring rate-driven and bookkeeping columns."""
    con = _connect(db_path)
    try:
        cur = con.cursor()
        tables = [
            r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        h = hashlib.sha256()
        for t in tables:
            if t in EXCLUDE_TABLES:
                continue
            cols = [r[1] for r in cur.execute(f'PRAGMA table_info("{t}")')]
            use = [c for c in cols if c not in EXCLUDE_COLUMNS]
            if not use:
                continue
            col_sql = ", ".join(f'"{c}"' for c in use)
            h.update(f"\n--{t}({col_sql})--\n".encode())
            try:
                rows = cur.execute(f'SELECT {col_sql} FROM "{t}" ORDER BY rowid')
            except sqlite3.OperationalError:
                rows = cur.execute(f'SELECT {col_sql} FROM "{t}"')
            for row in rows:
                h.update(repr(row).encode())
        return h.hexdigest()
    finally:
        con.close()


def _parse_ts(filename: str) -> Optional[datetime]:
    if not (filename.startswith(PREFIX) and filename.endswith(SUFFIX)):
        return None
    core = filename[len(PREFIX):-len(SUFFIX)]
    try:
        return datetime.strptime(core, TS_FORMAT)
    except ValueError:
        return None


def _prune(backup_dir: str) -> None:
    """Delete backups older than the retention window (no-op when 'never')."""
    days = settings_store.retention_days()
    if days is None:
        return
    cutoff = datetime.now() - timedelta(days=days)
    for path in glob.glob(os.path.join(backup_dir, f"{PREFIX}*{SUFFIX}")):
        ts = _parse_ts(os.path.basename(path))
        if ts is not None and ts < cutoff:
            try:
                os.remove(path)
            except OSError as e:
                logger.warning(f"Backup prune: could not remove {path}: {e}")


def dir_present() -> bool:
    """True if the backup directory exists (i.e. the target disk is mounted)."""
    return bool(BACKUP_DIR) and os.path.isdir(BACKUP_DIR)


def is_enabled() -> bool:
    """True if backups are switched on (sentinel present in the backup dir)."""
    return dir_present() and os.path.exists(os.path.join(BACKUP_DIR, SENTINEL))


def list_backups() -> list:
    """Backup filenames, oldest first (chronological by name)."""
    if not dir_present():
        return []
    return [os.path.basename(p) for p in
            sorted(glob.glob(os.path.join(BACKUP_DIR, f"{PREFIX}*{SUFFIX}")))]


def enable() -> None:
    """Switch backups on by creating the sentinel. Raises if the dir isn't available."""
    if not dir_present():
        raise RuntimeError(
            f"Backup folder '{BACKUP_DIR}' is not available — is the disk mounted "
            "and the volume mapped?")
    open(os.path.join(BACKUP_DIR, SENTINEL), "w").close()


def disable() -> None:
    """Switch backups off by removing the sentinel."""
    try:
        os.remove(os.path.join(BACKUP_DIR, SENTINEL))
    except OSError:
        pass


def status() -> dict:
    """Backup state for the Tools UI."""
    files = list_backups()
    last = files[-1] if files else None
    ts = _parse_ts(last) if last else None
    return {
        "backup_dir": BACKUP_DIR,
        "dir_present": dir_present(),
        "enabled": is_enabled(),
        "count": len(files),
        "last_backup": last,
        "last_backup_at": ts.isoformat() if ts else None,
    }


def run_backup() -> Optional[str]:
    """Back up the live DB to the external disk if user data changed since the last
    backup, then prune by age. Returns the new filename, or None. Never raises."""
    if not BACKUP_DIR:
        return None
    if not os.path.isdir(BACKUP_DIR):
        logger.info(f"Backup dir '{BACKUP_DIR}' not present — skipping backup.")
        return None
    if not os.path.exists(os.path.join(BACKUP_DIR, SENTINEL)):
        logger.info(
            f"Sentinel '{SENTINEL}' missing in {BACKUP_DIR} "
            "(backups disabled or external disk not mounted) — skipping backup."
        )
        return None
    if not os.path.exists(LIVE_DB):
        logger.warning("Live database not found — skipping backup.")
        return None

    # Keep the keyfile next to the backups: the encrypted .db is unrecoverable
    # without the wrapped data key, so disaster recovery needs both + the password.
    _copy_keyfile_to(BACKUP_DIR)

    tmp_path = os.path.join(BACKUP_DIR, TMP_NAME)
    try:
        _snapshot_to(tmp_path)
        new_hash = _activity_hash(tmp_path)

        hash_path = os.path.join(BACKUP_DIR, HASH_FILE)
        last_hash = None
        if os.path.exists(hash_path):
            try:
                with open(hash_path) as f:
                    last_hash = f.read().strip()
            except OSError:
                last_hash = None

        if new_hash == last_hash:
            logger.info("No user activity since last backup — skipping (rate-only changes ignored).")
            os.remove(tmp_path)
            _prune(BACKUP_DIR)
            return None

        final_name = f"{PREFIX}{datetime.now().strftime(TS_FORMAT)}{SUFFIX}"
        final_path = os.path.join(BACKUP_DIR, final_name)
        os.replace(tmp_path, final_path)
        with open(hash_path, "w") as f:
            f.write(new_hash)

        _prune(BACKUP_DIR)
        logger.info(f"Database backup written: {final_path}")
        return final_name
    except Exception as e:
        logger.warning(f"Backup failed (non-fatal): {e}")
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass
        return None
