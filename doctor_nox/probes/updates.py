"""Updates and packages: pending reboot, package database health, leftover config
files, when the system was last updated, and whether automatic updates are on.

Doctor reads state only: no package manager is asked to refresh, download,
install or repair anything, and nothing contacts a mirror.
"""
from __future__ import annotations

import datetime
import os
import re

from ..distro import KNOWN_FAMILY, Scope
from ..model import ERROR, INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "updates"
DAY = 86400
STALE_REBOOT_DAYS = 7
STALE_UPDATE_DAYS = 30

ENABLED = ("enabled", "enabled-runtime", "static", "indirect", "alias", "linked", "linked-runtime")
#: Programs that hold pacman's database lock themselves (libalpm), by process name.
ALPM_HOLDERS = frozenset({"pacman", "pamac-daemon", "packagekitd", "octphelper"})

RESTARTABLE = Scope("package-based and transactional-update systems",
                    lambda p: p.image in ("", "transactional"))
PACKAGE_FAMILIES = Scope("package-based Fedora, Debian/Ubuntu, Arch and openSUSE systems",
                         lambda p: not p.image_based and p.family in ("fedora", "debian", "arch", "suse"))
AUTO_UPDATES = Scope("Fedora (including Atomic), Debian/Ubuntu, Arch and openSUSE families (not SteamOS)",
                     lambda p: p.image != "steamos" and (p.family in ("fedora", "debian", "arch", "suse") or p.ostree))


def unit_file_state(ctx, unit, user=False):
    """systemctl is-enabled prints the state even when it exits non-zero."""
    args = (("--user",) if user else ()) + ("is-enabled", unit)
    res = ctx.runner.run("systemctl", *args)
    if res.status in ("ok", "exit"):
        word = (res.stdout.strip().splitlines() or [""])[0].strip()
        if word:
            return word, res
        if "not found" in res.stderr or "No such file" in res.stderr:
            return "not-found", res
    return "", res


def unit_show(ctx, unit, user=False):
    args = (("--user",) if user else ()) + ("show", unit, "--no-pager",
                                             "--property=LoadState,ActiveState,SubState,Result,"
                                             "UnitFileState,ExecMainStatus")
    res = ctx.runner.run("systemctl", *args)
    if not res.ok:
        return {}, res
    return dict(line.split("=", 1) for line in res.stdout.splitlines() if "=" in line), res


def _days(ctx, when: datetime.datetime | None) -> float | None:
    if when is None:
        return None
    return max(0.0, (ctx.now - when).total_seconds() / DAY)


def _ago(days: float | None) -> str:
    if days is None:
        return "at an unknown time"
    if days < 1:
        return "today"
    return f"{days:.0f} day{'s' if round(days) != 1 else ''} ago"


# ── pending reboot ───────────────────────────────────────────────────────────
MODULE_TREES = ("/usr/lib/modules", "/lib/modules")  # /lib/modules: distributions without merged /usr


def kernel_key(release: str):
    """Natural sort key for kernel releases (6.8.0-49-generic > 6.8.0-47-generic)."""
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.findall(r"\d+|[^\d]+", release)]


def kernel_flavour(release: str, family: str) -> str:
    """The build flavour, so only like kernels are compared.

    Fedora: the part after "+" (debug, 16k). Elsewhere the trailing words:
    generic, lowlatency, amd64 vs rt-amd64, default.
    """
    if family == "fedora":
        return release.partition("+")[2]
    m = re.search(r"-((?:[a-z][a-z0-9]*-)*[a-z][a-z0-9]*)$", release)
    return m.group(1) if m else ""


def installed_kernels(ctx) -> list[str]:
    """Kernel releases with a module tree (one per installed kernel)."""
    found = set()
    for tree in MODULE_TREES:
        found.update(d for d in ctx.listdir(tree) if ctx.exists(f"{tree}/{d}/modules.dep"))
    return sorted(found, key=kernel_key)


def running_modules_present(ctx) -> bool:
    return any(ctx.exists(f"{tree}/{ctx.kernel}/modules.dep") for tree in MODULE_TREES)


