"""Default Flatpaks and FE-XicoOS core applications (presence only, never launched)."""
from __future__ import annotations

import re

from ..model import INFO, PASS, UNKNOWN, WARN
from . import finding, probe
from .updates import unit_show

CAT = "software"
MANIFEST = "/usr/share/fe-xicoos/default-flatpaks.txt"
MARKER = "/var/lib/fe-xicoos/default-flatpaks-v4.done"
SERVICE = "fe-xicoos-flatpaks.service"
APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _list(ctx, scope):
    res = ctx.runner.run("flatpak", "list", f"--{scope}", "--app", "--columns=application")
    if not res.ok:
        return None, res
    return {l.strip() for l in res.stdout.splitlines() if APP_ID.fullmatch(l.strip())}, res


@probe("software.flatpaks", CAT, "Default Flatpaks")
def flatpaks(ctx):
    text = ctx.try_text(MANIFEST, 16 * 1024)
    if text is None:
        return finding("software.flatpaks", CAT, "Default Flatpaks", UNKNOWN, "Default Flatpak manifest missing",
                       reason=f"{MANIFEST} is not readable")
    defaults = [l.strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")
                and APP_ID.fullmatch(l.strip())]
    system, res_sys = _list(ctx, "system")
    if system is None:
        return finding("software.flatpaks", CAT, "Default Flatpaks", UNKNOWN, "Could not list Flatpaks",
                       reason=res_sys.describe())
    user, res_user = _list(ctx, "user")
    provisioned = ctx.exists(MARKER)
    props, res_show = unit_show(ctx, SERVICE)
    service = ", ".join(f"{k}={props[k]}" for k in ("ActiveState", "SubState", "Result") if k in props) \
        or res_show.describe()
    ev = [f"manifest: {len(defaults)} app(s)", f"provisioning marker {MARKER}: {'present' if provisioned else 'absent'}",
          f"{SERVICE}: {service}"]
    rows, missing, user_only = [], [], []
    for app in defaults:
        if app in system:
            rows.append(f"{app}: system")
        elif user and app in user:
            rows.append(f"{app}: user only")
            user_only.append(app)
        else:
            rows.append(f"{app}: not installed")
            missing.append(app)
    ev += rows
    if user is None:
        ev.append(f"user installation not readable: {res_user.describe()}")
    if not missing:
        note = f" ({len(user_only)} installed per-user)" if user_only else ""
        return finding("software.flatpaks", CAT, "Default Flatpaks", PASS,
                       f"All {len(defaults)} default apps installed{note}", evidence=ev)
    failed = props.get("Result") not in (None, "", "success") or props.get("ActiveState") == "failed"
    running = props.get("ActiveState") in ("activating", "active") and props.get("SubState") in ("start", "running")
    if not provisioned and running:
        return finding("software.flatpaks", CAT, "Default Flatpaks", INFO,
                       f"Default apps are being installed now ({len(missing)} remaining)", evidence=ev)
    if not provisioned and failed:
        return finding("software.flatpaks", CAT, "Default Flatpaks", WARN,
                       f"Default app provisioning failed; {len(missing)} app(s) missing", evidence=ev,
                       guidance=[f"`journalctl -u {SERVICE}` shows the error (often no network on first boot).",
                                 "fe-xicoos-flatpaks.timer retries automatically every 2 hours."])
    if not provisioned:
        return finding("software.flatpaks", CAT, "Default Flatpaks", INFO,
                       f"Default apps not provisioned yet ({len(missing)} pending)", evidence=ev,
                       guidance=["Provisioning runs after first login when Flathub is reachable and retries "
                                 "every 2 hours. This is normal on a fresh install."])
    return finding("software.flatpaks", CAT, "Default Flatpaks", INFO,
                   f"{len(missing)} default app(s) not installed: " + ", ".join(missing[:3]), evidence=ev,
                   guidance=["Provisioning already completed, so these were probably removed on purpose. "
                             "FE-XicoOS does not reinstall them. Reinstall from Bazaar if you want them."])


CORE = (
    ("Foot (default terminal)", "/usr/bin/foot", True),
    ("Dolphin", "/usr/bin/dolphin", True),
    ("Steam", "/usr/bin/steam", True),
    ("Doctor NoX", "/usr/bin/doctor-nox", True),
)


@probe("software.core_apps", CAT, "Core applications")
def core_apps(ctx):
    ev, missing = [], []
    for label, path, required in CORE:
        present = ctx.exists(path)
        ev.append(f"{label}: {path} {'present' if present else 'MISSING'}")
        if required and not present:
            missing.append(label)
    part = ctx.runner.run("rpm", "-q", "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n", "konsole-part")
    if part.ok:
        ev.append(f"Dolphin F4 terminal: {part.stdout.strip()}")
    elif part.status == "exit":
        ev.append("Dolphin F4 terminal: konsole-part NOT installed")
        missing.append("konsole-part (Dolphin F4 terminal)")
    else:
        ev.append(f"Dolphin F4 terminal: {part.describe()}")
    standalone = ctx.exists("/usr/bin/konsole")
    ev.append(f"standalone Konsole: {'present' if standalone else 'absent (by design; Foot is the terminal)'}")
    if missing:
        return finding("software.core_apps", CAT, "Core applications", WARN,
                       "Missing from the image: " + ", ".join(missing), evidence=ev,
                       guidance=["These ship in the FE-XicoOS image. A missing one points at an image build "
                                 "problem; check the next image or roll back."])
    if standalone:
        return finding("software.core_apps", CAT, "Core applications", INFO,
                       "Core apps present; standalone Konsole is also installed", evidence=ev)
    return finding("software.core_apps", CAT, "Core applications", PASS,
                   "Foot, Dolphin (with F4 terminal), Steam and Doctor present", evidence=ev)
