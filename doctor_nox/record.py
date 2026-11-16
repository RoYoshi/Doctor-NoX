"""What Doctor NoX remembers: your chart, the last 30 checkups that finished.

It lets a checkup say what got better or worse since the last one, and where
things are heading (clinic.prognosis). For each checkup it holds the time, the
overall condition, one condition per test ID, and two kinds of figures: free
space per filesystem and the hottest temperature. No evidence, no names, no
addresses. It lives in your own state directory, is readable only by you, and
is safe to delete. Only the window writes it, and only after a checkup that
finished; the command line, the self-tests and screenshots never do.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from . import clinic
from .model import SEVERITIES

VERSION = 1
FILE_NAME = "checkups.json"
KEEP = 30
MAX_BYTES = 1024 * 1024


def default_path(environ=None) -> Path:
    """$XDG_STATE_HOME/doctor-nox/checkups.json, or ~/.local/state/… without it."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_STATE_HOME", "").strip()
    root = Path(base) if base.startswith("/") else Path(env.get("HOME", "") or Path.home()) / ".local" / "state"
    return root / "doctor-nox" / FILE_NAME


def snapshot(report: dict) -> dict:
    """What is kept of one checkup."""
    return {"generated_at": report["generated_at"], "overall": report["overall"],
            "findings": {f["id"]: f["severity"] for f in report["findings"]},
            "metrics": clinic.metrics(report)}


def _entry(data) -> dict | None:
    """One kept checkup, with anything unusable dropped; None when it is not one."""
    if not isinstance(data, dict) or not isinstance(data.get("generated_at"), str) \
            or not isinstance(data.get("findings"), dict):
        return None
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    kept = {}
    disks = {str(path): [float(v[0]), float(v[1])] for path, v in (metrics.get("disk") or {}).items()
             if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) and not isinstance(x, bool)
                                                            for x in v)} if isinstance(metrics.get("disk"), dict) else {}
    if disks:
        kept["disk"] = disks
    if isinstance(metrics.get("temperature"), int) and not isinstance(metrics.get("temperature"), bool):
        kept["temperature"] = metrics["temperature"]
    return {"generated_at": data["generated_at"],
            "overall": data["overall"] if data.get("overall") in SEVERITIES else "unknown",
            "findings": {str(k): v for k, v in data["findings"].items() if v in SEVERITIES},
            "metrics": kept}


def load(path) -> list[dict]:
    """The checkups on record, oldest first. Empty when there are none or the file is not a chart."""
    if path is None:
        return []
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        data = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else None
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("checkups"), list):
        return []
    return [entry for entry in map(_entry, data["checkups"]) if entry][-KEEP:]


def last(history: list[dict]) -> dict | None:
    return history[-1] if history else None


def save(path, report: dict) -> bool:
    """Add a finished checkup to the chart, keeping the newest 30. Atomic, private (0600), and
    never an error for the caller."""
    if path is None or not report.get("complete") or report.get("redacted"):
        return False
    target = Path(path)
    history = [e for e in load(target) if e["generated_at"] != report["generated_at"]] + [snapshot(report)]
    tmp = ""
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".doctor-nox-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump({"version": VERSION, "checkups": history[-KEEP:]}, stream, indent=1)
            stream.write("\n")
        os.replace(tmp, target)
        return True
    except OSError:
        return False
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def forget(path) -> bool:
    """Delete the chart. True when there is none left."""
    if path is None:
        return True
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError:
        return False