@probe("updates.reboot", CAT, "Pending restart", applies=RESTARTABLE)
def reboot(ctx):
    title = "Pending restart"
    p = ctx.platform
    ev, reasons, oldest = [f"running kernel: {ctx.kernel}"], [], None
    for flag in ("/run/reboot-required", "/run/reboot-needed"):
        if ctx.exists(flag):
            days = _days(ctx, ctx.mtime(flag))
            oldest = days if oldest is None or (days or 0) > oldest else oldest
            who = "apt/dpkg" if flag.endswith("required") else "zypper"
            reasons.append(f"{who} asked for a restart {_ago(days)}")
            ev.append(f"{flag}: present (since {_ago(days)})")
            pkgs = ctx.try_text("/run/reboot-required.pkgs", 16 * 1024) if flag.endswith("required") else None
            if pkgs:
                names = sorted({line.strip() for line in pkgs.splitlines() if line.strip()})
                ev.append(f"packages asking for it: {', '.join(names[:12])}" + ("…" if len(names) > 12 else ""))
    kernels = installed_kernels(ctx)
    ev.append(f"kernels with modules installed: {', '.join(kernels) or 'none found'}")
    if kernels and not running_modules_present(ctx):
        reasons.append("the running kernel's modules were removed by an update")
    # Arch keeps one version per kernel package (linux, linux-lts…), so a different
    # release there is another flavour, not an update; removal above is the signal.
    if p.family != "arch":
        flavour = kernel_flavour(ctx.kernel, p.family)
        newer = [k for k in kernels if kernel_flavour(k, p.family) == flavour
                 and kernel_key(k) > kernel_key(ctx.kernel)]
        if newer:
            reasons.append(f"a newer kernel is installed ({newer[-1]})")
    if not kernels and not reasons:
        return finding("updates.reboot", CAT, title, UNKNOWN, "Installed kernels could not be listed",
                       evidence=ev, reason="/usr/lib/modules lists no kernel with a modules.dep file.",
                       guidance=["Normal inside a container or WSL, where the kernel comes from the host."])
    if not reasons:
        return finding("updates.reboot", CAT, title, PASS, "No restart needed: the newest installed kernel is running",
                       evidence=ev)
    gone = "the running kernel's modules were removed by an update" in reasons
    stale = oldest is not None and oldest >= STALE_REBOOT_DAYS
    severity = WARN if gone or stale else INFO
    guidance = ["Restart when convenient so the update takes full effect. Doctor never restarts."]
    if gone:
        guidance.insert(0, "Until you restart, plugging in hardware that needs a new kernel module (USB "
                           "devices, file systems, VPNs) can fail, because the modules for this kernel are gone.")
    if p.family == "arch" and gone:
        guidance.append("This is normal on Arch right after `pacman -Syu` updates the kernel.")
    return finding("updates.reboot", CAT, title, severity, "Restart needed: " + reasons[0], evidence=ev,
                   guidance=guidance)


# ── package database ─────────────────────────────────────────────────────────
def _running(ctx, names) -> str:
    """The name of a running process from `names`, or "" (read from /proc; never signals anything)."""
    for pid in ctx.listdir("/proc"):
        if pid.isdigit():
            comm = (ctx.try_text(f"/proc/{pid}/comm", 64) or "").strip()
            if comm in names:
                return comm
    return ""


def _rpmdb(ctx):
    res = ctx.runner.run("rpm", "-q", "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n", "rpm")
    if res.ok:
        return PASS, "RPM database readable", [res.describe(), f"rpm: {res.stdout.strip()[:120]}"], []
    if res.status == "exit":
        return ERROR, "The RPM database could not be read", [res.describe()], [
            "Package installs and updates will fail until the database is repaired.",
            "Ask for help before acting; `sudo rpmdb --rebuilddb` is the usual repair. Doctor changes nothing."]
    return UNKNOWN, "rpm could not be run", [res.describe()], []


