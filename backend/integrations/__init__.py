"""
Delfin data integrations.

Self-contained import/export modules for third-party finance formats.
Each format lives in its own sub-package and is exposed through a small,
uniform surface so that new formats can be added without touching the
rest of the application.

Currently supported:
    - financisto: native ``.backup`` (gzipped entity dump) and CSV export.

The public entry points used by the API layer are:
    - ``financisto.analyze_import(raw_bytes, filename)`` -> dry-run preview + report
    - ``financisto.run_import(db, raw_bytes, filename, mode)`` -> applies to the DB
    - ``financisto.export_database(db, fmt)`` -> bytes ready for download
"""

from backend.integrations.report import CompatibilityReport, Severity

__all__ = ["CompatibilityReport", "Severity"]
