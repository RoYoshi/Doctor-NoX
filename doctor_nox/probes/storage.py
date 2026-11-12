"""Mount layout and free space, read from /proc/self/mountinfo and statvfs.

What "healthy" means depends on the kind of system:
  package-based   /, /usr, /etc and /var are writable
  OSTree / bootc  / and /usr are read-only by design (composefs / OSTree);
                  /etc, /var and the home directory (on /var/home) are writable
  SteamOS         the system partition is read-only; /etc, /var and /home are writable
  transactional   the root snapshot is read-only; /etc, /var and /home are writable
"""
from __future__ import annotations

import os
import re

from ..model import ERROR, INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "storage"
MiB = 1024 ** 2
GiB = 1024 ** 3
#: Filesystems whose free space says nothing about the disk (image layers, live media).
IMAGE_FS = {"composefs", "erofs", "squashfs", "iso9660"}
LAYOUT_TEXT = {
    "": "/, /etc and /var are writable",
    "ostree": "/usr read-only by design; /etc and /var writable",
    "steamos": "System partition read-only by design; /etc and /var writable",
    "transactional": "Root snapshot read-only by design; /etc and /var writable",
}


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


def _mounts(ctx):
    text = ctx.try_text("/proc/self/mountinfo", 2 * 1024 * 1024)
    return parse_mountinfo(text) if text else None


@probe("storage.layout", CAT, "Filesystem layout")
def layout(ctx):
    mounts = _mounts(ctx)
    if mounts is None:
        return finding("storage.layout", CAT, "Filesystem layout", UNKNOWN, "Mount table unavailable",
                       reason="/proc/self/mountinfo unreadable")
    image = ctx.platform.image
    out = []

    def state(path):
        point = covering(mounts, path)
        if point is None:
            return None, None
        fstype, _source, ro = mounts[point]
        return point, (fstype, ro)

    ev = []
    for path in ("/", "/usr", "/etc", "/var") + (("/sysroot",) if image == "ostree" else ()):
        point, info = state(path)
        if info:
            ev.append(f"{path} → mount {point} ({info[0]}, {'read-only' if info[1] else 'read-write'})")
    _, root = state("/")
    _, usr = state("/usr")
    _, etc = state("/etc")
    _, var = state("/var")
    problems = []
    if var and var[1]:
        problems.append("/var is read-only: logs, caches, Flatpaks and services cannot write")
    if etc and etc[1]:
        problems.append("/etc is read-only: configuration changes cannot be saved")
    if not image and root and root[1]:
        problems.append("The root filesystem is read-only: updates and most programs cannot write")
    if problems:
        out.append(finding("storage.layout", CAT, "Filesystem layout", ERROR, problems[0], evidence=ev,
                           guidance=["An unexpected read-only filesystem usually means the kernel remounted it "
                                     "after an error. `journalctl -b -k | grep -iE 'error|remount'` shows why.",
                                     "Back up important data before restarting."]))
    elif root is None or var is None:
        out.append(finding("storage.layout", CAT, "Filesystem layout", UNKNOWN, "Mount layout not recognized",
                           evidence=ev))
    elif not image and usr and usr[1]:
        out.append(finding("storage.layout", CAT, "Filesystem layout", WARN,
                           "/usr is mounted read-only, so package updates cannot install", evidence=ev,
                           guidance=["Fine if you mount /usr read-only on purpose and remount it for updates. "
                                     "Otherwise check the kernel log for filesystem errors."]))
    elif image == "ostree" and usr and not usr[1]:
        overlay = usr[0] == "overlay"
        out.append(finding("storage.layout", CAT, "Filesystem layout", INFO,
                           "/usr is writable (temporary overlay; changes vanish at restart)" if overlay
                           else "/usr is writable, which is unusual on an image-based system", evidence=ev,
                           guidance=["`bootc usr-overlay` or `rpm-ostree usroverlay` creates a temporary writable "
                                     "/usr for debugging. Lasting changes belong in the image or as layered "
                                     "packages."]))
    elif image in ("steamos", "transactional") and root and not root[1]:
        what = ("SteamOS read-only mode is off (`steamos-readonly disable`)" if image == "steamos"
                else "The root snapshot is writable, which is unusual on a transactional system")
        out.append(finding("storage.layout", CAT, "Filesystem layout", INFO, what, evidence=ev,
                           guidance=["Changes made directly to the system partition are lost or overwritten at "
                                     "the next system update."]))
    else:
        out.append(finding("storage.layout", CAT, "Filesystem layout", PASS, LAYOUT_TEXT[image], evidence=ev))

    home = _real_home(ctx)
    point, info = state(home) if home else (None, None)
    if not home or not info:
        out.append(finding("storage.home", CAT, "Home directory", UNKNOWN, "Home directory location unknown",
                           evidence=[f"home: {home or 'unset'}"]))
        return out
    hev = [f"home resolves to {home}", f"mount {point} ({info[0]}, {'read-only' if info[1] else 'read-write'})"]
    if info[1]:
        out.append(finding("storage.home", CAT, "Home directory", ERROR, "Home directory is read-only",
                           evidence=hev, guidance=["Programs cannot save settings or files. "
                                                   "`journalctl -b -k | grep -iE 'error|remount'` shows why."]))
    elif info[0] in ("tmpfs", "ramfs"):
        out.append(finding("storage.home", CAT, "Home directory", WARN,
                           "Home is in memory (tmpfs): its files are lost at shutdown", evidence=hev,
                           guidance=["Expected in a live session. On an installed system, home belongs on a "
                                     "disk."]))
    elif image == "ostree" and not (home.startswith("/var/") or point not in ("/", "/usr")):
        out.append(finding("storage.home", CAT, "Home directory", WARN,
                           "Home is not on persistent /var storage", evidence=hev,
                           guidance=["On image-based systems /home normally links to /var/home, so data survives "
                                     "updates and rollbacks."]))
    else:
        out.append(finding("storage.home", CAT, "Home directory", PASS,
                           f"Home on persistent writable storage ({point})", evidence=hev))
    return out


