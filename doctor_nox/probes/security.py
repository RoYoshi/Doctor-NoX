"""Mandatory access control (SELinux or AppArmor), firewall and failed systemd units."""
from __future__ import annotations

import re

from ..distro import SYSTEMD
from ..model import INFO, PASS, UNKNOWN, WARN
from . import finding, probe
from .updates import unit_show

CAT = "security"
#: Families whose installers turn a mandatory access control system on by default.
MAC_DEFAULT = {"fedora": "SELinux", "debian": "AppArmor", "suse": "AppArmor or SELinux"}


def _selinux_mode(ctx, ev):
    """'enforcing' | 'permissive' | 'disabled' | '' (not readable)."""
    if ctx.exists("/sys/fs/selinux/enforce"):
        raw = (ctx.try_text("/sys/fs/selinux/enforce", 16) or "").strip()
        ev.append(f"/sys/fs/selinux/enforce: {raw or 'unreadable'}")
        mode = {"1": "enforcing", "0": "permissive"}.get(raw, "")
        if mode:
            return mode
    if ctx.exists("/sys/fs/selinux") or ctx.exists("/usr/sbin/getenforce") or ctx.exists("/usr/bin/getenforce"):
        res = ctx.runner.run("getenforce")
        ev.append(res.describe())
        if res.ok and res.stdout.strip().lower() in ("enforcing", "permissive", "disabled"):
            return res.stdout.strip().lower()
    return ""


@probe("security.mac", CAT, "SELinux / AppArmor")
def mac(ctx):
    title = "SELinux / AppArmor"
    family = ctx.platform.family
    lsm_text = ctx.try_text("/sys/kernel/security/lsm", 4096)
    lsm = [w for w in (lsm_text or "").strip().split(",") if w]
    ev = [f"active security modules: {', '.join(lsm)}" if lsm_text is not None
          else "/sys/kernel/security/lsm: unreadable"]
    cmdline = ctx.try_text("/proc/cmdline", 16 * 1024) or ""
    overrides = re.findall(r"(?:^|\s)((?:selinux|enforcing|apparmor|security|lsm)=\S+)", cmdline)
    if overrides:
        ev.append(f"kernel command line: {' '.join(overrides)}")
    selinux = _selinux_mode(ctx, ev)
    apparmor = (ctx.try_text("/sys/module/apparmor/parameters/enabled", 16) or "").strip()
    if apparmor:
        ev.append(f"/sys/module/apparmor/parameters/enabled: {apparmor}")
    apparmor_on = apparmor == "Y" and (lsm_text is None or "apparmor" in lsm)

    if selinux == "enforcing":
        return finding("security.mac", CAT, title, PASS, "SELinux is enforcing", evidence=ev)
    if selinux == "permissive":
        return finding("security.mac", CAT, title, WARN, "SELinux is permissive: it logs denials but blocks nothing",
                       evidence=ev, guidance=["Look for `SELINUX=permissive` in /etc/selinux/config and "
                                              "`enforcing=0` on the kernel command line.",
                                              "Permissive mode is a troubleshooting setting; services run "
                                              "unconfined while it is on."])
    if apparmor_on:
        return finding("security.mac", CAT, title, PASS, "AppArmor is enabled", evidence=ev)
    if lsm_text is None and not selinux and not apparmor:
        return finding("security.mac", CAT, title, UNKNOWN, "The security module state could not be read",
                       evidence=ev, reason="/sys/kernel/security is not readable here (common in containers).")
    if family in MAC_DEFAULT:
        what = "SELinux is disabled" if family == "fedora" else "Neither AppArmor nor SELinux is active"
        return finding("security.mac", CAT, title, WARN, what, evidence=ev,
                       guidance=[f"This distribution family turns on {MAC_DEFAULT[family]} by default, so "
                                 "someone switched it off. Services then run without that confinement.",
                                 "Check /etc/selinux/config, the AppArmor service and the kernel command line "
                                 "(`selinux=0`, `apparmor=0`)."])
    return finding("security.mac", CAT, title, INFO, "No mandatory access control is active", evidence=ev,
                   guidance=["This distribution does not turn on AppArmor or SELinux by default, so this is "
                             "its normal state, not a fault."])


