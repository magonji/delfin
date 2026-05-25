"""
Compatibility reporting for data import/export.

The whole point of this module is *transparency*: whenever the source format
(e.g. Financisto) carries information that Delfin's data model cannot represent
exactly, we record it here instead of dropping it silently. The report is
surfaced to the user before any destructive action is taken.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class Severity(str, Enum):
    """How serious a compatibility finding is."""
    INFO = "info"           # converted cleanly, just so you know
    PARTIAL = "partial"     # converted, but some nuance was lost/transformed
    SKIPPED = "skipped"     # not imported at all (no Delfin equivalent)
    ERROR = "error"         # could not be processed


@dataclass
class Finding:
    """A single compatibility observation, aggregated by ``code``."""
    code: str
    severity: Severity
    title: str
    detail: str = ""
    count: int = 0
    samples: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity.value,
            "title": self.title,
            "detail": self.detail,
            "count": self.count,
            "samples": self.samples[:8],
        }


class CompatibilityReport:
    """
    Collects per-category findings and running counters during an
    import or export. Designed to be serialised to JSON for the UI.
    """

    def __init__(self) -> None:
        self._findings: Dict[str, Finding] = {}
        self.counters: Dict[str, int] = defaultdict(int)

    # -- findings -----------------------------------------------------------
    def add(
        self,
        code: str,
        severity: Severity,
        title: str,
        detail: str = "",
        sample: str | None = None,
    ) -> None:
        """Record one occurrence of a finding, aggregating by ``code``."""
        f = self._findings.get(code)
        if f is None:
            f = Finding(code=code, severity=severity, title=title, detail=detail)
            self._findings[code] = f
        f.count += 1
        if sample and len(f.samples) < 8:
            f.samples.append(sample)

    # -- counters -----------------------------------------------------------
    def bump(self, key: str, amount: int = 1) -> None:
        self.counters[key] += amount

    # -- queries ------------------------------------------------------------
    @property
    def has_data_loss(self) -> bool:
        return any(
            f.severity in (Severity.SKIPPED, Severity.PARTIAL, Severity.ERROR)
            for f in self._findings.values()
        )

    @property
    def findings(self) -> List[Finding]:
        order = {
            Severity.ERROR: 0,
            Severity.SKIPPED: 1,
            Severity.PARTIAL: 2,
            Severity.INFO: 3,
        }
        return sorted(self._findings.values(), key=lambda f: (order[f.severity], -f.count))

    def to_dict(self) -> dict:
        return {
            "has_data_loss": self.has_data_loss,
            "counters": dict(self.counters),
            "findings": [f.to_dict() for f in self.findings],
        }
