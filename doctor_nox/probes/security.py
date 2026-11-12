"""SELinux, failed systemd units and root-owned update-state permissions."""
from __future__ import annotations

import os
import stat

from ..model import ERROR, INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "security"


@probe("security.selinux", CAT, "SELinux")
def selinux(ctx):
    ev = []
    mode = ""
    if ctx.exists("/sys/fs/selinux/enforce"):
        raw = (ctx.try_text("/sys/fs/selinux/enforce", 16) or "").strip()
        ev.append(f"/sys/fs/selinux/enforce: {raw or 'unreadable'}")
        mode = {"1": "enforcing", "0": "permissive"}.get(raw, "")
    if not mode:
        res = ctx.runner.run("getenforce")
        ev.append(res.describe())
        if res.ok:
            mode = res.stdout.strip().lower()
        if not ctx.exists("/sys/fs/selinux"):
            ev.append("/sys/fs/selinux: absent")
    if mode == "enforcing":
        return finding("security.selinux", CAT, "SELinux", PASS, "Enforcing", evidence=ev)
    if mode == "permissive":
        return finding("security.selinux", CAT, "SELinux", WARN, "Permissive: denials are logged, not enforced",
                       evidence=ev, guidance=["Fedora ships enforcing. Check `/etc/selinux/config` and the "
                                              "kernel command line (`enforcing=0`) for an override."])
    if mode == "disabled":
        return finding("security.selinux", CAT, "SELinux", ERROR, "SELinux is disabled", evidence=ev,
                       guidance=["Fedora's confinement of services is off. Check `/etc/selinux/config` and "
                                 "the kernel command line (`selinux=0`)."])
    # No selinuxfs and no getenforce answer is not proof of "disabled" (e.g. containers): don't guess.
    return finding("security.selinux", CAT, "SELinux", UNKNOWN, "SELinux state could not be read",
                   evidence=ev, reason="Neither /sys/fs/selinux/enforce nor getenforce gave a recognized state.")


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


@probe("security.failed_units", CAT, "Failed services")
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
    ours = [u for u in names if u.startswith("fe-xico")]
    return finding("security.failed_units", CAT, "Failed services", WARN,
                   f"{count} failed unit(s): " + ", ".join(names[:3]) + ("…" if count > 3 else ""),
                   evidence=ev,
                   guidance=[f"Inspect: `systemctl status {names[0]}` and `journalctl -b -u {names[0]}`"
                             + (" (add --user for user units)" if user and names[0] in [u for u, _ in user] else ""),
                             "Doctor never restarts or resets units."] +
                            ([f"FE-XicoOS units affected: {', '.join(ours)}"] if ours else []))


STATE_DIRS = ("/var/lib/fe-xicoos-auto-updates", "/run/fe-xicoos-updater", "/etc/fe-xico-updater")


@probe("security.state_permissions", CAT, "Update state permissions")
def state_permissions(ctx):
    """Root services trust these directories; a user-writable one would be a privilege problem."""
    ev, bad = [], []
    for path in STATE_DIRS:
        try:
            info = os.lstat(ctx.path(path))
        except FileNotFoundError:
            ev.append(f"{path}: absent")
            continue
        except OSError as exc:
            ev.append(f"{path}: unreadable ({exc.strerror})")
            continue
        mode = stat.S_IMODE(info.st_mode)
        line = f"{path}: uid={info.st_uid} mode={mode:04o}"
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            bad.append(path)
            line += " (not a plain directory)"
        elif info.st_uid != ctx.trusted_uid or mode & 0o022:
            bad.append(path)
            line += " (writable by non-root)"
        ev.append(line)
    if bad:
        return finding("security.state_permissions", CAT, "Update state permissions", WARN,
                       f"Unsafe ownership/permissions on {bad[0]}", evidence=ev,
                       guidance=["FE-XicoOS update services refuse to use state directories that "
                                 "non-root users can write. Expected: root-owned, mode 0755.",
                                 "Correct by hand only after confirming why it changed. Doctor changes nothing."])
    if all(line.endswith("absent") for line in ev):
        return finding("security.state_permissions", CAT, "Update state permissions", INFO,
                       "No update state directories yet", evidence=ev)
    return finding("security.state_permissions", CAT, "Update state permissions", PASS,
                   "Update state directories are root-owned and not writable by others", evidence=ev)
