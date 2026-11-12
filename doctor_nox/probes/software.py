"""Flatpak and Snap: remotes, installed apps, and whether installed apps can appear in menus.

Apps are listed, never launched, updated or removed.
"""
from __future__ import annotations

import re

from ..distro import FLATPAK, SNAP
from ..model import INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "software"
APP_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
REMOTE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _lines(res, pattern):
    return sorted({l.strip() for l in res.stdout.splitlines() if pattern.fullmatch(l.strip())})


def _flatpak(ctx, *args):
    res = ctx.runner.run("flatpak", *args)
    return (_lines(res, APP_ID if args[0] == "list" else REMOTE) if res.ok else None), res


def menu_dirs(ctx) -> list[str] | None:
    """The data directories desktop menus read apps from, or None when not in a desktop session.

    Inside a Flatpak sandbox the host's session environment is not visible, so nothing is judged.
    """
    if ctx.env.get("XDG_SESSION_TYPE") not in ("wayland", "x11") or ctx.exists("/.flatpak-info"):
        return None
    raw = ctx.env.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"  # the spec's default when unset
    return [d.rstrip("/") for d in raw.split(":") if d]


@probe("software.flatpak", CAT, "Flatpak", applies=FLATPAK)
def flatpak(ctx):
    title = "Flatpak"
    sys_remotes, res_sr = _flatpak(ctx, "remotes", "--system", "--columns=name")
    usr_remotes, _ = _flatpak(ctx, "remotes", "--user", "--columns=name")
    sys_apps, res_sa = _flatpak(ctx, "list", "--system", "--app", "--columns=application")
    usr_apps, _ = _flatpak(ctx, "list", "--user", "--app", "--columns=application")
    if sys_remotes is None and sys_apps is None:
        return finding("software.flatpak", CAT, title, UNKNOWN, "Flatpak did not answer",
                       reason=res_sa.describe(), evidence=[res_sr.describe(), res_sa.describe()])
    ev = [f"system remotes: {', '.join(sys_remotes) if sys_remotes else 'none' if sys_remotes is not None else 'unreadable'}",
          f"user remotes: {', '.join(usr_remotes) if usr_remotes else 'none' if usr_remotes is not None else 'unreadable'}",
          f"apps: {len(sys_apps or [])} system-wide, {len(usr_apps or [])} for this user"]
    remotes = (sys_remotes or []) + (usr_remotes or [])
    apps = (sys_apps or []) + (usr_apps or [])
    dirs = menu_dirs(ctx)
    missing = []
    if dirs is not None:
        system_dir = "/var/lib/flatpak/exports/share"
        # The user folder follows $HOME / $XDG_DATA_HOME, so match it by its tail.
        user_ok = any(d.endswith("/flatpak/exports/share") and d != system_dir for d in dirs)
        if sys_apps and system_dir not in dirs:
            missing.append(system_dir)
        if usr_apps and not user_ok:
            missing.append("~/.local/share/flatpak/exports/share")
        ev.append("XDG_DATA_DIRS " + ("lacks " + ", ".join(missing) if missing else
                                      "includes the Flatpak app folders" if apps else "not needed yet (no apps)"))
    if missing:
        return finding("software.flatpak", CAT, title, WARN,
                       "Installed Flatpak apps may be missing from your app menu", evidence=ev,
                       guidance=["Menus find Flatpak apps through XDG_DATA_DIRS. The Flatpak package sets it at "
                                 "login, so log out and back in after installing Flatpak for the first time.",
                                 "If it stays missing, your login does not read /etc/profile.d/flatpak.sh or "
                                 "Flatpak's systemd environment generator."])
    if not remotes and not apps:
        return finding("software.flatpak", CAT, title, INFO, "Flatpak is installed but has no app sources",
                       evidence=ev, guidance=["Add a remote such as Flathub (flathub.org/setup) to install apps "
                                              "with Flatpak."])
    has_flathub = any(r.startswith("flathub") for r in remotes)
    summary = f"{len(apps)} app(s) from {len(remotes)} source(s)" + (" including Flathub" if has_flathub else "")
    return finding("software.flatpak", CAT, title, PASS, summary, evidence=ev)


@probe("software.snap", CAT, "Snap", applies=SNAP)
def snap(ctx):
    title = "Snap"
    res = ctx.runner.run("snap", "list")
    if res.status == "exit" and "cannot communicate with server" in (res.stderr + res.stdout):
        return finding("software.snap", CAT, title, WARN, "snapd is not running, so snaps cannot start or update",
                       evidence=[res.describe()],
                       guidance=["`systemctl status snapd.service snapd.socket` shows why."])
    if not res.ok:
        return finding("software.snap", CAT, title, UNKNOWN, "Snap did not answer", reason=res.describe(),
                       evidence=[res.describe()])
    rows = [line.split() for line in res.stdout.splitlines()[1:] if line.strip()]
    snaps = [r for r in rows if r and APP_ID.fullmatch(r[0])]
    broken = [r[0] for r in snaps if len(r) >= 6 and "broken" in r[5].split(",")]
    disabled = [r[0] for r in snaps if len(r) >= 6 and "disabled" in r[5].split(",")]
    ev = [f"snaps installed: {len(snaps)}"] + [f"  {r[0]} {r[1] if len(r) > 1 else ''}".rstrip() for r in snaps[:15]]
    if disabled:
        ev.append(f"disabled: {', '.join(disabled)}")
    if broken:
        return finding("software.snap", CAT, title, WARN, f"Broken snap(s): {', '.join(broken[:3])}", evidence=ev,
                       guidance=["A broken snap is missing files it needs and will not start.",
                                 "`snap revert NAME` goes back to its previous revision; reinstalling the snap "
                                 "also helps. Doctor changes nothing."])
    dirs = menu_dirs(ctx)
    if dirs is not None and snaps and "/var/lib/snapd/desktop" not in dirs:
        ev.append("XDG_DATA_DIRS lacks /var/lib/snapd/desktop")
        return finding("software.snap", CAT, title, WARN, "Installed snap apps may be missing from your app menu",
                       evidence=ev, guidance=["Menus find snap apps through XDG_DATA_DIRS, which snapd sets at "
                                              "login. Log out and back in after installing snapd."])
    if not snaps:
        return finding("software.snap", CAT, title, INFO, "No snaps installed", evidence=ev)
    return finding("software.snap", CAT, title, PASS, f"{len(snaps)} snap(s) installed", evidence=ev)
