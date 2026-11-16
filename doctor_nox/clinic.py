"""How Doctor NoX behaves like a doctor (Qt-free, unit-tested).

The words: a *checkup* runs every *test* that applies to this PC. Tests are
grouped into *areas*. Each result has a *condition* (the report's severity in a
doctor's words, see theme.STATUS_TEXT), and may have a *diagnosis* (reason),
*what I found* (evidence) and a *treatment* (guidance).

The behaviour, all of it read from a finished report:
  verdict()    what a doctor would say first, in plain sentences
  vitals()     the four vital signs shown above the results
  SYMPTOMS     "What's wrong?": the tests that bear on a complaint
  guess()      the complaint someone means when they type it in their own words
  follow_up()  what changed since the last checkup (see record.py)
  note()       the doctor's note that is copied for sharing
  pulse()      the heart monitor's trace

Doctor NoX diagnoses and prescribes. It never treats: nothing here changes
the system, and no report field is rewritten (JSON keeps its severities).
"""
from __future__ import annotations

from dataclasses import dataclass
import datetime
import re

from .distro import FAMILY_TITLE
from .model import CATEGORY_TITLE, ERROR, INFO, PASS, UNKNOWN, WARN
from .report import FULL_NOTICE, IMAGE_TEXT, SHARE_NOTICE
from .theme import HEADLINE, STATUS_TEXT, status_count

# How much care a condition asks for. INFO and PASS ask for none.
CONCERN = {ERROR: 3, WARN: 2, UNKNOWN: 1, INFO: 0, PASS: 0}
MOTTO = "Doctor NoX is in the house!"
PRESCRIBE_ONLY = "I only examine and prescribe: nothing on this PC was changed, nothing was sent anywhere."
ASK_FOR_HELP = "Copy the doctor's note and share it where you ask for help."


# ── "What's wrong?" ──────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Symptom:
    key: str
    label: str            # what the patient says
    tests: tuple = ()     # test IDs, or "area." prefixes, that bear on it; empty: every test
    words: tuple = ()     # what people type when they mean it (lower case; see guess())


GENERAL = "general"
SYMPTOMS = (
    Symptom(GENERAL, "Just a checkup"),
    Symptom("slow", "It's slow or freezes",
            ("hardware.cpu_memory", "hardware.sensors", "hardware.graphics", "storage.space",
             "security.failed_units", "system.startup", "logs.boot"),
            ("slow", "lag", "freez", "frozen", "stutter", "hang", "unresponsive", "sluggish")),
    Symptom("sound", "No sound",
            ("hardware.audio", "hardware.session", "security.failed_units", "logs.boot"),
            ("sound", "audio", "speaker", "headphone", "headset", "volume", "mute", "hear")),
    Symptom("camera", "The camera or microphone isn't working",
            ("hardware.capture", "hardware.audio", "hardware.session"),
            ("camera", "webcam", "microphone", "mic", "video call", "zoom")),
    Symptom("bluetooth", "Bluetooth isn't working",
            ("hardware.bluetooth", "security.failed_units", "logs.boot"),
            ("bluetooth", "pair", "airpods", "earbuds")),
    Symptom("battery", "The battery drains fast",
            ("hardware.battery", "hardware.cpu_memory", "hardware.sensors"),
            ("battery", "charge", "charging", "drain", "unplugged")),
    Symptom("network", "No internet", ("network.", "security.firewall"),
            ("internet", "wifi", "wi-fi", "network", "ethernet", "online", "offline", "connect", "dns", "router",
             "signal")),
    Symptom("updates", "Updates won't install",
            ("updates.", "system.image", "storage.space", "network.route", "network.dns", "software."),
            ("update", "upgrade", "patch", "outdated")),
    Symptom("graphics", "Graphics or games act up",
            ("hardware.graphics", "hardware.nvidia", "hardware.session", "hardware.controllers",
             "hardware.sensors", "software.flatpak"),
            ("graphic", "game", "gaming", "fps", "screen", "display", "gpu", "nvidia", "amd", "flicker",
             "resolution", "tearing", "steam")),
    Symptom("controller", "A controller isn't working", ("hardware.controllers", "hardware.session", "logs.boot"),
            ("controller", "gamepad", "joystick", "xbox", "dualsense", "dualshock", "joy-con")),
    Symptom("hot", "It runs hot or loud",
            ("hardware.sensors", "hardware.cpu_memory", "hardware.graphics", "hardware.nvidia"),
            ("hot", "heat", "overheat", "fan", "loud", "noisy", "temperature", "thermal")),
    Symptom("space", "Running out of space", ("storage.", "software."),
            ("space", "full", "disk", "storage", "drive")),
    Symptom("apps", "An app won't install or open",
            ("software.", "storage.space", "network.route", "network.dns", "security.mac"),
            ("app", "install", "flatpak", "snap", "program", "launch", "open")),
    Symptom("secure", "Is my PC secure?",
            ("security.", "updates.automatic", "updates.last_update", "updates.reboot"),
            ("secure", "security", "safe", "virus", "malware", "hack", "firewall", "protect")),
    Symptom("crash", "Something crashes or won't start",
            ("logs.boot", "security.failed_units", "security.mac", "storage.space", "hardware.cpu_memory"),
            ("crash", "boot", "start", "error", "broken", "fail", "black screen", "restart")),
)
SYMPTOM = {s.key: s for s in SYMPTOMS}


