"""Allowlisted, bounded, cancellable command execution.

Every command Doctor can run is declared here as an executable *and* the
exact argument patterns that are permitted. Anything else raises
CommandNotAllowed before a process is created. There is no shell, no stdin,
a minimal environment, a wall-clock timeout, per-stream output caps, and the
whole process group is killed on timeout, cap overflow or cancellation.

Adding a probe that needs a new command means adding a reviewed pattern
here; tests/test_doctor_nox.py fails if a pattern contains a mutating verb.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
import re
import selectors
import signal
import stat
import subprocess
import threading
import time
from typing import Sequence, Union

Token = Union[str, "re.Pattern[str]", frozenset]

# ── fixed vocabularies ───────────────────────────────────────────────────────
#: The only systemd units Doctor may name in a command.
UNITS = frozenset({
    "fe-xicoos-auto-update.timer", "fe-xicoos-auto-update.service",
    "fe-xicoos-auto-update-migrate.service", "bootc-fetch-apply-updates.timer",
    "bootc-fetch-apply-updates.service", "fe-xicoos-flatpaks.service",
    "fe-xicoos-flatpaks.timer", "fe-xicoos-updater-config-migrate.service",
    "fe-xicoos-update-notifier.timer", "fe-xicoos-update-notifier.service",
    "fe-xicoos-status.service", "fe-xicoos-check.service", "fe-xicoos-upgrade.service",
    "fe-xicoos-rollback.service", "NetworkManager.service", "systemd-resolved.service",
})
JOURNAL_UNITS = frozenset(f"--unit={u}" for u in UNITS if u.endswith(".service"))
#: Packages Doctor may query with rpm -q (read-only rpmdb query).
PACKAGES = frozenset({
    "kernel-core", "kernel-modules", "kernel-modules-extra", "foot", "foot-terminfo",
    "konsole", "konsole-part", "dolphin", "steam", "python3-pyside6", "flatpak",
    "akmod-nvidia", "xorg-x11-drv-nvidia", "xorg-x11-drv-nvidia-libs", "mesa-vulkan-drivers",
    "kmod-nvidia", "bootc", "pipewire",
})
KREL = re.compile(r"[0-9][0-9A-Za-z._+~-]{0,80}")
#: rpm -q also accepts a package name pinned to the running kernel release.
KERNEL_NVR = re.compile(r"kernel-(?:core|modules|modules-extra)-[0-9][0-9A-Za-z._+~-]{0,80}")
MODULES = frozenset({"xpad", "nvidia", "nvidia_drm", "nouveau", "hid_xpadneo", "uinput"})
MODINFO_FIELDS = frozenset({"filename", "license", "version"})
SHOW_PROPS = "--property=LoadState,ActiveState,SubState,Result,UnitFileState,ExecMainStatus"
FAILED = ("list-units", "--state=failed", "--all", "--no-legend", "--plain", "--no-pager", "--full")
RPM_QF = "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n"


@dataclass(frozen=True)
class CommandSpec:
    paths: tuple[str, ...]
    patterns: tuple[tuple[Token, ...], ...]
    timeout: float = 10.0
    max_stdout: int = 512 * 1024
    max_stderr: int = 16 * 1024


ALLOWED: dict[str, CommandSpec] = {
    "bootc": CommandSpec(("/usr/bin/bootc",), (("status", "--json", "--format-version=1"),),
                         timeout=20.0),
    "systemctl": CommandSpec(("/usr/bin/systemctl",), (
        FAILED,
        ("--user", *FAILED),
        ("is-system-running",),
        ("is-enabled", UNITS),
        ("--user", "is-enabled", UNITS),
        ("show", UNITS, "--no-pager", SHOW_PROPS),
        ("--user", "show", UNITS, "--no-pager", SHOW_PROPS),
    )),
    "getenforce": CommandSpec(("/usr/sbin/getenforce", "/usr/bin/getenforce"), ((),), timeout=5.0),
    "journalctl": CommandSpec(("/usr/bin/journalctl",), (
        # no --quiet: it would hide journalctl's "not seeing system messages" access hint
        ("--boot=0", "--priority=0..3", "--no-pager", "--output=json", "--lines=400"),
        ("--boot=0", JOURNAL_UNITS, "--priority=0..4", "--no-pager", "--output=json", "--lines=40"),
    ), timeout=12.0, max_stdout=2 * 1024 * 1024),
    "flatpak": CommandSpec(("/usr/bin/flatpak",), (
        ("list", "--system", "--app", "--columns=application"),
        ("list", "--user", "--app", "--columns=application"),
    ), timeout=20.0),
    "lspci": CommandSpec(("/usr/bin/lspci", "/usr/sbin/lspci"), (("-nnk",),), timeout=10.0),
    "nvidia-smi": CommandSpec(("/usr/bin/nvidia-smi",), (
        ("--query-gpu=name,driver_version", "--format=csv,noheader"),), timeout=10.0),
    "modinfo": CommandSpec(("/usr/sbin/modinfo", "/usr/bin/modinfo"), (
        ("-k", KREL, "-F", MODINFO_FIELDS, MODULES),), timeout=6.0),
    "rpm": CommandSpec(("/usr/bin/rpm",), (
        ("-q", RPM_QF, PACKAGES),
        ("-q", RPM_QF, KERNEL_NVR),
    ), timeout=10.0),
    "nmcli": CommandSpec(("/usr/bin/nmcli",), (
        ("-t", "-f", "STATE,CONNECTIVITY", "general"),
        ("-t", "-f", "DEVICE,TYPE,STATE", "device"),
    ), timeout=6.0),
}

#: Verbs that must never appear in any allowed pattern (enforced by tests).
FORBIDDEN_VERBS = frozenset({
    "upgrade", "switch", "rollback", "edit", "install", "uninstall", "remove", "update",
    "start", "stop", "restart", "reload", "try-restart", "reload-or-restart", "enable",
    "disable", "mask", "unmask", "reboot", "poweroff", "halt", "kexec", "daemon-reload",
    "reset-failed", "kill", "set-property", "isolate", "--rotate", "--vacuum-size",
    "--vacuum-time", "--flush", "--sync", "repair", "remote-add", "remote-modify",
    "remote-delete", "usr-overlay", "--erase", "-e", "-i", "-U", "--install", "--upgrade",
    "insmod", "rmmod", "modprobe", "--apply", "--now", "set-hours",
})


class CommandNotAllowed(ValueError):
    """Raised for any command/argument combination that is not allowlisted."""


@dataclass
class CmdResult:
    argv: tuple[str, ...]
    status: str            # ok | exit | missing | untrusted | timeout | cancelled | oserror
    returncode: int | None
    stdout: str
    stderr: str
    truncated: bool = False
    duration: float = 0.0

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def describe(self) -> str:
        """Short, factual reason for evidence/UNKNOWN text."""
        cmd = " ".join(self.argv)
        if self.status == "ok":
            return f"`{cmd}` exited 0" + (" (output truncated)" if self.truncated else "")
        if self.status == "exit":
            err = self.stderr.strip().splitlines()[-1:] or [""]
            return f"`{cmd}` exited {self.returncode}" + (f": {err[0][:200]}" if err[0] else "")
        return {
            "missing": f"{self.argv[0]} is not installed",
            "untrusted": f"{self.argv[0]} is not a root-owned system binary; refused to run it",
            "timeout": f"`{cmd}` timed out and was stopped",
            "cancelled": f"`{cmd}` was cancelled",
            "oserror": f"`{cmd}` could not start: {self.stderr[:160]}",
        }.get(self.status, f"`{cmd}`: {self.status}")


def _token_ok(token: Token, value: str) -> bool:
    if isinstance(token, str):
        return value == token
    if isinstance(token, frozenset):
        return value in token
    return bool(token.fullmatch(value))


def validate(name: str, args: Sequence[str]) -> CommandSpec:
    spec = ALLOWED.get(name)
    if spec is None:
        raise CommandNotAllowed(f"command not allowlisted: {name}")
    if not all(isinstance(a, str) for a in args):
        raise CommandNotAllowed("arguments must be strings")
    for pattern in spec.patterns:
        if len(pattern) == len(args) and all(_token_ok(t, a) for t, a in zip(pattern, args, strict=True)):
            return spec
    raise CommandNotAllowed(f"arguments not allowlisted for {name}: {list(args)!r}")


def trusted_executable(paths: Sequence[str]) -> tuple[str | None, str]:
    """First candidate that exists and is a root-owned, non-writable file under /usr."""
    seen_untrusted = ""
    for candidate in paths:
        try:
            real = os.path.realpath(candidate)
            info = os.stat(real)
        except OSError:
            continue
        if (stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022
                and real.startswith("/usr/") and info.st_mode & 0o111):
            return real, "ok"
        seen_untrusted = candidate
    return (None, "untrusted") if seen_untrusted else (None, "missing")


def _safe_env() -> dict[str, str]:
    env = {"PATH": "/usr/bin:/usr/sbin", "LC_ALL": "C.UTF-8", "LANG": "C.UTF-8",
           "SYSTEMD_PAGER": "", "SYSTEMD_COLORS": "0", "NO_COLOR": "1", "PAGER": "cat"}
    uid = os.getuid()
    runtime = os.environ.get("XDG_RUNTIME_DIR", "")
    if runtime == f"/run/user/{uid}":
        env["XDG_RUNTIME_DIR"] = runtime
    bus = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    if re.fullmatch(rf"unix:path=/run/user/{uid}/bus", bus):
        env["DBUS_SESSION_BUS_ADDRESS"] = bus
    try:
        import pwd
        env["HOME"] = pwd.getpwuid(uid).pw_dir
    except (KeyError, ImportError):
        pass
    return env


class Runner:
    """Executes allowlisted commands; shared by all probes of one scan."""

    def __init__(self, cancel: threading.Event | None = None, max_concurrency: int = 4,
                 resolve=trusted_executable):
        self.cancel = cancel or threading.Event()
        self._slots = threading.BoundedSemaphore(max_concurrency)
        self._resolve = resolve
        self.log: list[CmdResult] = []
        self._log_lock = threading.Lock()

    def run(self, name: str, *args: str, timeout: float | None = None) -> CmdResult:
        spec = validate(name, args)  # raises before anything is executed
        argv_display = (name, *args)
        if self.cancel.is_set():
            return self._record(CmdResult(argv_display, "cancelled", None, "", ""))
        exe, why = self._resolve(spec.paths)
        if exe is None:
            return self._record(CmdResult(argv_display, why, None, "", ""))
        with self._slots:
            if self.cancel.is_set():  # may have waited for a slot while the scan was cancelled
                return self._record(CmdResult(argv_display, "cancelled", None, "", ""))
            return self._record(self._execute((exe, *args), argv_display, spec,
                                              min(timeout or spec.timeout, spec.timeout)))

    def _record(self, result: CmdResult) -> CmdResult:
        with self._log_lock:
            self.log.append(result)
        return result

    def _execute(self, argv, display, spec: CommandSpec, timeout: float) -> CmdResult:
        start = time.monotonic()
        try:
            proc = subprocess.Popen(argv, shell=False, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    env=_safe_env(), close_fds=True, start_new_session=True,
                                    cwd="/")
        except OSError as exc:
            return CmdResult(display, "oserror", None, "", f"{type(exc).__name__}: {exc}")
        caps = {proc.stdout: spec.max_stdout, proc.stderr: spec.max_stderr}
        bufs = {proc.stdout: bytearray(), proc.stderr: bytearray()}
        truncated = False
        state = None
        sel = selectors.DefaultSelector()
        for stream in (proc.stdout, proc.stderr):
            os.set_blocking(stream.fileno(), False)
            sel.register(stream, selectors.EVENT_READ)
        capped = False
        try:
            open_streams = 2
            while open_streams:
                if self.cancel.is_set():
                    state = "cancelled"
                    break
                remaining = timeout - (time.monotonic() - start)
                if remaining <= 0:
                    state = "timeout"
                    break
                for key, _ in sel.select(min(remaining, 0.05)):
                    try:
                        chunk = os.read(key.fileobj.fileno(), 65536)
                    except BlockingIOError:
                        continue
                    if not chunk:
                        sel.unregister(key.fileobj)
                        open_streams -= 1
                        continue
                    room = caps[key.fileobj] - len(bufs[key.fileobj])
                    bufs[key.fileobj] += chunk[:max(room, 0)]
                    if len(chunk) > room:
                        truncated = True
                if truncated and len(bufs[proc.stdout]) >= caps[proc.stdout]:
                    capped = True  # stop a runaway producer instead of draining it forever
                    break
            if not state and not capped:
                # Both pipes closed: let the process exit on its own within the time left.
                try:
                    proc.wait(timeout=max(timeout - (time.monotonic() - start), 0.05))
                except subprocess.TimeoutExpired:
                    state = "timeout"
        finally:
            sel.close()
            if state or capped or proc.poll() is None:
                # Kill the whole group before reaping, so its PGID cannot have been reused
                # and grandchildren die even if the leader already exited.
                _kill_group(proc)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                _kill_group(proc)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:  # uninterruptible (D-state); report, don't hang
                    state = state or "timeout"
            for stream in (proc.stdout, proc.stderr):
                stream.close()
        out = bufs[proc.stdout].decode("utf-8", "replace").replace("\x00", "")
        err = bufs[proc.stderr].decode("utf-8", "replace").replace("\x00", "")
        duration = time.monotonic() - start
        if state:
            return CmdResult(display, state, None, out, err, truncated, duration)
        if capped:
            # Doctor stopped the producer at the cap: the output is a valid prefix, not a failure.
            return CmdResult(display, "ok", None, out, err, True, duration)
        code = proc.returncode
        return CmdResult(display, "ok" if code == 0 else "exit", code, out, err, truncated, duration)


def _kill_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # pid == pgid (start_new_session=True)
    except (ProcessLookupError, PermissionError):
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
