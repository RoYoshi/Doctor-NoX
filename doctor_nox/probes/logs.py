"""Bounded, current-boot journal summaries. Never follows, never needs root."""
from __future__ import annotations

from collections import Counter, OrderedDict
import json
import os
from pathlib import Path

from ..model import INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "logs"
PARTIAL_HINT = "not seeing messages from other users and the system"
UNITS = ("fe-xicoos-auto-update.service", "fe-xicoos-upgrade.service",
         "fe-xicoos-check.service", "fe-xicoos-flatpaks.service")


def _message(entry):
    msg = entry.get("MESSAGE", "")
    if isinstance(msg, list):  # journald encodes non-UTF-8 messages as byte arrays
        try:
            msg = bytes(v for v in msg if isinstance(v, int) and 0 <= v < 256).decode("utf-8", "replace")
        except (TypeError, ValueError):
            msg = "<binary message>"
    return " ".join(str(msg).split())[:220]


def journal_present(ctx):
    """journalctl prints nothing (exit 0) when there is no journal at all; don't call that a pass."""
    for base in ("/run/log/journal", "/var/log/journal"):
        for sub in ctx.listdir(base):
            if ctx.listdir(f"{base}/{sub}") or sub.endswith(".journal"):
                return True
    return False


def access_note(ctx):
    if ctx.uid == 0:
        return "running as root"
    try:
        import grp
        names = {grp.getgrgid(g).gr_name for g in os.getgroups()} if ctx.root == Path("/") else set()
    except (KeyError, ImportError, OSError):
        names = set()
    hit = sorted(names & {"wheel", "systemd-journal", "adm"})
    return f"member of {', '.join(hit)}" if hit else "group membership not detected"


def parse_journal(text):
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        try:
            prio = int(entry.get("PRIORITY", 6))
        except (TypeError, ValueError):
            prio = 6
        ident = entry.get("SYSLOG_IDENTIFIER") or entry.get("_SYSTEMD_UNIT") or entry.get("_COMM") or "unknown"
        entries.append((prio, str(ident)[:60], _message(entry)))
    return entries


@probe("logs.boot", CAT, "Current boot errors")
def boot(ctx):
    res = ctx.runner.run("journalctl", "--boot=0", "--priority=0..3", "--no-pager", "--output=json",
                         "--lines=400")
    partial = PARTIAL_HINT in res.stderr
    if not journal_present(ctx):
        return finding("logs.boot", CAT, "Current boot errors", UNKNOWN, "No systemd journal found",
                       reason="Neither /run/log/journal nor /var/log/journal contains journal files.",
                       evidence=[res.describe()])
    if res.status not in ("ok", "exit") or (res.status == "exit" and not res.stdout.strip()):
        return finding("logs.boot", CAT, "Current boot errors", UNKNOWN, "Journal not readable",
                       reason=res.describe(),
                       guidance=["Members of the `wheel` or `systemd-journal` groups can read the system "
                                 "journal. Doctor never elevates to read it."])
    entries = parse_journal(res.stdout)
    scope = ("only your own logs are visible (no system journal access)" if partial
             else f"system + user journal ({access_note(ctx)})")
    ev = [f"query: current boot, priority emerg..err, newest 400 lines ({scope})",
          f"{len(entries)} entr{'y' if len(entries) == 1 else 'ies'}" + (", output truncated" if res.truncated else "")]
    if not entries:
        return finding("logs.boot", CAT, "Current boot errors", UNKNOWN if partial else PASS,
                       "No error-level journal entries visible this boot" if partial
                       else "No error-level journal entries this boot", evidence=ev,
                       reason="Only the user journal is visible to this account." if partial else "")
    crit = [e for e in entries if e[0] <= 2]
    groups = Counter(ident for _, ident, _ in entries)
    last = OrderedDict()
    for prio, ident, msg in entries:
        last[ident] = (prio, msg)
    for ident, count in groups.most_common(10):
        prio, msg = last[ident]
        ev.append(f"{ident} ×{count} [{'crit' if prio <= 2 else 'err'}]: {msg}")
    if crit:
        return finding("logs.boot", CAT, "Current boot errors", WARN,
                       f"{len(crit)} critical and {len(entries) - len(crit)} error entries this boot",
                       evidence=ev, guidance=["`journalctl -b -p 2` lists the critical entries.",
                                              "Many desktops log harmless firmware/driver errors; judge "
                                              "entries by whether something actually misbehaves."])
    return finding("logs.boot", CAT, "Current boot errors", INFO,
                   f"{len(entries)} error-level entries this boot (no critical entries)", evidence=ev,
                   guidance=["Error-level messages are common and often harmless. "
                             "`journalctl -b -p 3` lists them."])


@probe("logs.update_units", CAT, "FE-XicoOS service logs")
def update_units(ctx):
    if not journal_present(ctx):
        return finding("logs.update_units", CAT, "FE-XicoOS service logs", UNKNOWN, "No systemd journal found",
                       reason="Neither /run/log/journal nor /var/log/journal contains journal files.")
    ev, errors, warnings, unreadable = [], 0, 0, 0
    for unit in UNITS:
        res = ctx.runner.run("journalctl", "--boot=0", f"--unit={unit}", "--priority=0..4", "--no-pager",
                             "--output=json", "--lines=40")
        if res.status not in ("ok", "exit"):
            unreadable += 1
            ev.append(f"{unit}: {res.describe()}")
            continue
        entries = parse_journal(res.stdout)
        e = sum(p <= 3 for p, _, _ in entries)
        w = len(entries) - e
        errors, warnings = errors + e, warnings + w
        partial = PARTIAL_HINT in res.stderr
        if partial:
            unreadable += 1
        detail = f"{unit}: {e} error, {w} warning" + (" (system journal not visible)" if partial else "")
        ev.append(detail)
        for prio, _, msg in entries[-2:]:
            ev.append(f"    {'err' if prio <= 3 else 'warn'}: {msg}")
    if unreadable == len(UNITS):
        return finding("logs.update_units", CAT, "FE-XicoOS service logs", UNKNOWN,
                       "FE-XicoOS service logs are not visible to this account", evidence=ev,
                       reason="System journal access is required to read service logs.")
    if errors:
        return finding("logs.update_units", CAT, "FE-XicoOS service logs", WARN,
                       f"{errors} error entr{'y' if errors == 1 else 'ies'} from FE-XicoOS services this boot",
                       evidence=ev)
    if warnings:
        return finding("logs.update_units", CAT, "FE-XicoOS service logs", INFO,
                       f"{warnings} warning entr{'y' if warnings == 1 else 'ies'}, no errors", evidence=ev)
    return finding("logs.update_units", CAT, "FE-XicoOS service logs", PASS,
                   "No warnings or errors from FE-XicoOS services this boot", evidence=ev)