def related(finding_id: str, symptom: str = GENERAL) -> bool:
    """Whether a test bears on the complaint. A test's sub-results count with it."""
    tests = SYMPTOM.get(symptom, SYMPTOMS[0]).tests
    if not tests:
        return True
    return any(finding_id.startswith(t) if t.endswith(".") else finding_id == t or finding_id.startswith(t + ".")
               for t in tests)


def guess(text: str) -> str | None:
    """The complaint someone means by what they typed ("my wifi keeps dropping"), or None.

    A word counts when a word they typed starts with it ("freezes" for "freez"). The complaint
    with the most such words wins; the one listed first on a tie. Nothing is sent anywhere.
    """
    typed = re.findall(r"[a-z0-9][a-z0-9'-]*", text.lower())
    joined = " ".join(typed)
    best, best_score = None, 0
    for symptom in SYMPTOMS[1:]:
        score = sum(1 for word in symptom.words
                    if (word in joined if " " in word else any(t.startswith(word) for t in typed)))
        if score > best_score:
            best, best_score = symptom.key, score
    return best


# ── saying it ────────────────────────────────────────────────────────────────
def _names(findings, limit=3) -> str:
    """'Free space', 'Free space and Audio', 'Free space, Audio and 2 more'."""
    titles = [f["title"] for f in findings]
    if len(titles) > limit:
        titles = titles[:limit - 1] + [f"{len(titles) - limit + 1} more"]
    return titles[0] if len(titles) == 1 else ", ".join(titles[:-1]) + " and " + titles[-1]


def _by_concern(findings):
    return {sev: [f for f in findings if f["severity"] == sev] for sev in (ERROR, WARN, UNKNOWN)}


def _care(findings) -> list[str]:
    """One sentence for each condition that asks for care, most urgent first."""
    groups = _by_concern(findings)
    out = []
    if groups[ERROR]:
        n = len(groups[ERROR])
        out.append(f"{n} {'thing needs' if n == 1 else 'things need'} treatment: {_names(groups[ERROR])}.")
    if groups[WARN]:
        out.append(f"Keep an eye on {_names(groups[WARN])}.")
    first = (groups[ERROR] or groups[WARN] or [None])[0]
    if first is not None and len(groups[ERROR]) + len(groups[WARN]) > 1:
        out.append(f"Start with {first['title']}.")
    if groups[UNKNOWN]:
        out.append(f"I couldn't examine {_names(groups[UNKNOWN])}, so I can't call it healthy.")
    return out


def _worst(findings) -> str:
    groups = _by_concern(findings)
    return ERROR if groups[ERROR] else WARN if groups[WARN] else UNKNOWN if groups[UNKNOWN] else PASS


def _tests(n: int) -> str:
    return f"{n} test" + ("" if n == 1 else "s")


