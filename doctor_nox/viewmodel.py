"""Qt-free presentation logic: grouping, filtering, ordering and detail HTML.

Kept separate from gui.py so it is unit-tested without a display or PySide6.
What the doctor says (verdict, vital signs, symptoms, follow-ups) is clinic.py;
this module lays it out.
"""
from __future__ import annotations

import html

from . import clinic
from .clinic import count_summary  # noqa: F401  (part of this module's interface)
from .model import CATEGORIES, CATEGORY_TITLE, ERROR, INFO, PASS, SEVERITIES, UNKNOWN, WARN
from .report import IMAGE_TEXT
from .theme import DARK_THEME, HEADLINE, STATUS_MEANING, STATUS_TEXT, Theme, status_count

OVERVIEW = "overview"
RANK = {sev: i for i, sev in enumerate(SEVERITIES)}  # error first

# Other ways to say a condition in the search box: is:healthy, is:treat, is:watch…
IS_WORDS = {"err": ERROR, "treat": ERROR, "treatment": ERROR, "warning": WARN, "watch": WARN,
            "?": UNKNOWN, "unseen": UNKNOWN, "note": INFO, "notes": INFO, "healthy": PASS}


def category_rows(report):
    """[(key, title, total, counts, worst)] with OVERVIEW first. worst=None when empty."""
    rows = []
    findings = report["findings"] if report else []
    for key, title in ((OVERVIEW, "Diagnosis"), *CATEGORIES):
        items = findings if key == OVERVIEW else [f for f in findings if f["category"] == key]
        counts = {s: sum(f["severity"] == s for f in items) for s in SEVERITIES}
        worst = min((f["severity"] for f in items), key=RANK.get) if items else None
        rows.append((key, title, len(items), counts, worst))
    return rows


def category_badge(counts, total):
    """(text, severity) for the sidebar pill: the number of problems coloured by the worst one,
    "OK" when every check in the group verified fine, nothing for an empty group."""
    if not total:
        return "", None
    problems = counts[ERROR] + counts[WARN] + counts[UNKNOWN]
    if problems:
        worst = ERROR if counts[ERROR] else WARN if counts[WARN] else UNKNOWN
        return str(problems), worst
    return "OK", PASS


def category_accessible(title, counts, total):
    if not total:
        return f"{title}: no tests"
    parts = [status_count(s, counts[s]) for s in (ERROR, WARN, UNKNOWN) if counts[s]]
    tests = f"{total} test" + ("" if total == 1 else "s")
    return f"{title}: {tests}" + (", " + ", ".join(parts) if parts else ", all fine")


def matches(finding, query):
    """Every whitespace-separated term must match. `is:warn` / `is:problem` filter by severity."""
    terms = query.lower().split()
    if not terms:
        return True
    hay = " ".join([finding["id"], finding["title"], finding["summary"], finding["reason"],
                    CATEGORY_TITLE.get(finding["category"], ""), *finding["evidence"],
                    *finding["guidance"]]).lower()
    for term in terms:
        if term.startswith("is:"):
            want = term[3:]
            if want in ("problem", "problems"):
                if finding["severity"] not in (ERROR, WARN, UNKNOWN):
                    return False
            elif want in ("ok", "pass"):
                if finding["severity"] != PASS:
                    return False
            elif finding["severity"] != IS_WORDS.get(want, want):
                return False
        elif term not in hay:
            return False
    return True


def visible(report, category=OVERVIEW, query="", severities=frozenset(SEVERITIES), symptom=clinic.GENERAL):
    if not report:
        return []
    items = [f for f in report["findings"]
             if (category == OVERVIEW or f["category"] == category)
             and f["severity"] in severities and matches(f, query) and clinic.related(f["id"], symptom)]
    order = {key: i for i, (key, _) in enumerate(CATEGORIES)}
    return sorted(items, key=lambda f: (RANK[f["severity"]], order[f["category"]]))


def header_text(report):
    if not report:
        return "No checkup yet", ""
    h, t = report["host"], report["totals"]
    host = ", ".join(x for x in (h.get("name", ""), IMAGE_TEXT.get(h.get("image", ""), ""),
                                 f"kernel {h.get('kernel', '')}") if x)
    return host, count_summary(t)


def duration_text(ms):
    """How long a checkup took, for people: '0.4 s', '1.8 s', '12 s'."""
    seconds = max(0, ms) / 1000
    return f"{seconds:.1f} s" if seconds < 10 else f"{seconds:.0f} s"