def _dpkg(ctx):
    ev, guidance = [], []
    res = ctx.runner.run("dpkg", "--audit")
    ev.append(res.describe())
    pending = ctx.listdir("/var/lib/dpkg/updates")
    ev.append(f"/var/lib/dpkg/updates: {len(pending)} pending record(s)")
    if res.status not in ("ok", "exit"):
        return UNKNOWN, "dpkg could not be run", ev, []
    audit = [line for line in res.stdout.splitlines() if line.strip()]
    ev += [f"  {line[:200]}" for line in audit[:12]]
    if audit or pending:
        guidance = ["An earlier install or upgrade did not finish. Until it does, apt refuses further changes.",
                    "`sudo dpkg --configure -a`, then `sudo apt --fix-broken install`, usually finishes it. "
                    "Doctor changes nothing."]
        return WARN, "Unfinished package operations", ev, guidance
    return PASS, "No broken or half-installed packages", ev, []


def _pacman(ctx):
    ev = []
    db = ctx.runner.run("pacman", "-Q", "pacman")
    ev.append(db.describe())
    if db.status not in ("ok", "exit"):
        return UNKNOWN, "pacman could not be run", ev, []
    if not db.ok:
        return ERROR, "The pacman database could not be read", ev, [
            "`sudo pacman -Dk` checks the database. Doctor changes nothing."]
    lock = ctx.exists("/var/lib/pacman/db.lck")
    busy = _running(ctx, ALPM_HOLDERS) if lock else ""
    ev.append(f"/var/lib/pacman/db.lck: {'present' if lock else 'absent'}" + (f" ({busy} running)" if busy else ""))
    orphans = ctx.runner.run("pacman", "-Qdtq")
    names = [line.strip() for line in orphans.stdout.splitlines() if line.strip()] if orphans.status in ("ok", "exit") else []
    ev.append(f"orphaned dependencies: {len(names)}" + (f" ({', '.join(names[:8])}…)" if len(names) > 8 else
                                                         f" ({', '.join(names)})" if names else ""))
    if lock and not busy:
        return WARN, "pacman's database lock is left over from an interrupted run", ev, [
            "No pacman, pamac or PackageKit is running, so the lock is stale, and every package change "
            "fails until it is gone. Close any package tool, then `sudo rm /var/lib/pacman/db.lck`. "
            "Doctor changes nothing."]
    if busy:
        return INFO, f"A package transaction is running now ({busy})", ev, []
    if names:
        return INFO, f"{len(names)} orphaned package(s) no longer needed by anything", ev, [
            "Optional cleanup: review `pacman -Qdt`, then remove what you don't want."]
    return PASS, "pacman database readable; no stale lock", ev, []


def _zypper(ctx):
    level, summary, ev, guidance = _rpmdb(ctx)
    pid = (ctx.try_text("/run/zypp.pid", 64) or "").strip()
    alive = pid.isdigit() and ctx.exists(f"/proc/{pid}")
    ev.append(f"/run/zypp.pid: {pid or 'empty or absent'}" + (" (running)" if alive else ""))
    if alive and level == PASS:
        # libzypp takes over a lock whose process is gone, so only a live holder matters.
        return INFO, f"A package operation (process {pid}) is running now", ev, [
            "Package tools wait or refuse until it finishes."]
    return level, summary, ev, guidance


@probe("updates.packages", CAT, "Package database", applies=PACKAGE_FAMILIES)
def packages(ctx):
    family = ctx.platform.family
    check = {"fedora": _rpmdb, "debian": _dpkg, "arch": _pacman, "suse": _zypper}[family]
    level, summary, ev, guidance = check(ctx)
    return finding("updates.packages", CAT, "Package database", level, summary, evidence=ev, guidance=guidance,
                   reason=ev[0] if level == UNKNOWN else "")


# ── configuration files left for review by package updates ──────────────────
LEFTOVER = {
    "fedora": (".rpmnew", ".rpmsave"),
    "suse": (".rpmnew", ".rpmsave", ".rpmorig"),
    "debian": (".dpkg-dist", ".dpkg-new", ".dpkg-old", ".ucf-dist", ".ucf-new", ".ucf-old"),
    "arch": (".pacnew", ".pacsave"),
}
MAX_VISIT = 20000