def verdict(report: dict, symptom: str = GENERAL) -> list[str]:
    """What the doctor says first. Short plain sentences, most important first."""
    findings = report["findings"]
    out = []
    asked = SYMPTOM.get(symptom, SYMPTOMS[0])
    focus = [f for f in findings if related(f["id"], asked.key)] if asked.tests else []
    if asked.tests and not focus:
        out.append(f"I have no tests for “{asked.label}” on this system. Here is the whole checkup.")
    if focus:
        lead = f"For “{asked.label}” I looked at {_tests(len(focus))}."
        worst = _worst(focus)
        if worst == PASS:
            out.append(f"{lead} All fine, so the cause is somewhere I can't see. {ASK_FOR_HELP}")
        else:
            out.append(lead + {ERROR: " Something there needs treatment.",
                               WARN: " Nothing is broken, but one thing bears watching.",
                               UNKNOWN: " Nothing wrong that I could see, but I couldn't examine all of it."}[worst])
            out += _care(focus)
        elsewhere = [f for f in findings if CONCERN[f["severity"]] and f not in focus]
        if elsewhere:
            n = len(elsewhere)
            out.append(f"Elsewhere: {_names(elsewhere)} {'needs' if n == 1 else 'need'} a look.")
    else:
        worst = _worst(findings)
        if not findings:
            out.append("I had no tests to run on this system.")
        elif worst == PASS:
            out.append(f"Your PC is in good health: {_tests(len(findings))}, nothing to treat.")
        else:
            out.append({ERROR: "Your PC needs treatment.",
                        WARN: "Your PC is mostly well.",
                        UNKNOWN: "Nothing wrong that I could see, but I couldn't examine everything."}[worst])
            out += _care(findings)
            fine = sum(1 for f in findings if not CONCERN[f["severity"]])
            if fine:
                out.append(f"The other {_tests(fine)} came back fine.")
    if not report["complete"]:
        out.append("This checkup was stopped early: some tests didn't finish.")
    return out


def count_summary(totals: dict) -> str:
    """'1 needs treatment, 2 to keep an eye on, 1 couldn't be examined, 4 notes and 6 healthy'."""
    items = [status_count(s, totals[s]) for s in (ERROR, WARN, UNKNOWN, INFO, PASS)]
    return ", ".join(items[:-1]) + " and " + items[-1]


# ── vital signs ──────────────────────────────────────────────────────────────
# (key, label, the tests that measure it; the first one present is used)
VITALS = (
    ("temperature", "Temperature", ("hardware.sensors",)),
    ("memory", "Memory", ("hardware.cpu_memory",)),
    ("disk", "Disk space", ("storage.space",)),
    ("restart", "Restart", ("updates.reboot", "system.image.pending")),
)
NO_VALUE = "—"
_DISK_LINE = re.compile(r"^(?P<paths>.+?) \(.*\): .+ free of .+ \((?P<pct>\d+)%\) — (?P<state>[A-Z]+)")
_STATE_RANK = {"ERROR": 2, "WARN": 1}


def _temperature(f):
    reading = re.search(r"(-?\d+) °C", f["summary"])
    if reading:
        return f"{reading.group(1)} °C"
    return "No sensor" if f["summary"].startswith("No temperature sensors") else ""


def _memory(f):
    m = (re.search(r"(?P<total>[\d.]+ GiB) RAM \((?P<free>[\d.]+ GiB) available\)", f["summary"])
         or re.search(r"(?P<free>[\d.]+ GiB) of (?P<total>[\d.]+ GiB) available", f["summary"]))
    return f"{m.group('free')} free of {m.group('total')}" if m else ""


def _disk(f):
    """The filesystem that is worst off: the most urgent state, then the least room."""
    lines = [m for m in map(_DISK_LINE.match, f["evidence"]) if m]
    if not lines:
        return ""
    tight = min(lines, key=lambda m: (-_STATE_RANK.get(m.group("state"), 0), int(m.group("pct"))))
    return f"{tight.group('pct')}% free on {tight.group('paths').split(' + ')[0]}"


def _restart(f):
    text = f["summary"]
    for start, value in (("No restart needed", "Not needed"), ("Nothing staged", "Not needed"),
                         ("Download-only", "Not needed"), ("Restart needed", "Needed"),
                         ("Update staged", "Update waiting"), ("Rollback queued", "Rollback waiting")):
        if text.startswith(start):
            return value
    return ""


_READ = {"temperature": _temperature, "memory": _memory, "disk": _disk, "restart": _restart}