def empty_state(report, scanning, category, query, severities, symptom=clinic.GENERAL):
    if scanning and not report:
        return "Examining… I'll have the findings when every test has finished."
    if not report:
        return "No checkup yet.\nPress Start checkup (F5). I only look: nothing on this PC is changed."
    if not visible(report, category, query, severities, symptom):
        if query or severities != frozenset(SEVERITIES):
            return "No tests match the search or the conditions shown.\nClear the search or turn a condition back on."
        if symptom != clinic.GENERAL and visible(report, category):
            return (f"No tests for “{clinic.SYMPTOM[symptom].label}” in this area.\n"
                    "Choose Diagnosis on the left, or Just a checkup, to see more.")
        return "No tests in this area."
    return ""


def _e(text):
    return html.escape(str(text), quote=False)


# Qt rich text (QTextBrowser) supports a CSS subset: colours, font weight/size, block margins,
# table cell padding/background. No border-radius, so pills are padded tinted spans.
def pill(severity, theme: Theme = DARK_THEME, text=None):
    s = theme.status[severity]
    return (f'<span style="background-color:{s.bg};color:{s.fg};font-weight:600">'
            f'&nbsp;{_e(text or STATUS_TEXT[severity])}&nbsp;</span>')


def _section(title, theme):
    return (f'<p style="color:{theme.muted};font-weight:600;'
            f'margin-top:16px;margin-bottom:4px">{_e(title)}</p>')


def _callout(severity, body_html, theme):
    s = theme.status[severity]
    return (f'<table width="100%" cellspacing="0" cellpadding="8" style="margin-top:8px">'
            f'<tr><td style="background-color:{s.bg};color:{theme.text}">{body_html}</td></tr></table>')


def detail_html(finding, theme: Theme = DARK_THEME, mono="monospace", change=None):
    """One test's result. change is its entry in clinic.follow_up()'s changes, when it has one."""
    t = theme
    sev = finding["severity"]
    parts = [
        f'<p style="color:{t.muted};margin-bottom:2px">{_e(CATEGORY_TITLE[finding["category"]])}</p>',
        f'<h2 style="color:{t.text};margin-top:0;margin-bottom:8px;font-weight:600">{_e(finding["title"])}</h2>',
        (f'<p style="margin-bottom:4px">{pill(sev, t)}&nbsp;&nbsp;'
         f'<span style="color:{t.text}">{_e(finding["summary"])}</span></p>'),
        f'<p style="color:{t.muted};margin-top:2px">{_e(STATUS_MEANING[sev])}</p>',
    ]
    if change and clinic.CHANGE_TEXT.get(change[0]):
        parts.append(f'<p style="color:{t.text}">{_e(clinic.CHANGE_TEXT[change[0]])} since your last checkup: '
                     f'it was {_e(STATUS_TEXT[change[1]])}.</p>')
    if finding["reason"]:
        parts.append(_section("Diagnosis", t)
                     + f'<p style="color:{t.text};margin-top:0">{_e(finding["reason"])}</p>')
    if finding["evidence"]:
        parts.append(_section("What I found", t)
                     + f'<table width="100%" cellspacing="0" cellpadding="10"><tr>'
                       f'<td style="background-color:{t.surface_alt}">'
                       f'<pre style="font-family:\'{_e(mono)}\';color:{t.text};margin:0">'
                     + _e("\n".join(finding["evidence"])) + "</pre></td></tr></table>")
    if finding["guidance"]:
        parts.append(_section("Treatment", t)
                     + f'<ul style="color:{t.text};margin-top:0">'
                     + "".join(f'<li style="margin-bottom:4px">{_e(g)}</li>' for g in finding["guidance"]) + "</ul>"
                     f'<p style="color:{t.muted}">I only prescribe: Doctor NoX never carries out these steps '
                     f'for you.</p>')
    elif sev == PASS:
        parts.append(_section("Treatment", t) + f'<p style="color:{t.text};margin-top:0">None needed.</p>')
    # The test's ID, for reports and bug reports; last, because it is reference, not reading.
    parts.append(f'<p style="color:{t.muted};margin-top:16px">Test ID&nbsp; '
                 f'<span style="font-family:\'{_e(mono)}\'">{_e(finding["id"])}</span></p>')
    return "".join(parts)


def error_html(message, theme: Theme = DARK_THEME):
    t = theme
    return (f'<h2 style="color:{t.text};font-weight:600">The checkup couldn\'t finish</h2>'
            + _callout(ERROR, f'<span style="color:{t.status[ERROR].fg};font-weight:600">Doctor NoX error</span><br>'
                              f'{_e(message)}', t)
            + f'<p style="color:{t.muted}">Nothing on the system was changed. Press F5 to try again; '
              f'if it keeps failing, run <code>doctor-nox --text</code> in a terminal.</p>')


