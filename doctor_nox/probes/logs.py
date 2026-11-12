"""Bounded, current-boot journal summaries. Never follows, never needs root."""
from __future__ import annotations

from collections import Counter, OrderedDict
import json
import os
from pathlib import Path

from ..distro import SYSTEMD
from ..model import INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "logs"
PARTIAL_HINT = "not seeing messages from other users and the system"


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


@probe("logs.boot", CAT, "Current boot errors", applies=SYSTEMD)
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
                       guidance=["Members of the `systemd-journal`, `adm` or `wheel` groups can read the "
                                 "system journal. Doctor never elevates to read it."])
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
