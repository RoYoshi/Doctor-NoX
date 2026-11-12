"""Findings, severities, categories and the versioned report document."""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import re
from typing import Any

from . import VERSION

SCHEMA_ID = "doctor-nox/report"
SCHEMA_VERSION = 2

# Ordered from most to least urgent. Every severity is shown as text, never colour alone.
PASS, INFO, WARN, ERROR, UNKNOWN = "pass", "info", "warn", "error", "unknown"
SEVERITIES = (ERROR, WARN, UNKNOWN, INFO, PASS)
SEVERITY_LABEL = {PASS: "PASS", INFO: "INFO", WARN: "WARN", ERROR: "ERROR", UNKNOWN: "UNKNOWN"}
SEVERITY_MEANING = {
    PASS: "Verified healthy.",
    INFO: "Verified; worth knowing, not a fault (for example manual-update mode).",
    WARN: "Verified condition that may need attention.",
    ERROR: "Verified fault that is likely to break functionality or safety.",
    UNKNOWN: "Could not be verified (missing tool, permission, unsupported format). Not a pass.",
}

CATEGORIES = (
    ("system", "System & bootc"),
    ("updates", "Updates"),
    ("security", "Security & Services"),
    ("hardware", "Hardware & Gaming"),
    ("storage", "Storage"),
    ("software", "Apps & Flatpaks"),
    ("network", "Network"),
    ("logs", "Logs"),
)
CATEGORY_TITLE = dict(CATEGORIES)

_ID = re.compile(r"[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+")
MAX_EVIDENCE_LINES = 60
MAX_LINE = 400


@dataclass
class Finding:
    """One verified (or explicitly unverifiable) observation.

    id        stable dotted identifier, e.g. "system.bootc.rollback"
    category  key from CATEGORIES
    title     short noun phrase
    severity  one of SEVERITIES
    summary   one line, the result
    evidence  factual lines the result is based on (commands, files, values)
    reason    why the result is UNKNOWN/ERROR, when applicable
    guidance  human steps to troubleshoot; never executed by Doctor
    """
    id: str
    category: str
    title: str
    severity: str
    summary: str
    evidence: list[str] = field(default_factory=list)
    reason: str = ""
    guidance: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not _ID.fullmatch(self.id):
            raise ValueError(f"invalid finding id: {self.id!r}")
        if self.category not in CATEGORY_TITLE:
            raise ValueError(f"invalid category: {self.category!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"invalid severity: {self.severity!r}")
        self.title = _clip(self.title, 80)
        self.summary = _clip(self.summary, 200)
        self.reason = _clip(self.reason, MAX_LINE)
        self.evidence = [_clip(str(line), MAX_LINE) for line in self.evidence][:MAX_EVIDENCE_LINES]
        self.guidance = [_clip(str(line), MAX_LINE) for line in self.guidance][:12]

    def to_dict(self) -> dict[str, Any]:
        return dict(id=self.id, category=self.category, title=self.title,
                    severity=self.severity, summary=self.summary,
                    evidence=list(self.evidence), reason=self.reason,
                    guidance=list(self.guidance))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Finding":
        return cls(**{k: data[k] for k in ("id", "category", "title", "severity", "summary",
                                           "evidence", "reason", "guidance")})


def _clip(text: str, limit: int) -> str:
    text = str(text).replace("\x00", "")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def totals(findings: list[Finding]) -> dict[str, int]:
    return {sev: sum(f.severity == sev for f in findings) for sev in (PASS, INFO, WARN, ERROR, UNKNOWN)}


def overall(findings: list[Finding]) -> str:
    """Worst verified state. UNKNOWN never upgrades a scan to PASS."""
    t = totals(findings)
    if t[ERROR]:
        return ERROR
    if t[WARN]:
        return WARN
    if t[UNKNOWN]:
        return UNKNOWN
    return PASS if findings else UNKNOWN


def build_report(findings: list[Finding], host: dict[str, str], *, started: datetime.datetime,
                 finished: datetime.datetime, complete: bool, redacted: bool = False) -> dict[str, Any]:
    return {
        "schema": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "doctor_version": VERSION,
        "generated_at": finished.astimezone(datetime.timezone.utc).isoformat(timespec="seconds"),
        "duration_ms": max(0, int((finished - started).total_seconds() * 1000)),
        "complete": complete,
        "redacted": redacted,
        "host": {k: str(host.get(k, "")) for k in ("name", "edition", "version", "kernel", "image_variant")},
        "overall": overall(findings),
        "totals": totals(findings),
        "findings": [f.to_dict() for f in findings],
    }


def validate_report(report: dict[str, Any]) -> list[str]:
    """Structural self-check mirroring data/doctor-nox-report.schema.json."""
    problems = []
    expect = {"schema": str, "schema_version": int, "doctor_version": str, "generated_at": str,
              "duration_ms": int, "complete": bool, "redacted": bool, "host": dict,
              "overall": str, "totals": dict, "findings": list}
    for key, kind in expect.items():
        if not isinstance(report.get(key), kind):
            problems.append(f"{key}: expected {kind.__name__}")
    if problems:
        return problems
    if report["schema"] != SCHEMA_ID or report["schema_version"] != SCHEMA_VERSION:
        problems.append("unsupported schema")
    if report["overall"] not in SEVERITIES:
        problems.append("overall: invalid severity")
    seen = set()
    for i, item in enumerate(report["findings"]):
        try:
            finding = Finding.from_dict(item)
        except (KeyError, TypeError, ValueError) as exc:
            problems.append(f"findings[{i}]: {exc}")
            continue
        if finding.id in seen:
            problems.append(f"findings[{i}]: duplicate id {finding.id}")
        seen.add(finding.id)
    return problems