@probe("updates.config_files", CAT, "Config files to review", applies=KNOWN_FAMILY)
def config_files(ctx):
    title = "Config files to review"
    suffixes = LEFTOVER[ctx.platform.family]
    found, visited, unreadable = [], 0, 0
    stack = [("/etc", 0)]
    while stack and visited < MAX_VISIT:
        directory, depth = stack.pop()
        try:
            entries = list(os.scandir(ctx.path(directory)))
        except OSError:
            unreadable += 1
            continue
        for entry in entries:
            visited += 1
            path = f"{directory}/{entry.name}"
            if entry.name.endswith(suffixes):
                found.append(path)
            elif depth < 6 and entry.is_dir(follow_symlinks=False):
                stack.append((path, depth + 1))
    if not visited and unreadable:
        return finding("updates.config_files", CAT, title, UNKNOWN, "/etc could not be read",
                       reason="/etc is missing or not readable by this user.")
    ev = [f"looked for {', '.join(suffixes)} under /etc: {visited} entries"
          + (f", {unreadable} folder(s) not readable by this user" if unreadable else "")
          + (", stopped at the limit" if visited >= MAX_VISIT else "")]
    ev += sorted(found)[:20]
    if not found:
        return finding("updates.config_files", CAT, title, PASS, "No config files left for review by updates",
                       evidence=ev)
    tool = {"arch": "`pacdiff` (from pacman-contrib)", "fedora": "`rpmconf -a` or a diff tool",
            "debian": "a diff tool, for example `diff -u /etc/x /etc/x.dpkg-dist`", "suse": "a diff tool"}
    return finding("updates.config_files", CAT, title, INFO,
                   f"{len(found)} config file(s) left by package updates to review", evidence=ev,
                   guidance=["When an update brings a new version of a config file you changed, the package "
                             "manager keeps yours and puts the new one beside it (or the reverse). Until you "
                             "merge them, you miss the new defaults.",
                             f"Compare and merge with {tool[ctx.platform.family]}, then remove the leftover. "
                             "Doctor changes nothing."])


# ── when the system was last updated ─────────────────────────────────────────
PACMAN_STAMP = r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4})\] "


def _pacman_last_upgrade(ctx):
    """Newest `pacman -Syu` in the recent part of pacman.log; else the newest package change."""
    text = ctx.tail_text("/var/log/pacman.log", 4 * 1024 * 1024)
    if text is None:
        return None, "/var/log/pacman.log unreadable", ""
    full = re.findall(PACMAN_STAMP + r"\[PACMAN\] starting full system upgrade", text, re.M)
    if full:
        return (datetime.datetime.strptime(full[-1], "%Y-%m-%dT%H:%M:%S%z"),
                f"pacman.log: last full system upgrade started {full[-1]}", "Last full system upgrade")
    changed = re.findall(PACMAN_STAMP + r"\[ALPM\] (?:upgraded|installed|downgraded) ", text, re.M)
    if changed:
        return (datetime.datetime.strptime(changed[-1], "%Y-%m-%dT%H:%M:%S%z"),
                f"pacman.log: no full system upgrade in the recent log; last package change {changed[-1]}",
                "No full system upgrade recently; packages last changed")
    return None, "pacman.log records no upgrade", ""


def _rpm_last_install(ctx):
    res = ctx.runner.run("rpm", "-qa", "--queryformat=%{INSTALLTIME}\\n")
    if not res.ok:
        return None, res.describe(), ""
    stamps = [int(x) for x in res.stdout.split() if x.isdigit()]
    if not stamps:
        return None, "rpm reported no install times", ""
    when = datetime.datetime.fromtimestamp(max(stamps), datetime.timezone.utc)
    return when, f"newest package install time in the RPM database: {when:%Y-%m-%d %H:%M} UTC", ""