# ── free space ───────────────────────────────────────────────────────────────
def _fmt(n):
    return f"{n / GiB:.1f} GiB" if n >= GiB else f"{n / MiB:.0f} MiB"


def _targets(ctx, mounts):
    image = ctx.platform.image
    if image == "ostree":
        targets = [("/sysroot", "OS deployments"), ("/var", "system data, Flatpaks, logs")]
    elif image == "steamos":
        targets = [("/var", "system data, logs")]  # the system partition is full by design
    else:
        targets = [("/", "system"), ("/var", "system data, caches, logs")]
    home = _real_home(ctx)
    if home:
        targets.append((home, "your home"))
    if mounts and "/boot" in mounts:
        targets.append(("/boot", "kernels"))
    return targets


def judge(path, total, free, deployments=False):
    """(severity, rule text) for one filesystem."""
    if path == "/boot":
        rule = "WARN below 200 MiB, ERROR below 80 MiB"
        return (ERROR if free < 80 * MiB else WARN if free < 200 * MiB else PASS), rule
    if total < 8 * GiB:  # a small special-purpose filesystem: judge by share, not size
        rule = "small filesystem: WARN below 10%, ERROR below 3%"
        return (ERROR if free < 0.03 * total else WARN if free < 0.10 * total else PASS), rule
    floor = 10 * GiB if deployments else 5 * GiB
    warn_at = max(floor, min(0.05 * total, 20 * GiB))
    rule = f"WARN below {_fmt(warn_at)}, ERROR below 2.0 GiB"
    return (ERROR if free < 2 * GiB else WARN if free < warn_at else PASS), rule


CLEANUP = {
    "arch": "the package cache (`paccache -r` from pacman-contrib keeps the last 3 versions)",
    "debian": "the package cache and old kernels (`sudo apt clean`, `sudo apt autoremove`)",
    "fedora": "old kernels and the package cache (`sudo dnf autoremove`, `sudo dnf clean packages`)",
    "suse": "old snapshots (`sudo snapper list`) and the package cache (`sudo zypper clean`)",
}


@probe("storage.space", CAT, "Free space")
def space(ctx):
    mounts = _mounts(ctx)
    filesystems, errors, skipped = {}, [], []
    for path, role in _targets(ctx, mounts):
        point = covering(mounts, path) if mounts else None
        fstype, source = (mounts[point][0], mounts[point][1]) if point else ("", "")
        if fstype in IMAGE_FS:
            skipped.append(f"{path}: {fstype} image layer, free space not meaningful")
            continue
        try:
            st = ctx.statvfs(path)
        except OSError as exc:
            errors.append(f"{path}: {exc.strerror}")
            continue
        # One entry per real filesystem: btrfs subvolumes and OSTree bind mounts share a device.
        key = source if source.startswith("/") else (f"{fstype}:{point}" if point else getattr(st, "f_fsid", 0) or path)
        entry = filesystems.setdefault(key, {"paths": [], "roles": [], "st": st, "fstype": fstype})
        entry["paths"].append(path)
        entry["roles"].append(role)
    if not filesystems:
        return finding("storage.space", CAT, "Free space", UNKNOWN, "Free space could not be measured",
                       reason="; ".join(errors + skipped) or "nothing to measure")
    worst, worst_paths, ev = PASS, "", []
    rank = {PASS: 0, WARN: 1, ERROR: 2}
    for entry in filesystems.values():
        st = entry["st"]
        total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
        if total <= 0:
            continue
        paths = entry["paths"]
        sev, rule = judge("/boot" if paths == ["/boot"] else paths[0], total, free, "/sysroot" in paths)
        kind = f" {entry['fstype']}" if entry["fstype"] else ""
        ev.append(f"{' + '.join(paths)} ({', '.join(entry['roles'])};{kind}): {_fmt(free)} free of {_fmt(total)} "
                  f"({100 * free / total:.0f}%) — {sev.upper()} [{rule}]")
        if rank[sev] > rank[worst]:
            worst, worst_paths = sev, " + ".join(paths)
    ev += errors + skipped
    if worst == PASS:
        return finding("storage.space", CAT, "Free space", PASS, "Enough free space on all checked filesystems",
                       evidence=ev)
    guidance = ["Updates need room to download and unpack; when space runs out they fail part-way."]
    if worst_paths == "/boot":
        guidance.append("A full /boot stops kernel updates. Removing old kernels with your package manager frees "
                        "it.")
    family = ctx.platform.family
    if family in CLEANUP and not ctx.platform.image_based:
        guidance.append(f"Places to look: {CLEANUP[family]}, the journal (`journalctl --disk-usage`), "
                        "~/.local/share/Steam and Flatpak data.")
    else:
        guidance.append("Places to look: ~/.local/share/Steam, Flatpak data in /var/lib/flatpak, old container "
                        "images and the journal (`journalctl --disk-usage`).")
    guidance.append("Doctor deletes nothing.")
    return finding("storage.space", CAT, "Free space", worst, "Low free space: " + worst_paths, evidence=ev,
                   guidance=guidance)
