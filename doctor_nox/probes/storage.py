"""Mount layout and free space, read from /proc/self/mountinfo and statvfs.

On bootc the root filesystem and /usr are read-only by design (composefs /
OSTree), and /sysroot is normally mounted read-only too. Only /etc, /var and
the user's home (on /var/home) must be writable.
"""
from __future__ import annotations

import os
import re

from ..model import ERROR, INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "storage"
GiB = 1024 ** 3


def _unescape(field):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def parse_mountinfo(text):
    """{mountpoint: (fstype, source, readonly)} — later mounts shadow earlier ones."""
    mounts = {}
    for line in text.splitlines():
        if " - " not in line:
            continue
        left, right = line.split(" - ", 1)
        lf, rf = left.split(), right.split()
        if len(lf) < 6 or len(rf) < 3:
            continue
        point = _unescape(lf[4])
        # read-only if the mount OR its superblock is read-only (e.g. btrfs after an error remount)
        readonly = "ro" in lf[5].split(",") or "ro" in rf[2].split(",")
        mounts[point] = (rf[0], _unescape(rf[1]), readonly)
    return mounts


def covering(mounts, path):
    best = None
    for point in mounts:
        if path == point or path.startswith(point.rstrip("/") + "/") or point == "/":
            if best is None or len(point) > len(best):
                best = point
    return best


def _real_home(ctx):
    if not ctx.home:
        return ""
    real = os.path.realpath(ctx.path(ctx.home))
    root = str(ctx.root).rstrip("/")
    return real[len(root):] or "/" if root and real.startswith(root) else real


@probe("storage.layout", CAT, "Filesystem layout")
def layout(ctx):
    text = ctx.try_text("/proc/self/mountinfo", 2 * 1024 * 1024)
    if not text:
        return finding("storage.layout", CAT, "Filesystem layout", UNKNOWN, "Mount table unavailable",
                       reason="/proc/self/mountinfo unreadable")
    mounts = parse_mountinfo(text)
    out = []

    def state(path):
        point = covering(mounts, path)
        if point is None:
            return None, None
        fstype, source, ro = mounts[point]
        return point, (fstype, ro)

    ev = []
    for path in ("/", "/usr", "/etc", "/var", "/sysroot"):
        point, info = state(path)
        if info:
            ev.append(f"{path} → mount {point} ({info[0]}, {'read-only' if info[1] else 'read-write'})")
    _, usr = state("/usr")
    _, etc = state("/etc")
    _, var = state("/var")
    problems = []
    if var and var[1]:
        problems.append("/var is read-only: updates, Flatpaks, logs and home data cannot be written")
    if etc and etc[1]:
        problems.append("/etc is read-only: configuration changes cannot persist")
    if problems:
        out.append(finding("storage.layout", CAT, "Filesystem layout", ERROR, problems[0], evidence=ev,
                           guidance=["A read-only /var or /etc usually means the filesystem was remounted "
                                     "after an error. `journalctl -b -k | grep -iE 'error|remount'` shows why.",
                                     "Back up important data before rebooting."]))
    elif usr and not usr[1]:
        overlay = usr[0] == "overlay"
        out.append(finding("storage.layout", CAT, "Filesystem layout", INFO,
                           "/usr is writable (transient overlay; changes vanish at reboot)" if overlay
                           else "/usr is writable, which is unusual on bootc", evidence=ev,
                           guidance=["`bootc usr-overlay` creates a temporary writable /usr for debugging. "
                                     "Persistent changes belong in the image."]))
    elif usr is None or var is None:
        out.append(finding("storage.layout", CAT, "Filesystem layout", UNKNOWN, "Mount layout not recognized",
                           evidence=ev))
    else:
        out.append(finding("storage.layout", CAT, "Filesystem layout", PASS,
                           "/usr read-only by design; /etc and /var writable", evidence=ev))

    home = _real_home(ctx)
    point, info = state(home) if home else (None, None)
    if not home or not info:
        out.append(finding("storage.home", CAT, "Home directory", UNKNOWN, "Home directory location unknown",
                           evidence=[f"home: {home or 'unset'}"]))
    else:
        hev = [f"home resolves to {home}", f"mount {point} ({info[0]}, {'read-only' if info[1] else 'read-write'})"]
        persistent = home.startswith("/var/") or point not in ("/", "/usr")
        if info[1]:
            out.append(finding("storage.home", CAT, "Home directory", ERROR, "Home directory is read-only",
                               evidence=hev))
        elif not persistent:
            out.append(finding("storage.home", CAT, "Home directory", WARN,
                               "Home is not on persistent /var storage", evidence=hev,
                               guidance=["On bootc, /home normally links to /var/home so data survives "
                                         "updates and rollbacks."]))
        else:
            out.append(finding("storage.home", CAT, "Home directory", PASS,
                               f"Home on persistent writable storage ({point})", evidence=hev))
    return out


def _fmt(n):
    return f"{n / GiB:.1f} GiB"


@probe("storage.space", CAT, "Free space")
def space(ctx):
    targets = [("/var", "system data, Flatpaks, logs"), ("/sysroot", "OS deployments")]
    home = _real_home(ctx)
    if home:
        targets.append((home, "your home"))
    filesystems = {}
    errors = []
    for path, role in targets:
        try:
            st = ctx.statvfs(path)
        except OSError as exc:
            errors.append(f"{path}: {exc.strerror}")
            continue
        key = getattr(st, "f_fsid", 0) or path
        entry = filesystems.setdefault(key, {"paths": [], "roles": [], "st": st})
        entry["paths"].append(path)
        entry["roles"].append(role)
    if not filesystems:
        return finding("storage.space", CAT, "Free space", UNKNOWN, "Free space could not be measured",
                       reason="; ".join(errors))
    worst, worst_line, ev = PASS, "", []
    rank = {PASS: 0, WARN: 1, ERROR: 2}
    for entry in filesystems.values():
        st = entry["st"]
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        if total <= 0:
            continue
        pct = 100 * free / total
        deployments = "/sysroot" in entry["paths"]
        warn_bytes = 10 * GiB if deployments else 5 * GiB
        # ERROR is absolute: below 2 GiB an update cannot stage and apps start failing.
        if free < 2 * GiB:
            sev = ERROR
        elif free < warn_bytes or pct < 5:
            sev = WARN
        else:
            sev = PASS
        line = (f"{' + '.join(entry['paths'])} ({', '.join(entry['roles'])}): "
                f"{_fmt(free)} free of {_fmt(total)} ({pct:.0f}%)")
        ev.append(line + ("" if sev == PASS else f" — {sev.upper()}"))
        if rank[sev] > rank[worst]:
            worst, worst_line = sev, line
    ev += errors
    ev.append("thresholds: WARN below 5% or 5 GiB (10 GiB where deployments live); ERROR below 2 GiB")
    if worst == PASS:
        return finding("storage.space", CAT, "Free space", PASS, "Enough free space on all checked filesystems",
                       evidence=ev)
    return finding("storage.space", CAT, "Free space", worst, "Low free space: " + worst_line.split(":")[0],
                   evidence=ev, guidance=["Staging an OS update needs room for a complete new deployment.",
                                          "Large consumers are usually ~/.local/share/Steam, Flatpak data in "
                                          "/var/lib/flatpak and old container images. Doctor deletes nothing."])
