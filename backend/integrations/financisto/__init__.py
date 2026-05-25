"""
Financisto integration: native ``.backup`` and CSV import/export.

This is the only module the API layer needs to import. Everything below is a
thin orchestration over the format readers (``backup_format``), the structural
mappers (``model``), and the import/export engines.
"""
from __future__ import annotations

from typing import Tuple

from sqlalchemy.orm import Session

from backend.integrations.report import CompatibilityReport
from backend.integrations.financisto import backup_format, exporter, importer


def detect_format(raw: bytes, filename: str = "") -> str:
    """Return 'backup' or 'csv'. Content sniffing wins over the extension."""
    name = (filename or "").lower()
    if backup_format.looks_like_backup(raw):
        return "backup"
    if name.endswith(".csv"):
        return "csv"
    # Default: if it decompresses/decodes and has commas in the first line.
    try:
        head = backup_format.decompress(raw)[:512].decode("utf-8-sig", errors="replace")
    except Exception:
        head = ""
    if "," in head.splitlines()[0] if head.splitlines() else False:
        return "csv"
    return "backup"


def _normalize(raw: bytes, filename: str, report: CompatibilityReport):
    fmt = detect_format(raw, filename)
    if fmt == "backup":
        _, entities = backup_format.parse(raw)
        data = importer.normalize_backup(entities, report)
    else:
        data = importer.normalize_csv(raw, report)
    return fmt, data


def analyze_import(raw: bytes, filename: str = "") -> dict:
    """
    Dry run: parse + map without touching the database. Returns the detected
    format, a summary of what would be imported, and the compatibility report.
    """
    report = CompatibilityReport()
    fmt, data = _normalize(raw, filename, report)
    return {
        "format": fmt,
        "summary": data.summary(),
        "report": report.to_dict(),
    }


def run_import(db: Session, raw: bytes, filename: str, mode: str) -> dict:
    """
    Apply an import. ``mode`` is "merge" or "replace". The caller must already
    have taken a safety backup. Returns the apply summary + compatibility report.
    """
    if mode not in ("merge", "replace"):
        raise ValueError(f"Unknown import mode: {mode!r}")
    report = CompatibilityReport()
    fmt, data = _normalize(raw, filename, report)
    result = importer.apply_to_database(db, data, mode, report)
    return {
        "format": fmt,
        "result": result,
        "report": report.to_dict(),
    }


def export_database(db: Session, fmt: str = "backup") -> Tuple[bytes, str, str]:
    """
    Export the Delfin database in Financisto format.
    Returns (bytes, filename, media_type).
    """
    from datetime import datetime
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if fmt == "csv":
        return (
            exporter.export_csv(db),
            f"delfin_financisto_{stamp}.csv",
            "text/csv",
        )
    return (
        exporter.export_backup(db, gzip_output=True),
        f"delfin_financisto_{stamp}.backup",
        "application/x-gzip",
    )


def export_notes() -> list:
    return exporter.export_notes()