def _dpkg_last_change(ctx):
    when = ctx.mtime("/var/lib/dpkg/status")
    if when is None:
        return None, "/var/lib/dpkg/status unreadable", ""
    return when, f"/var/lib/dpkg/status last changed {when:%Y-%m-%d %H:%M} UTC", ""


UPDATE_COMMAND = {"arch": "`sudo pacman -Syu`", "debian": "`sudo apt update && sudo apt upgrade`",
                  "fedora": "`sudo dnf upgrade --refresh`",
                  "suse": "`sudo zypper dup` (Tumbleweed) or `sudo zypper up` (Leap)"}


@probe("updates.last_update", CAT, "Last update", applies=PACKAGE_FAMILIES)
def last_update(ctx):
    title = "Last update"
    family = ctx.platform.family
    reader = {"arch": _pacman_last_upgrade, "debian": _dpkg_last_change,
              "fedora": _rpm_last_install, "suse": _rpm_last_install}[family]
    when, source, what = reader(ctx)
    if when is None:
        return finding("updates.last_update", CAT, title, UNKNOWN, "When the system was last updated is unknown",
                       evidence=[source], reason=source)
    days = _days(ctx, when)
    what = what or "Packages last changed"
    ev = [source, f"WARN after {STALE_UPDATE_DAYS} days"]
    if days is not None and days >= STALE_UPDATE_DAYS:
        return finding("updates.last_update", CAT, title, WARN, f"{what} {_ago(days)}", evidence=ev,
                       guidance=["Security fixes arrive through updates; a month without them leaves known "
                                 "problems unfixed.",
                                 f"Update with your software center or {UPDATE_COMMAND[family]}."])
    return finding("updates.last_update", CAT, title, PASS, f"{what} {_ago(days)}", evidence=ev)


# ── automatic updates ────────────────────────────────────────────────────────
#: (unit, what it does when on). Only read with `systemctl is-enabled`.
AUTO_UNITS = {
    "ostree": (("rpm-ostreed-automatic.timer", "rpm-ostree"),
               ("bootc-fetch-apply-updates.timer", "bootc downloads and applies updates, then restarts the "
                                                   "computer by itself"),
               ("uupd.timer", "uupd (Universal Blue) updates the system image, Flatpaks and more"),
               ("ublue-update.timer", "ublue-update (Universal Blue) updates the system image, Flatpaks and more"),
               ("fe-xicoos-auto-update.timer", "FE-XicoOS stages updates in the background; they apply when you "
                                               "restart")),
    "fedora": (("dnf-automatic.timer", "dnf-automatic"),
               ("dnf5-automatic.timer", "dnf-automatic"),
               ("dnf-automatic-install.timer", "dnf-automatic installs updates")),
    "debian": (("apt-daily-upgrade.timer", "unattended-upgrades installs updates (by default security updates "
                                           "only)"),),
    "suse": (("transactional-update.timer", "transactional-update installs updates into a new snapshot; they "
                                            "apply when you restart"),),
    "arch": (),
}


def _ini(ctx, absolute):
    import configparser
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(ctx.try_text(absolute, 64 * 1024) or "")
    except configparser.Error:
        return None
    return parser


def _rpm_ostree_policy(ctx):
    """AutomaticUpdatePolicy from /etc/rpm-ostreed.conf: none (default), check or stage."""
    parser = _ini(ctx, "/etc/rpm-ostreed.conf")
    value = parser.get("Daemon", "AutomaticUpdatePolicy", fallback="none") if parser else "none"
    return (value or "none").strip().lower()


def _dnf_automatic(ctx):
    """apply_updates from /etc/dnf/automatic.conf (dnf4 and dnf5 both default to no)."""
    parser = _ini(ctx, "/etc/dnf/automatic.conf")
    value = parser.get("commands", "apply_updates", fallback="no") if parser else "no"
    return (value or "no").strip().lower() in ("yes", "true", "1", "on")


