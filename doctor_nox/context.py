"""Probe environment: filesystem root, environment, kernel, identity, runner.

Probes never touch absolute paths directly; they call ctx.path("/proc/...")
so tests can point the whole scan at a fixture directory tree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import datetime
import json
import os
from pathlib import Path
import socket
import stat
import threading
from typing import Any, Mapping

from .runner import Runner


class FileProblem(Exception):
    """A file exists but is not safe/usable to read (reason in str())."""


@dataclass
class Context:
    root: Path = Path("/")
    env: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))
    kernel: str = field(default_factory=lambda: os.uname().release)
    uid: int = field(default_factory=os.getuid)
    user: str = ""
    home: str = ""
    hostname: str = field(default_factory=socket.gethostname)
    now: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc))
    runner: Runner | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    #: Owner required for root-controlled state files. Only fixture tests change it.
    trusted_uid: int = 0

    def __post_init__(self):
        if self.runner is None:
            self.runner = Runner(self.cancel)
        if not self.user or not self.home:
            try:
                import pwd
                entry = pwd.getpwuid(self.uid)
                self.user = self.user or entry.pw_name
                self.home = self.home or entry.pw_dir
            except (KeyError, ImportError):
                self.user = self.user or self.env.get("USER", "")
                self.home = self.home or self.env.get("HOME", "")

    # ── filesystem ───────────────────────────────────────────────────────────
    def path(self, absolute: str) -> Path:
        if not absolute.startswith("/"):
            raise ValueError("probe paths must be absolute")
        return self.root / absolute.lstrip("/")

    def exists(self, absolute: str) -> bool:
        return os.path.lexists(self.path(absolute))

    def read_text(self, absolute: str, limit: int = 256 * 1024, *, regular: bool = True) -> str:
        """Bounded read. /proc and /sys files report size 0, so read is capped instead."""
        p = self.path(absolute)
        info = p.stat()
        if regular and not stat.S_ISREG(info.st_mode):
            raise FileProblem("not a regular file")
        with p.open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise FileProblem(f"larger than {limit} bytes")
        return data.decode("utf-8", "replace").replace("\x00", "")

    def try_text(self, absolute: str, limit: int = 256 * 1024) -> str | None:
        try:
            return self.read_text(absolute, limit)
        except (OSError, FileProblem):
            return None

    def trusted_file(self, absolute: str, limit: int = 256 * 1024) -> tuple[str | None, str]:
        """Read a root-controlled file: regular, not a symlink, root-owned, not group/other writable.

        Returns (text, "") or (None, reason). Used for state written by root
        services, so a user-writable look-alike is never believed.
        """
        p = self.path(absolute)
        try:
            info = os.lstat(p)
        except FileNotFoundError:
            return None, "missing"
        except OSError as exc:
            return None, f"unreadable ({exc.strerror})"
        if stat.S_ISLNK(info.st_mode):
            return None, "is a symlink"
        if not stat.S_ISREG(info.st_mode):
            return None, "not a regular file"
        if info.st_uid != self.trusted_uid:
            return None, f"owned by uid {info.st_uid}, not root"
        if info.st_mode & 0o022:
            return None, f"group/world writable (mode {stat.S_IMODE(info.st_mode):04o})"
        if info.st_size > limit:
            return None, "too large"
        try:
            return self.read_text(absolute, limit), ""
        except PermissionError:
            return None, "permission denied"
        except (OSError, FileProblem) as exc:
            return None, str(exc)

    def json_file(self, absolute: str, limit: int = 256 * 1024, trusted: bool = True) -> tuple[Any, str]:
        if trusted:
            text, why = self.trusted_file(absolute, limit)
        else:
            text = self.try_text(absolute, limit)
            why = "" if text is not None else "missing or unreadable"
        if text is None:
            return None, why
        try:
            return json.loads(text), ""
        except ValueError:
            return None, "malformed JSON"

    def mtime(self, absolute: str) -> datetime.datetime | None:
        try:
            return datetime.datetime.fromtimestamp(os.lstat(self.path(absolute)).st_mtime,
                                                   datetime.timezone.utc)
        except OSError:
            return None

    def listdir(self, absolute: str) -> list[str]:
        try:
            return sorted(os.listdir(self.path(absolute)))
        except OSError:
            return []

    def os_release(self) -> dict[str, str]:
        data: dict[str, str] = {}
        for candidate in ("/usr/lib/os-release", "/etc/os-release"):
            text = self.try_text(candidate, 64 * 1024)
            if text is None:
                continue
            for line in text.splitlines():
                if line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.isupper() or key.replace("_", "").isalnum():
                    data.setdefault(key.strip(), val.strip().strip("\"'"))
            if data:
                break
        return data

    def statvfs(self, absolute: str):
        return os.statvfs(self.path(absolute))
