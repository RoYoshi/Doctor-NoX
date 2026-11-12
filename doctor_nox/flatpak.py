"""The Flatpak build: the checks run on the host, not in the sandbox.

Inside the Flatpak sandbox Doctor would see the runtime's /usr and /etc, its
own private /var and /run, and only its own processes and mounts: nothing of
the system it is meant to check. So when Doctor runs as the
io.github.royoshi.doctornox Flatpak, it starts its own command line on the
host with flatpak-spawn --host (which needs the org.freedesktop.Flatpak
permission), using the host's /usr/bin/python3 and Doctor's code from the
app's folder, and shows that report. The host run is an ordinary scan: the
same allowlist, read-only, no root.

When that cannot happen (the permission is off, the host has no Python 3.10,
the run fails), the report says so in one finding instead of showing the
sandbox's state as if it were the computer's.
"""
from __future__ import annotations

import configparser
import datetime
import json
from pathlib import Path
import re

from . import APP_ID
from .model import UNKNOWN, Finding, build_report, validate_report

INFO_FILE = "/.flatpak-info"
PACKAGE_DIR = Path(__file__).resolve().parent
TOO_OLD = 64  # hostrun.py's exit status for a host Python older than 3.10
FINDING_ID = "system.host_access"
TITLE = "Access to this computer"
PERMISSION = "org.freedesktop.Flatpak"


def sandbox_app_path(info_path: str = INFO_FILE, package_dir: Path = PACKAGE_DIR) -> str:
    """The host path of this app's files when this is Doctor's own Flatpak, else "".

    Only Doctor's own build qualifies: its code under /app, and /.flatpak-info
    (written by Flatpak itself) naming this app ID.
    """
    if not str(package_dir).startswith("/app/"):
        return ""
    try:
        with open(info_path, encoding="utf-8") as stream:
            text = stream.read(64 * 1024)
    except OSError:
        return ""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return ""
    if parser.get("Application", "name", fallback="") != APP_ID:
        return ""
    path = parser.get("Instance", "app-path", fallback="").rstrip("/")
    if not path.startswith("/") or re.search(r"[\x00-\x1f\x7f]", path) or "/../" in path + "/":
        return ""
    return path


#: Where packaging/flatpak/io.github.royoshi.doctornox.yml installs the package inside /app.
FLATPAK_PACKAGE = "share/doctor-nox/doctor_nox"


def host_script(app_path: str, package_dir: Path = PACKAGE_DIR) -> str:
    """hostrun.py as the host sees it: the app's folder is /app inside the sandbox."""
    try:
        relative = package_dir.relative_to("/app").as_posix()
    except ValueError:  # not running from /app (tests): the manifest's layout
        relative = FLATPAK_PACKAGE
    return f"{app_path}/{relative}/hostrun.py"


def host_args(deadline: float, selected) -> list[str]:
    args = ["--json", "--deadline", f"{deadline:g}"]
    for item in sorted(selected or ()):
        args += ["--only", item]
    return args


def _failure(ctx, started, severity_reason, summary, *, evidence=(), guidance=(), complete=True):
    finished = datetime.datetime.now(datetime.timezone.utc)
    finding = Finding(FINDING_ID, "system", TITLE, UNKNOWN, summary, evidence=list(evidence),
                      reason=severity_reason, guidance=list(guidance))
    host = {"name": "unknown", "kernel": ctx.kernel, "family": "other"}
    return build_report([finding], host, started=started, finished=finished, complete=complete)


def _tail(text: str, lines: int = 6) -> list[str]:
    return [line[:300] for line in text.strip().splitlines()[-lines:] if line.strip()]


def read_report(res) -> dict | None:
    """The host's JSON report, when the run produced a valid one (exit 0, or 1 for Error findings)."""
    if res.status not in ("ok", "exit") or res.returncode not in (0, 1):
        return None
    try:
        report = json.loads(res.stdout)
    except ValueError:
        return None
    return report if isinstance(report, dict) and not validate_report(report) else None


def explain(ctx, res, started) -> dict:
    """A one-finding report saying why a host run produced no report."""
    ev = [res.describe()] + _tail(res.stderr)
    err = res.stderr
    if res.status in ("missing", "untrusted"):
        return _failure(ctx, started, "flatpak-spawn is not available in this Flatpak's runtime.",
                        "Doctor cannot reach this computer from the Flatpak", evidence=ev,
                        guidance=["Install Doctor NoX from your distribution's package instead."])
    if res.status in ("timeout", "cancelled"):
        what = "timed out" if res.status == "timeout" else "was cancelled"
        return _failure(ctx, started, f"The check on the host {what} before it finished.",
                        "Not completed: scan cancelled" if res.status == "cancelled"
                        else "Not completed: the check on the host timed out", evidence=ev, complete=False)
    if res.status not in ("ok", "exit"):
        return _failure(ctx, started, res.describe(), "The check on the host could not start", evidence=ev)
    if res.returncode == TOO_OLD or ("No such file" in err and "python" in err):
        return _failure(ctx, started, "The Flatpak runs its checks with this computer's own Python "
                        "(/usr/bin/python3), and it is missing or older than 3.10.",
                        "Doctor needs Python 3.10 or later on this computer", evidence=ev,
                        guidance=["Install python3 with your package manager (python on Arch), or install "
                                  "Doctor NoX from your distribution's package instead."])
    if PERMISSION in err or "ServiceUnknown" in err or "AccessDenied" in err or "not allowed" in err.lower():
        return _failure(ctx, started, f"The Flatpak runs its read-only checks on this computer, which needs "
                        f"the {PERMISSION} permission, and it is turned off.",
                        "Doctor cannot check this computer: running commands outside the sandbox is off",
                        evidence=ev,
                        guidance=["Turn the permission back on in Flatseal or your desktop's app permissions, "
                                  f"or run `flatpak override --user --talk-name={PERMISSION} {APP_ID}`.",
                                  "Or install Doctor NoX from your distribution's package instead."])
    return _failure(ctx, started, "Doctor's command line on the host stopped without a usable report.",
                    "The check on the host failed", evidence=ev)


def scan_on_host(ctx, app_path: str, *, progress=None, deadline: float = 60.0, selected=None) -> dict:
    """engine.scan for the Flatpak build: run the scan on the host and return its report."""
    started = datetime.datetime.now(datetime.timezone.utc)
    if progress:
        progress(0, 1, "Checking this computer from the Flatpak")
    try:
        res = ctx.runner.run_host_doctor(host_script(app_path), host_args(deadline, selected),
                                         timeout=deadline + 20)
    finally:
        ctx.cancel.set()  # like engine.scan: nothing keeps running after a scan
    report = read_report(res)
    if report is None:
        report = explain(ctx, res, started)
    if progress:
        progress(1, 1, "Checking this computer from the Flatpak")
    return report


def list_checks_on_host(ctx, app_path: str) -> tuple[int, str, str]:
    """`doctor-nox --list-checks` for the Flatpak build: the list for the host, from the host."""
    started = datetime.datetime.now(datetime.timezone.utc)
    res = ctx.runner.run_host_doctor(host_script(app_path), ["--list-checks"], timeout=60)
    if res.status == "ok" and res.stdout.strip():
        return 0, res.stdout, ""
    f = explain(ctx, res, started)["findings"][0]
    lines = [f"Doctor NoX: {f['summary']}", f["reason"], *f["guidance"]]
    return 3, "", "\n".join(line for line in lines if line) + "\n"
