"""
Daily maintenance job and its scheduler.

Because Delfin runs continuously (e.g. on a Raspberry Pi), one nightly job does
all the housekeeping at a user-configured time (default 02:28, see
``settings_store``):

  1. refresh exchange rates,
  2. recalculate balances (the data behind the dashboard graphs),
  3. recalculate payee statistics,
  4. back up the database to a separate disk — but only if user data changed
     (see ``backup.run_backup``).

The scheduler is a pure-Python background thread (no cron/systemd), so it behaves
identically on Linux, Windows and macOS hosts. It also runs a catch-up pass on
startup if the machine was off during the configured window.
"""
import logging
import threading
import time
from datetime import date, datetime, timedelta

from backend.database import SessionLocal
from backend.helpers import initialise_all_balances
from backend.models import Payee, Transaction
from backend import backup as db_backup
from backend import settings_store

logger = logging.getLogger("delfin")

STATE_PATH = "./data/.maintenance_state"   # last successful maintenance date (ISO)

_run_lock = threading.Lock()
_state_lock = threading.Lock()


def recalculate_all_payee_stats(db) -> int:
    """Recompute each payee's most-common category/location/project. Returns count.
    Shared by the maintenance job and the /payees/recalculate-all-stats endpoint."""
    payees = db.query(Payee).all()
    for payee in payees:
        transactions = db.query(Transaction).filter(Transaction.payee_id == payee.id).all()
        if not transactions:
            payee.most_common_category_id = None
            payee.most_common_location_id = None
            payee.most_common_project_id = None
            payee.updated_at = datetime.utcnow()
            continue
        category_counts, location_counts, project_counts = {}, {}, {}
        for t in transactions:
            if t.category_id:
                category_counts[t.category_id] = category_counts.get(t.category_id, 0) + 1
            if t.location_id:
                location_counts[t.location_id] = location_counts.get(t.location_id, 0) + 1
            if t.project_id:
                project_counts[t.project_id] = project_counts.get(t.project_id, 0) + 1
        payee.most_common_category_id = max(category_counts, key=category_counts.get) if category_counts else None
        payee.most_common_location_id = max(location_counts, key=location_counts.get) if location_counts else None
        payee.most_common_project_id = max(project_counts, key=project_counts.get) if project_counts else None
        payee.updated_at = datetime.utcnow()
    return len(payees)


def _update_rates_if_needed() -> bool:
    from backend.update_exchange_rates import update_exchange_rates, get_last_exchange_rate_date
    db = SessionLocal()
    try:
        last = get_last_exchange_rate_date(db)
    finally:
        db.close()
    if not last or last < date.today():
        update_exchange_rates()
        return True
    return False


def run_maintenance(trigger: str = "scheduled") -> dict:
    """Run the full nightly maintenance. Serialized: concurrent triggers are skipped."""
    if not _run_lock.acquire(blocking=False):
        logger.info("Maintenance already running — skipping this trigger.")
        return {"status": "already_running"}
    try:
        logger.info(f"--- Maintenance start ({trigger}) ---")
        result = {"status": "ok", "rates_updated": False, "balances": False,
                  "payees": 0, "backup": None}
        try:
            result["rates_updated"] = _update_rates_if_needed()
        except Exception as e:
            logger.warning(f"Maintenance: rate update failed (non-fatal): {e}")

        db = SessionLocal()
        try:
            initialise_all_balances(db)        # recompute balances behind the graphs
            result["balances"] = True
            result["payees"] = recalculate_all_payee_stats(db)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning(f"Maintenance: recalculation failed (non-fatal): {e}")
        finally:
            db.close()

        try:
            result["backup"] = db_backup.run_backup()
        except Exception as e:
            logger.warning(f"Maintenance: backup failed (non-fatal): {e}")

        _mark_ran_today()
        logger.info(f"--- Maintenance done ({trigger}): {result} ---")
        return result
    finally:
        _run_lock.release()


def is_running() -> bool:
    """True while a maintenance run is in progress."""
    return _run_lock.locked()


def last_run_date() -> "str | None":
    """ISO date of the last successful maintenance, or None."""
    try:
        with open(STATE_PATH) as f:
            return f.read().strip() or None
    except OSError:
        return None


def _ran_today() -> bool:
    try:
        with open(STATE_PATH) as f:
            return f.read().strip() == date.today().isoformat()
    except OSError:
        return False


def _mark_ran_today() -> None:
    with _state_lock:
        try:
            with open(STATE_PATH, "w") as f:
                f.write(date.today().isoformat())
        except OSError as e:
            logger.warning(f"Could not write maintenance state: {e}")


def _seconds_until(hhmm: str) -> float:
    hh, mm = map(int, hhmm.split(":"))
    now = datetime.now()
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def _scheduler_loop() -> None:
    # Catch-up: if the configured time already passed today and we haven't run, run now.
    try:
        hhmm = settings_store.get_settings()["maintenance_time"]
        hh, mm = map(int, hhmm.split(":"))
        now = datetime.now()
        if now > now.replace(hour=hh, minute=mm, second=0, microsecond=0) and not _ran_today():
            logger.info("Missed today's maintenance window — running catch-up.")
            run_maintenance(trigger="catch-up")
    except Exception as e:
        logger.warning(f"Maintenance catch-up check failed: {e}")

    while True:
        delay = _seconds_until(settings_store.get_settings()["maintenance_time"])
        # Cap the sleep so a changed schedule time is picked up within the hour.
        sleep_for = min(delay, 3600)
        time.sleep(sleep_for)
        if sleep_for >= delay:
            run_maintenance(trigger="scheduled")


def start_scheduler() -> None:
    threading.Thread(target=_scheduler_loop, name="delfin-maintenance", daemon=True).start()
    logger.info("Maintenance scheduler started.")