def vitals(report: dict | None) -> list[dict]:
    """Four vital signs: label, a short value, the condition, and the test to open for more."""
    out = []
    by_id = {f["id"]: f for f in report["findings"]} if report else {}
    for key, label, tests in VITALS:
        finding = next((by_id[t] for t in tests if t in by_id), None)
        if finding is None:
            out.append(dict(key=key, label=label, value=NO_VALUE if not report else "Not examined", severity=None,
                            finding_id="", detail="" if not report else "No test in this checkup measured it."))
            continue
        if finding["severity"] == UNKNOWN:
            value = "Couldn't read"
        else:
            # A summary the reader doesn't know is shown as it is rather than guessed at.
            value = _READ[key](finding) or finding["summary"]
        out.append(dict(key=key, label=label, value=value, severity=finding["severity"],
                        finding_id=finding["id"], detail=f"{finding['title']}: {finding['summary']}"))
    return out


# ── follow-ups ───────────────────────────────────────────────────────────────
BETTER, WORSE, UNSEEN = "better", "worse", "unseen"
CHANGE_TEXT = {BETTER: "Better", WORSE: "Worse", UNSEEN: ""}


def follow_up(report: dict, previous: dict | None) -> dict | None:
    """What changed since the remembered checkup (record.snapshot), or None without one.

    Returns {"when": ISO time, "changes": {test ID: (BETTER|WORSE|UNSEEN, condition before)}}.
    A test that can't be examined this time is UNSEEN, never "better".
    """
    if not previous or previous.get("generated_at") == report["generated_at"]:
        return None
    changes = {}
    for f in report["findings"]:
        before, now = previous["findings"].get(f["id"]), f["severity"]
        if before is None or before == now:
            continue
        if now == UNKNOWN:
            changes[f["id"]] = (UNSEEN, before)
        elif before == UNKNOWN:
            if CONCERN[now] > CONCERN[UNKNOWN]:
                changes[f["id"]] = (WORSE, before)
            # examined and fine this time: good, but nothing got better on the PC
        elif CONCERN[now] != CONCERN[before]:
            changes[f["id"]] = (WORSE if CONCERN[now] > CONCERN[before] else BETTER, before)
    return {"when": previous["generated_at"], "changes": changes}


def when_text(iso: str, now: datetime.datetime | None = None) -> str:
    """'earlier today', 'yesterday', '4 days ago (12 Nov)'. Empty for a time that can't be read.

    Days are counted on the calendar of now's time zone (the local one when now is not given).
    """
    now = now or datetime.datetime.now().astimezone()
    try:
        then = datetime.datetime.fromisoformat(iso)
    except ValueError:
        return ""
    if then.tzinfo is None:
        then = then.replace(tzinfo=datetime.timezone.utc)
    then = then.astimezone(now.tzinfo)
    days = (now.date() - then.date()).days
    if days <= 0:
        return "earlier today"
    if days == 1:
        return "yesterday"
    return f"{days} days ago ({then.day} {then:%b}" + (f" {then.year})" if then.year != now.year else ")")


def follow_up_text(report: dict, follow: dict | None, now: datetime.datetime | None = None) -> list[str]:
    """The follow-up in sentences. Empty when there is no earlier checkup."""
    if follow is None:
        return []
    when = when_text(follow["when"], now)
    since = f"since your last checkup {when}" if when else "since your last checkup"
    title = {f["id"]: f for f in report["findings"]}
    groups = {kind: [(title[i], before) for i, (k, before) in follow["changes"].items() if k == kind]
              for kind in (WORSE, BETTER, UNSEEN)}
    if not any(groups.values()):
        return [f"Nothing has changed {since}."]

    def listed(items):
        return "; ".join(f"{f['title']} (was {STATUS_TEXT[before]}, now {STATUS_TEXT[f['severity']]})"
                         for f, before in items)

    out = []
    if groups[WORSE]:
        out.append(f"Worse {since}: {listed(groups[WORSE])}.")
    if groups[BETTER]:
        out.append((f"Better {since}: " if not out else "Better: ") + listed(groups[BETTER]) + ".")
    if groups[UNSEEN]:
        out.append(f"I couldn't examine {_names([f for f, _ in groups[UNSEEN]])} this time.")
    return out


# ── the doctor's note ────────────────────────────────────────────────────────
NOTE_HEADING = {ERROR: "NEEDS TREATMENT", WARN: "KEEP AN EYE ON", UNKNOWN: "COULDN'T EXAMINE", INFO: "NOTES"}


