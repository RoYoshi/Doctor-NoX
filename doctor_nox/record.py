"""What Doctor NoX remembers: the conditions found at your last checkup.

It lets the next checkup say what got better or worse. It holds a time and one
condition per test ID, nothing else: no evidence, no names, no addresses. It
lives in your own state directory, is readable only by you, and is safe to
delete. Only the window writes it, and only after a checkup that finished;
the command line, the self-tests and screenshots never do.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .model import SEVERITIES

VERSION = 1
FILE_NAME = "last-checkup.json"
MAX_BYTES = 256 * 1024


def default_path(environ=None) -> Path:
    """$XDG_STATE_HOME/doctor-nox/last-checkup.json, or ~/.local/state/… without it."""
    env = os.environ if environ is None else environ
    base = env.get("XDG_STATE_HOME", "").strip()
    root = Path(base) if base.startswith("/") else Path(env.get("HOME", "") or Path.home()) / ".local" / "state"
    return root / "doctor-nox" / FILE_NAME


def snapshot(report: dict) -> dict:
    """What is remembered of a checkup."""
    return {"version": VERSION, "generated_at": report["generated_at"],
            "findings": {f["id"]: f["severity"] for f in report["findings"]}}


def forget(path) -> bool:
    """Delete the remembered checkup. True when there is none left."""
    if path is None:
        return True
    try:
        Path(path).unlink(missing_ok=True)
        return True
    except OSError:
        return False


def load(path) -> dict | None:
    """The remembered checkup, or None when there is none or the file is not one."""
    if path is None:
        return None
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        data = json.loads(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else None
    except (OSError, ValueError):
        return None
    if (not isinstance(data, dict) or data.get("version") != VERSION
            or not isinstance(data.get("generated_at"), str) or not isinstance(data.get("findings"), dict)):
        return None
    findings = {str(k): v for k, v in data["findings"].items() if v in SEVERITIES}
    return {"version": VERSION, "generated_at": data["generated_at"], "findings": findings}


def save(path, report: dict) -> bool:
    """Remember a finished checkup. Atomic, private (0600), and never an error for the caller."""
    if path is None or not report.get("complete") or report.get("redacted"):
        return False
    target = Path(path)
    tmp = ""
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".doctor-nox-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(snapshot(report), stream, indent=1)
            stream.write("\n")
        os.replace(tmp, target)
        return True
    except OSError:
        return False
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
