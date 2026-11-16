"""Referrals: the desktop's own tool for a finding, opened only when you click (Qt-free, unit-tested).

Doctor NoX never operates, but it can send you to the place where you can. Each
referral is a fixed command line from the table below: a graphical tool of the
desktop you are running, with no argument that comes from a report or from you.
A tool is offered only when it is installed in a system directory. The window
starts it detached and does nothing else with it; whatever you change there is
your own doing.
"""
from __future__ import annotations

import os
import shutil

TRUSTED_DIRS = ("/usr/bin", "/usr/local/bin", "/usr/sbin")

# key: (what the link says, ((desktop, command line), …)); "any" suits every desktop. First installed wins.
TOOLS = {
    "sound": ("Open sound settings", (("kde", ("systemsettings", "kcm_pulseaudio")),
                                      ("gnome", ("gnome-control-center", "sound")),
                                      ("any", ("pavucontrol",)))),
    "network": ("Open network settings", (("kde", ("systemsettings", "kcm_networkmanagement")),
                                          ("gnome", ("gnome-control-center", "network")),
                                          ("any", ("nm-connection-editor",)))),
    "bluetooth": ("Open Bluetooth settings", (("kde", ("systemsettings", "kcm_bluetooth")),
                                              ("gnome", ("gnome-control-center", "bluetooth")),
                                              ("any", ("blueman-manager",)))),
    "display": ("Open display settings", (("kde", ("systemsettings", "kcm_kscreen")),
                                          ("gnome", ("gnome-control-center", "display")))),
    "controllers": ("Open game controller settings", (("kde", ("systemsettings", "kcm_gamecontroller")),)),
    "power": ("Open power settings", (("gnome", ("gnome-control-center", "power")),)),
    "firewall": ("Open firewall settings", (("kde", ("systemsettings", "kcm_firewall")),
                                            ("any", ("firewall-config",)))),
    "disk": ("See what is using the space", (("kde", ("filelight",)), ("gnome", ("baobab",)),
                                             ("any", ("filelight",)), ("any", ("baobab",)))),
    "monitor": ("Open the system monitor", (("kde", ("plasma-systemmonitor",)),
                                            ("gnome", ("gnome-system-monitor",)),
                                            ("any", ("plasma-systemmonitor",)),
                                            ("any", ("gnome-system-monitor",)))),
    "software": ("Open the software center", (("kde", ("plasma-discover", "--mode", "update")),
                                              ("gnome", ("gnome-software", "--mode=updates")),
                                              ("any", ("plasma-discover",)), ("any", ("gnome-software",)))),
    "logs": ("Open the log viewer", (("kde", ("ksystemlog",)), ("gnome", ("gnome-logs",)),
                                     ("any", ("ksystemlog",)), ("any", ("gnome-logs",)))),
}

# Which referral suits a test: its ID, or an "area." prefix. The first match wins.
FOR_TEST = (
    ("hardware.audio", "sound"), ("hardware.capture", "sound"), ("hardware.bluetooth", "bluetooth"),
    ("hardware.graphics", "display"), ("hardware.session", "display"), ("hardware.controllers", "controllers"),
    ("hardware.battery", "power"), ("hardware.cpu_memory", "monitor"), ("hardware.sensors", "monitor"),
    ("security.firewall", "firewall"), ("security.failed_units", "logs"), ("logs.", "logs"),
    ("storage.space", "disk"), ("network.", "network"), ("updates.", "software"), ("software.", "software"),
)


def desktop(environ=None) -> str:
    """"kde", "gnome" or "" from XDG_CURRENT_DESKTOP."""
    names = (os.environ if environ is None else environ).get("XDG_CURRENT_DESKTOP", "").lower().split(":")
    if "kde" in names:
        return "kde"
    if any(name in ("gnome", "unity", "budgie", "pantheon") or name.endswith("gnome") for name in names):
        return "gnome"
    return ""


def key_for(finding_id: str) -> str | None:
    for test, key in FOR_TEST:
        if finding_id.startswith(test) if test.endswith(".") else finding_id == test or finding_id.startswith(test + "."):
            return key
    return None


def find(key: str | None, environ=None, which=shutil.which) -> dict | None:
    """{"label", "argv"} for the first installed tool that suits this desktop, or None."""
    if key not in TOOLS:
        return None
    label, candidates = TOOLS[key]
    here = desktop(environ)
    for wanted, argv in candidates:
        if wanted not in ("any", here):
            continue
        path = which(argv[0])
        if path and os.path.dirname(os.path.realpath(path)) in TRUSTED_DIRS:
            return {"label": label, "argv": (path, *argv[1:])}
    return None


def for_finding(finding_id: str, environ=None, which=shutil.which) -> dict | None:
    return find(key_for(finding_id), environ, which)


HELP_KEYS = ("SUPPORT_URL", "BUG_REPORT_URL", "HOME_URL")


def help_url(os_release: dict) -> str:
    """Where this distribution says to ask for help (its os-release), or "". Only a plain https address."""
    for key in HELP_KEYS:
        url = str(os_release.get(key, "")).strip()
        if (url.startswith("https://") and len(url) <= 300 and url.isascii()
                and not any(c.isspace() or c in "\"'<>\\`" for c in url)):
            return url
    return ""


def parse_os_release(text: str) -> dict:
    data = {}
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data.setdefault(key.strip(), value.strip().strip("\"'"))
    return data