def note(report: dict, follow: dict | None = None, now: datetime.datetime | None = None) -> str:
    """Plain text for the clipboard: the verdict, then everything that isn't simply healthy."""
    h = report["host"]
    patient = " · ".join(bit for bit in (
        h.get("name") or "unknown Linux", FAMILY_TITLE.get(h.get("family", ""), ""),
        IMAGE_TEXT.get(h.get("image", ""), ""), f"kernel {h.get('kernel') or '?'}") if bit)
    out = [f"Doctor NoX {report['doctor_version']}  //  doctor's note  //  schema {report['schema_version']}",
           SHARE_NOTICE if report.get("redacted") else FULL_NOTICE,
           f"Checkup:  {report['generated_at']}  ({report['duration_ms']} ms"
           + ("" if report["complete"] else ", stopped early") + ")",
           f"Patient:  {patient}",
           f"Verdict:  {HEADLINE[report['overall']]} ({count_summary(report['totals'])})",
           ""]
    out += verdict(report) + follow_up_text(report, follow, now)
    for sev in (ERROR, WARN, UNKNOWN, INFO):
        items = [f for f in report["findings"] if f["severity"] == sev]
        if not items:
            continue
        out += ["", NOTE_HEADING[sev]]
        for f in items:
            out.append(f"- {CATEGORY_TITLE[f['category']]} / {f['title']}: {f['summary']}")
            if f["reason"]:
                out.append(f"    Diagnosis: {f['reason']}")
            out += [f"    Treatment: {line}" for line in f["guidance"]]
    skipped = report.get("not_applicable") or []
    if skipped:
        out += ["", f"{_tests(len(skipped))} for other kinds of Linux did not apply here and "
                    f"{'was' if len(skipped) == 1 else 'were'} not run."]
    out += ["", PRESCRIBE_ONLY]
    return "\n".join(out) + "\n"


# ── the heart monitor ────────────────────────────────────────────────────────
# One heartbeat as (x, y): x runs 0..1 along the beat, y is 0 on the baseline and 1 at the spike.
BEAT = ((0.00, 0.0), (0.14, 0.0), (0.19, 0.12), (0.24, 0.0), (0.33, 0.0), (0.36, -0.14), (0.40, 1.0),
        (0.44, -0.30), (0.48, 0.0), (0.60, 0.0), (0.69, 0.24), (0.78, 0.0), (1.00, 0.0))
BEAT_WIDTH = 2.6  # one beat is this many times as wide as the trace is tall


def pulse(width: float, height: float) -> list[tuple[float, float]]:
    """The monitor's trace for a strip of this size: whole beats side by side, in pixels.

    The baseline sits at 62% of the height, so the spike has room above it and the dips below.
    """
    if width <= 0 or height <= 0:
        return []
    beats = max(1, round(width / (height * BEAT_WIDTH)))
    span = width / beats
    base, up, down = height * 0.62, height * 0.54, height * 0.30
    points = []
    for beat in range(beats):
        for x, y in BEAT if beat == 0 else BEAT[1:]:
            points.append((round((beat + x) * span, 2), round(base - (y * up if y >= 0 else y * down), 2)))
    return points


def pulse_y(points, x: float) -> float:
    """The trace's height at x, for the dot at the head of the sweep."""
    if not points:
        return 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            return y0 if x1 == x0 else y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return points[0][1] if x < points[0][0] else points[-1][1]