@probe("security.firewall", CAT, "Firewall", applies=SYSTEMD)
def firewall(ctx):
    title = "Firewall"
    ev = []
    props, res = unit_show(ctx, "firewalld.service")
    firewalld = props.get("ActiveState", "")
    ev.append(f"firewalld.service: {firewalld or res.describe()}"
              + (f" ({props.get('LoadState')})" if props.get("LoadState") == "not-found" else ""))
    ufw_conf = ctx.try_text("/etc/ufw/ufw.conf", 16 * 1024)
    ufw_on = bool(ufw_conf and re.search(r"^\s*ENABLED\s*=\s*yes\s*$", ufw_conf, re.M | re.I))
    ev.append("ufw: " + ("not installed" if ufw_conf is None else "enabled" if ufw_on else "installed, disabled"))
    if firewalld == "active":
        return finding("security.firewall", CAT, title, PASS, "firewalld is running", evidence=ev)
    if ufw_on:
        return finding("security.firewall", CAT, title, PASS, "ufw is enabled", evidence=ev)
    if not firewalld and ufw_conf is None:
        return finding("security.firewall", CAT, title, UNKNOWN, "Firewall state could not be read", evidence=ev,
                       reason=res.describe())
    return finding("security.firewall", CAT, title, INFO, "No firewalld or ufw firewall is on", evidence=ev,
                   guidance=["A desktop that offers no network services is not exposed by this alone. If you "
                             "share files, run servers or use public networks, turn on your distribution's "
                             "firewall (firewalld or ufw).",
                             "Rules set directly with nftables or iptables need root to read, so Doctor cannot "
                             "see them."])


def _failed(ctx, user):
    args = (("--user",) if user else ()) + ("list-units", "--state=failed", "--all", "--no-legend",
                                             "--plain", "--no-pager", "--full")
    res = ctx.runner.run("systemctl", *args)
    if not res.ok:
        return None, res
    units = []
    for line in res.stdout.splitlines():
        parts = line.split(None, 4)
        if parts and parts[0].lstrip("●* ").count(".") and not parts[0].startswith("0"):
            units.append((parts[0].lstrip("●* "), parts[4] if len(parts) > 4 else ""))
    return units, res


@probe("security.failed_units", CAT, "Failed services", applies=SYSTEMD)
def failed_units(ctx):
    system, res_s = _failed(ctx, user=False)
    user, res_u = _failed(ctx, user=True)
    state = ctx.runner.run("systemctl", "is-system-running")
    ev = [f"system state: {state.stdout.strip() or state.describe()}"]
    if system is None:
        return finding("security.failed_units", CAT, "Failed services", UNKNOWN,
                       "Could not list failed systemd units", reason=res_s.describe(), evidence=ev)
    ev += [f"system: {u}  — {d}" for u, d in system[:20]]
    if user is None:
        ev.append(f"user units: not readable ({res_u.describe()})")
    else:
        ev += [f"user: {u}  — {d}" for u, d in user[:20]]
    count = len(system) + len(user or [])
    if not count:
        return finding("security.failed_units", CAT, "Failed services", PASS,
                       "No failed systemd units" + ("" if user is not None else " (system scope)"), evidence=ev)
    names = [u for u, _ in system] + [u for u, _ in (user or [])]
    first_is_user = bool(user) and names[0] in [u for u, _ in user]
    return finding("security.failed_units", CAT, "Failed services", WARN,
                   f"{count} failed unit(s): " + ", ".join(names[:3]) + ("…" if count > 3 else ""),
                   evidence=ev,
                   guidance=[f"Inspect: `systemctl{' --user' if first_is_user else ''} status {names[0]}` and "
                             f"`journalctl -b {'--user-unit' if first_is_user else '-u'} {names[0]}`.",
                             "Doctor never restarts or resets units."])