def _unattended_upgrades(ctx):
    """Debian/Ubuntu: APT::Periodic::Unattended-Upgrade from apt.conf.d (later files win)."""
    value = None
    source = ""
    for name in ctx.listdir("/etc/apt/apt.conf.d"):
        text = ctx.try_text(f"/etc/apt/apt.conf.d/{name}", 64 * 1024) or ""
        text = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith(("//", "#")))
        for m in re.finditer(r'APT::Periodic::Unattended-Upgrade\s+"(\d+)"\s*;', text):
            value, source = m.group(1), name
    return value, source


@probe("updates.automatic", CAT, "Automatic updates", applies=AUTO_UPDATES)
def automatic(ctx):
    title = "Automatic updates"
    p = ctx.platform
    kind = "ostree" if p.ostree else p.family
    if kind == "arch":
        return finding("updates.automatic", CAT, title, INFO, "Arch updates only when you run them",
                       evidence=["Arch-based systems have no automatic system updates by default."],
                       guidance=[f"Update regularly with {UPDATE_COMMAND['arch']}, and read the Arch news "
                                 "before large upgrades."])
    ev, enabled, unreadable = [], [], 0
    for unit, what in AUTO_UNITS[kind]:
        state, res = unit_file_state(ctx, unit)
        ev.append(f"{unit}: {state or res.describe()}")
        if not state:
            unreadable += 1
            continue
        if state not in ENABLED:
            continue
        if unit == "rpm-ostreed-automatic.timer":
            policy = _rpm_ostree_policy(ctx)
            ev.append(f"/etc/rpm-ostreed.conf AutomaticUpdatePolicy={policy}")
            if policy == "none":
                continue  # the timer runs, but the policy tells it to do nothing
            what = ("rpm-ostree downloads and stages updates; they apply when you restart" if policy == "stage"
                    else f"rpm-ostree checks for updates (policy {policy})")
        elif what == "dnf-automatic":
            applies = _dnf_automatic(ctx)
            ev.append(f"/etc/dnf/automatic.conf apply_updates={'yes' if applies else 'no'}")
            what = "dnf-automatic installs updates" if applies else "dnf-automatic downloads updates for you to install"
        enabled.append((unit, what))
    if kind == "debian":
        value, source = _unattended_upgrades(ctx)
        installed = ctx.exists("/usr/bin/unattended-upgrade")
        ev.append(f"unattended-upgrades: {'installed' if installed else 'not installed'}; "
                  f"APT::Periodic::Unattended-Upgrade={value or 'unset'}" + (f" ({source})" if source else ""))
        if not (installed and value and value != "0"):
            enabled = [e for e in enabled if e[0] != "apt-daily-upgrade.timer"]
        if p.has("snap"):
            ev.append("snaps refresh themselves (snapd)")
    if AUTO_UNITS[kind] and unreadable == len(AUTO_UNITS[kind]):
        return finding("updates.automatic", CAT, title, UNKNOWN, "Automatic update settings could not be read",
                       evidence=ev, reason="systemctl did not answer.")
    software_center = ("Desktop software centers (GNOME Software, KDE Discover) can also download updates "
                       "for you; Doctor does not read their settings.")
    if not enabled:
        return finding("updates.automatic", CAT, title, INFO, "No automatic update service is on", evidence=ev,
                       guidance=["That is a choice, not a fault: update regularly with your software center or "
                                 "package manager.", software_center])
    reboots = any(unit == "bootc-fetch-apply-updates.timer" for unit, _ in enabled)
    ev += [f"on: {unit} — {what}" for unit, what in enabled]
    summary = "Automatic updates are on" + (" and restart this computer by themselves" if reboots else "")
    guidance = ([enabled[0][1][0].upper() + enabled[0][1][1:] + "."] if len(enabled) == 1 else [])
    if reboots:
        guidance.append("bootc-fetch-apply-updates.timer restarts the computer after applying an update, even "
                        "with unsaved work open. To stop that: "
                        "`sudo systemctl disable --now bootc-fetch-apply-updates.timer`.")
    return finding("updates.automatic", CAT, title, INFO, summary, evidence=ev, guidance=guidance)