# ── treatment: the commands in it, and what they do ──────────────────────────
# (how the command starts once "sudo" is taken off, what it does, whether it changes the system)
COMMANDS = (
    ("journalctl --disk-usage", "Shows how much space the logs take.", False),
    ("journalctl", "Shows log messages.", False),
    ("systemctl status", "Shows whether a service is running, and its last messages.", False),
    ("systemctl --user status", "Shows whether one of your own services is running.", False),
    ("systemctl is-enabled", "Shows whether a service starts by itself.", False),
    ("systemctl disable", "Stops a service from starting by itself.", True),
    ("systemd-analyze", "Shows what starting up spent its time on.", False),
    ("rpm-ostree status", "Shows the installed system images.", False),
    ("bootc status", "Shows the installed system images.", False),
    ("rpm-ostree rollback", "Makes the previous system image the one that starts next.", True),
    ("bootc rollback", "Makes the previous system image the one that starts next.", True),
    ("rpm-ostree usroverlay", "Makes /usr writable until the next restart.", True),
    ("bootc usr-overlay", "Makes /usr writable until the next restart.", True),
    ("ostree admin pin", "Keeps a system image from being removed.", True),
    ("pacman -Qdt", "Lists packages nothing needs any more.", False),
    ("pacman -Dk", "Checks the package database for missing dependencies.", False),
    ("pacman -Syu", "Updates every package.", True),
    ("paccache", "Removes old downloaded packages.", True),
    ("pacdiff", "Walks you through config files left by updates.", True),
    ("rpmconf", "Walks you through config files left by updates.", True),
    ("diff", "Shows the difference between two files.", False),
    ("dnf upgrade", "Updates every package.", True),
    ("dnf install", "Installs a package.", True),
    ("dnf clean", "Removes downloaded package files.", True),
    ("dnf autoremove", "Removes packages nothing needs any more.", True),
    ("apt update", "Updates every package.", True),
    ("apt install", "Installs a package.", True),
    ("apt clean", "Removes downloaded package files.", True),
    ("apt autoremove", "Removes packages nothing needs any more.", True),
    ("apt --fix-broken", "Finishes or undoes a half-done package install.", True),
    ("dpkg --configure", "Finishes setting up packages that were interrupted.", True),
    ("zypper up", "Updates every package.", True),
    ("zypper dup", "Upgrades the whole distribution.", True),
    ("zypper clean", "Removes downloaded package files.", True),
    ("snapper list", "Lists the snapshots that are kept.", False),
    ("rpmdb --rebuilddb", "Rebuilds the package database from the installed packages.", True),
    ("snap revert", "Puts a snap back to its previous version.", True),
    ("flatpak override", "Changes what an app is allowed to reach.", True),
    ("steamos-readonly", "Makes SteamOS's system partition writable.", True),
    ("rm ", "Deletes a file.", True),
    ("ln ", "Replaces a file with a link.", True),
    ("doctor-nox", "Runs Doctor NoX from the terminal.", False),
)


def explain(command: str) -> str | None:
    """One plain line on a command from a treatment, or None when it is not a command Doctor NoX knows."""
    text = command.strip()
    admin = text.startswith("sudo ")
    bare = text[5:].lstrip() if admin else text
    for start, does, changes in COMMANDS:
        if bare == start.strip() or bare.startswith(start if start.endswith(" ") else start + " "):
            if not changes:
                return f"{does} Changes nothing."
            return f"{does} This changes your system{', as administrator' if admin else ''}: read it first."
    return None


def commands(finding: dict) -> list[str]:
    """The commands a finding's treatment mentions, in order, each once. Kernel options, group
    names and the like are in back-ticks too, and are left out."""
    found = []
    for line in finding["guidance"]:
        for text in re.findall(r"`([^`]+)`", line):
            if explain(text) and text not in found:
                found.append(text)
    return found


# ── checking one test again ──────────────────────────────────────────────────
def probe_for(finding_id: str, probe_ids) -> str | None:
    """The test a finding comes from: itself, or the test it is a sub-result of."""
    owners = [p for p in probe_ids if finding_id == p or finding_id.startswith(p + ".")]
    return max(owners, key=len) if owners else None


def merge_recheck(report: dict, again: dict, probe_id: str) -> tuple[dict, list[tuple[str, str, str]]]:
    """The report with one test's results replaced by a fresh run of it.

    Returns the new report and (title, condition before, condition now) for each of its results.
    The checkup's own time stays: the rest of it is still from then.
    """
    def owned(f):
        return f["id"] == probe_id or f["id"].startswith(probe_id + ".")
    before = {f["id"]: f["severity"] for f in report["findings"] if owned(f)}
    fresh = [f for f in again["findings"] if owned(f)]
    kept, placed = [], False
    for f in report["findings"]:
        if not owned(f):
            kept.append(f)
        elif not placed:
            kept += fresh
            placed = True
    if not placed:
        kept += fresh
    merged = dict(report, findings=kept)
    merged["totals"] = {sev: sum(1 for f in kept if f["severity"] == sev) for sev in report["totals"]}
    merged["overall"] = _worst(kept) if kept else UNKNOWN
    changes = [(f["title"], before.get(f["id"], ""), f["severity"]) for f in fresh]
    return merged, changes