def _said(sentences, theme):
    """The doctor's sentences: the first one stands out, the rest follow as ordinary text."""
    if not sentences:
        return ""
    head, rest = sentences[0], sentences[1:]
    return (f'<p style="color:{theme.text};font-weight:600;margin-bottom:4px">{_e(head)}</p>'
            + "".join(f'<p style="color:{theme.text};margin-top:0;margin-bottom:4px">{_e(line)}</p>' for line in rest))


def overview_html(report, scanning=False, theme: Theme = DARK_THEME, follow=None, symptom=clinic.GENERAL, now=None):
    """The diagnosis pane: what the doctor says, the follow-up, then the areas.

    follow is clinic.follow_up()'s result (None: no earlier checkup to compare with).
    """
    t = theme
    if not report:
        lead = ("Examining… I'll have the findings when every test has finished." if scanning
                else "Press <b>Start checkup</b> (F5).")
        return (f'<h2 style="color:{t.text};font-weight:600">{_e(clinic.MOTTO)}</h2>'
                f'<p style="color:{t.muted}">I examine updates and packages, security, services, drivers, '
                f'controllers, storage, apps, network and logs on any Linux. I only look and prescribe: I need no '
                f'root, change nothing on your system and send nothing anywhere. To follow up next time, I keep '
                f'one small note of my own: the conditions found at your last checkup.</p>'
                f'<p style="color:{t.text}">{lead}</p>' + _legend(t))
    tot = report["totals"]
    state = report["overall"]
    rows = []
    for _key, title, total, _counts, worst in category_rows(report)[1:]:
        if not total:
            continue
        rows.append(f'<tr><td style="padding:4px 12px 4px 0">{pill(worst, t)}</td>'
                    f'<td style="color:{t.text};padding:4px 16px 4px 0">{_e(title)}</td>'
                    f'<td style="color:{t.muted};padding:4px 0">{total} test{"" if total == 1 else "s"}</td></tr>')
    incomplete = "" if report["complete"] else _callout(
        WARN, f'<span style="color:{t.status[WARN].fg};font-weight:600">Checkup stopped early</span><br>'
              f'It was cancelled or ran out of time. Tests that didn\'t finish are shown as '
              f'{_e(STATUS_TEXT[UNKNOWN])}.', t)
    since = clinic.follow_up_text(report, follow, now)
    followed = (_section("Since your last checkup", t)
                + "".join(f'<p style="color:{t.text};margin-top:0;margin-bottom:4px">{_e(line)}</p>' for line in since)
                ) if since else ""
    skipped = report.get("not_applicable") or []
    not_here = ""
    if skipped:
        not_here = (_section("Not examined here", t)
                    + f'<p style="color:{t.muted};margin-top:0">These tests are only for other kinds of Linux:</p>'
                    + '<table cellspacing="0">' + "".join(
                        f'<tr><td style="color:{t.text};padding:2px 16px 2px 0">{_e(item["title"])}</td>'
                        f'<td style="color:{t.muted};padding:2px 0">only on {_e(item["applies_to"])}</td></tr>'
                        for item in skipped) + "</table>")
    return (_section("Diagnosis", t).replace("margin-top:16px", "margin-top:0")
            + f'<h2 style="color:{t.status[state].fg};margin-top:0;font-weight:600">{_e(HEADLINE[state])}</h2>'
            + _said(clinic.verdict(report, symptom), t)
            + f'<p style="color:{t.muted}">{_e(count_summary(tot))}<br>'
              f'A checkup shows this moment; it is not a score.</p>'
            + incomplete + followed
            + _section("Areas examined", t) + '<table cellspacing="0">' + "".join(rows) + "</table>"
            + f'<p style="color:{t.muted};margin-top:12px">Select a test to see what I found and the treatment.</p>'
            + not_here + _legend(t))


def _legend(theme: Theme = DARK_THEME):
    t = theme
    return (_section("What the conditions mean", t) + '<table cellspacing="0">' + "".join(
        f'<tr><td style="padding:3px 12px 3px 0">{pill(s, t)}</td>'
        f'<td style="color:{t.muted};padding:3px 0">{_e(STATUS_MEANING[s])}</td></tr>'
        for s in (ERROR, WARN, UNKNOWN, INFO, PASS)) + "</table>")
