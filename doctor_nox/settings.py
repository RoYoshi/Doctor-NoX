"""Your settings for the Doctor NoX window (Qt-free, unit-tested).

Kept in $XDG_CONFIG_HOME/doctor-nox/settings.json (~/.config/doctor-nox/…
without it). Only the window reads or writes them; the command line has its
own options. A missing, damaged or partly wrong file is never an error: every
value that can't be used falls back to its default.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .model import CATEGORIES
from .theme import MODES, SYSTEM

FILE_NAME = "settings.json"
MAX_BYTES = 64 * 1024
AREAS = tuple(key for key, _title in CATEGORIES)
DEADLINE_RANGE = (5, 300)  # seconds, the same limits as --deadline

DEFAULTS = {
    "theme": SYSTEM,             # system | light | dark
    "checkup_at_launch": True,   # examine as soon as the window opens
    "remember": True,            # keep a chart of checkups for follow-ups (record.py)
    "monitor_motion": True,      # the heart monitor sweeps during a checkup
    "skip_areas": [],            # areas left out of a checkup
    "deadline": 60,              # seconds before a checkup is stopped
}


def default_path(environ=None) -> Path:
    env = os.environ if environ is None else environ
    base = env.get("XDG_CONFIG_HOME", "").strip()
    root = Path(base) if base.startswith("/") else Path(env.get("HOME", "") or Path.home()) / ".config"
    return root / "doctor-nox" / FILE_NAME


def clean(data) -> dict:
    """Settings that can be used: known keys, right types, sensible values; defaults otherwise."""
    out = {key: (list(value) if isinstance(value, list) else value) for key, value in DEFAULTS.items()}
    if not isinstance(data, dict):
        return out
    if data.get("theme") in MODES:
        out["theme"] = data["theme"]
    for key in ("checkup_at_launch", "remember", "monitor_motion"):
        if isinstance(data.get(key), bool):
            out[key] = data[key]
    skip = data.get("skip_areas")
    if isinstance(skip, list):
        chosen = [area for area in AREAS if area in skip]
        if len(chosen) < len(AREAS):  # a checkup always examines something
            out["skip_areas"] = chosen
    deadline = data.get("deadline")
    if isinstance(deadline, (int, float)) and not isinstance(deadline, bool):
        out["deadline"] = int(min(max(deadline, DEADLINE_RANGE[0]), DEADLINE_RANGE[1]))
    return out


def load(path) -> dict:
    if path is None:
        return clean(None)
    try:
        with open(path, "rb") as stream:
            raw = stream.read(MAX_BYTES + 1)
        return clean(json.loads(raw.decode("utf-8")) if len(raw) <= MAX_BYTES else None)
    except (OSError, ValueError):
        return clean(None)


def save(path, settings: dict) -> bool:
    """Atomic and private. Never an error for the caller: False means nothing was written."""
    if path is None:
        return False
    target = Path(path)
    tmp = ""
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=".doctor-nox-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(clean(settings), stream, indent=1)
            stream.write("\n")
        os.replace(tmp, target)
        return True
    except OSError:
        return False
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)


def scan_options(settings: dict) -> dict:
    """What a checkup is asked for beyond the defaults: {} for a full, 60-second one."""
    options = {}
    if settings["skip_areas"]:
        options["selected"] = {area for area in AREAS if area not in settings["skip_areas"]}
    if settings["deadline"] != DEFAULTS["deadline"]:
        options["deadline"] = float(settings["deadline"])
    return options