def recheck_text(changes) -> str:
    """'Free space: Healthy now (was Needs treatment).' / 'Free space: still Needs treatment.'"""
    parts = []
    for title, before, now in changes:
        if before and before != now:
            parts.append(f"{title}: {STATUS_TEXT[now]} now (was {STATUS_TEXT[before]}).")
        else:
            parts.append(f"{title}: still {STATUS_TEXT[now]}.")
    return " ".join(parts) or "That test has nothing to report now."


# ── the chart: figures kept from each checkup, and what they point to ────────
_SIZE = {"MiB": 1 / 1024, "GiB": 1.0, "TiB": 1024.0}
_DISK_SIZES = re.compile(r"^(?P<paths>.+?) \(.*\): (?P<free>[\d.]+) (?P<fu>MiB|GiB|TiB) free of "
                         r"(?P<total>[\d.]+) (?P<tu>MiB|GiB|TiB) ")


def metrics(report: dict) -> dict:
    """The figures worth following from one checkup to the next: free space per filesystem (GiB)
    and the hottest sensor (°C). Only what the tests reported; nothing is measured here."""
    by_id = {f["id"]: f for f in report["findings"]}
    out = {}
    disks = {}
    for line in by_id.get("storage.space", {}).get("evidence", []):
        m = _DISK_SIZES.match(line)
        if m:
            disks[m.group("paths").split(" + ")[0]] = [round(float(m.group("free")) * _SIZE[m.group("fu")], 3),
                                                       round(float(m.group("total")) * _SIZE[m.group("tu")], 3)]
    if disks:
        out["disk"] = disks
    heat = re.search(r"(-?\d+) °C", by_id.get("hardware.sensors", {}).get("summary", ""))
    if heat:
        out["temperature"] = int(heat.group(1))
    return out


def _days(iso_a: str, iso_b: str) -> float:
    a, b = datetime.datetime.fromisoformat(iso_a), datetime.datetime.fromisoformat(iso_b)
    return (b - a).total_seconds() / 86400


def _span(days: float) -> str:
    if days < 14:
        return f"{max(1, round(days))} day" + ("" if round(days) <= 1 else "s")
    if days < 70:
        return f"about {round(days / 7)} weeks"
    return f"about {round(days / 30)} months"


def prognosis(history: list[dict]) -> list[str]:
    """Where things are heading, from the checkups on record (oldest first, the current one last).

    Says something only when the record supports it: free space that keeps falling and would
    run out within half a year, or a PC running clearly hotter than it has been.
    """
    usable = []
    for entry in history:
        try:
            datetime.datetime.fromisoformat(entry["generated_at"])
            usable.append(entry)
        except (KeyError, TypeError, ValueError):
            continue
    if len(usable) < 3:
        return []
    out = []
    now = usable[-1]
    for path, (free, total) in sorted(now.get("metrics", {}).get("disk", {}).items()):
        points = [(_days(e["generated_at"], now["generated_at"]), e["metrics"]["disk"][path][0]) for e in usable
                  if path in e.get("metrics", {}).get("disk", {}) and _days(e["generated_at"], now["generated_at"]) <= 60]
        if len(points) < 3 or points[0][0] < 3:
            continue  # fewer than three checkups, or less than three days of them
        n = len(points)
        mean_x, mean_y = sum(-x for x, _ in points) / n, sum(y for _, y in points) / n
        spread = sum((-x - mean_x) ** 2 for x, _ in points)
        slope = sum((-x - mean_x) * (y - mean_y) for x, y in points) / spread if spread else 0.0  # GiB per day
        lost = points[0][1] - free
        if slope >= 0 or lost < max(1.0, total * 0.01):
            continue
        left = free / -slope
        if left <= 183:
            out.append(f"{path} has lost {lost:.0f} GiB in {_span(points[0][0])}. "
                       f"At this rate it is full in {_span(left)}.")
    earlier = [e["metrics"]["temperature"] for e in usable[:-1] if "temperature" in e.get("metrics", {})][-10:]
    heat = now.get("metrics", {}).get("temperature")
    if heat is not None and len(earlier) >= 3:
        usual = sum(earlier) / len(earlier)
        if heat - usual >= 12:
            out.append(f"Running hotter than usual: {heat} °C now, {usual:.0f} °C over the last "
                       f"{len(earlier)} checkups.")
    return out
