"""Qt-free presentation logic: grouping, filtering, ordering and detail HTML.

Kept separate from gui.py so it is unit-tested without a display or PySide6.
"""
from __future__ import annotations

import html

from .model import CATEGORIES, CATEGORY_TITLE, ERROR, INFO, PASS, SEVERITIES, SEVERITY_MEANING, UNKNOWN, WARN
from .report import IMAGE_TEXT
from .theme import DARK_THEME, HEADLINE, STATUS_PLURAL, STATUS_TEXT, Theme

OVERVIEW = "overview"
RANK = {sev: i for i, sev in enumerate(SEVERITIES)}  # error first

def category_rows(report):
    """[(key, title, total, counts, worst)] with OVERVIEW first. worst=None when empty."""
    rows = []
    findings = report["findings"] if report else []
    for key, title in ((OVERVIEW, "Overview"), *CATEGORIES):
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
        return f"{title}: no checks"
    parts = [f"{counts[s]} {STATUS_PLURAL[s]}" for s in (ERROR, WARN, UNKNOWN) if counts[s]]
    return f"{title}: {total} checks" + (", " + ", ".join(parts) if parts else ", all verified")


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
            elif finding["severity"] != {"err": ERROR, "warning": WARN, "?": UNKNOWN}.get(want, want):
                return False
        elif term not in hay:
            return False
    return True


def visible(report, category=OVERVIEW, query="", severities=frozenset(SEVERITIES)):
    if not report:
        return []
    items = [f for f in report["findings"]
             if (category == OVERVIEW or f["category"] == category)
             and f["severity"] in severities and matches(f, query)]
    order = {key: i for i, (key, _) in enumerate(CATEGORIES)}
    return sorted(items, key=lambda f: (RANK[f["severity"]], order[f["category"]]))


def header_text(report):
    if not report:
        return "No scan yet", ""
    h, t = report["host"], report["totals"]
    host = " · ".join(x for x in (h.get("name", ""), IMAGE_TEXT.get(h.get("image", ""), ""),
                                  f"kernel {h.get('kernel', '')}") if x)
    counts = " · ".join(f"{t[s]} {STATUS_PLURAL[s]}" for s in (ERROR, WARN, UNKNOWN, INFO, PASS))
    return host, counts


def empty_state(report, scanning, category, query, severities):
    if scanning and not report:
        return "Scanning… results appear when every check has finished."
    if not report:
        return "No results yet.\nPress Scan (F5) to run the read-only checks."
    if not visible(report, category, query, severities):
        if query or severities != frozenset(SEVERITIES):
            return "No checks match the current search or status filter.\nClear the search or turn a status back on."
        return "No checks in this group."
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
    return (f'<p style="color:{theme.muted};font-size:small;font-weight:600;'
            f'margin-top:16px;margin-bottom:4px">{_e(title.upper())}</p>')


def _callout(severity, body_html, theme):
    s = theme.status[severity]
    return (f'<table width="100%" cellspacing="0" cellpadding="8" style="margin-top:8px">'
            f'<tr><td style="background-color:{s.bg};color:{theme.text}">{body_html}</td></tr></table>')


def detail_html(finding, theme: Theme = DARK_THEME, mono="monospace"):
    t = theme
    sev = finding["severity"]
    parts = [
        (f'<p style="color:{t.muted};margin-bottom:2px">{_e(CATEGORY_TITLE[finding["category"]])}'
         f' &nbsp;·&nbsp; <span style="font-family:\'{_e(mono)}\'">{_e(finding["id"])}</span></p>'),
        f'<h2 style="color:{t.text};margin-top:0;margin-bottom:8px;font-weight:600">{_e(finding["title"])}</h2>',
        (f'<p style="margin-bottom:4px">{pill(sev, t)}&nbsp;&nbsp;'
         f'<span style="color:{t.text}">{_e(finding["summary"])}</span></p>'),
        f'<p style="color:{t.muted};margin-top:2px">{_e(SEVERITY_MEANING[sev])}</p>',
    ]
    if finding["reason"]:
        parts.append(_section("Why", t) + f'<p style="color:{t.text};margin-top:0">{_e(finding["reason"])}</p>')
    if finding["evidence"]:
        parts.append(_section("Evidence", t)
                     + f'<table width="100%" cellspacing="0" cellpadding="10"><tr>'
                       f'<td style="background-color:{t.surface_alt}">'
                       f'<pre style="font-family:\'{_e(mono)}\';color:{t.text};margin:0">'
                     + _e("\n".join(finding["evidence"])) + "</pre></td></tr></table>")
    if finding["guidance"]:
        parts.append(_section("What you can do", t)
                     + f'<ul style="color:{t.text};margin-top:0">'
                     + "".join(f'<li style="margin-bottom:4px">{_e(g)}</li>' for g in finding["guidance"]) + "</ul>"
                     f'<p style="color:{t.muted}">Doctor is read-only: it never runs these steps for you.</p>')
    return "".join(parts)


def error_html(message, theme: Theme = DARK_THEME):
    t = theme
    return (f'<h2 style="color:{t.text};font-weight:600">The scan could not finish</h2>'
            + _callout(ERROR, f'<span style="color:{t.status[ERROR].fg};font-weight:600">Doctor error</span><br>'
                              f'{_e(message)}', t)
            + f'<p style="color:{t.muted}">Nothing on the system was changed. Press Scan (F5) to try again; '
              f'if it keeps failing, run <code>doctor-nox --text</code> in a terminal.</p>')


def overview_html(report, scanning=False, theme: Theme = DARK_THEME):
    t = theme
    if not report:
        lead = ("Scanning… results appear when every check has finished." if scanning
                else "Press <b>Scan</b> (F5) to run the checks.")
        return (f'<h2 style="color:{t.text};font-weight:600">Check this system\'s health</h2>'
                f'<p style="color:{t.muted}">Doctor checks updates and packages, security, services, drivers, '
                f'controllers, storage, apps, network and logs on any Linux. It is read-only: it needs no '
                f'root, changes nothing and sends nothing anywhere.</p>'
                f'<p style="color:{t.text}">{lead}</p>' + _legend(t))
    tot = report["totals"]
    state = report["overall"]
    rows = []
    for _key, title, total, _counts, worst in category_rows(report)[1:]:
        if not total:
            continue
        rows.append(f'<tr><td style="padding:4px 12px 4px 0">{pill(worst, t)}</td>'
                    f'<td style="color:{t.text};padding:4px 16px 4px 0">{_e(title)}</td>'
                    f'<td style="color:{t.muted};padding:4px 0">{total} checks</td></tr>')
    incomplete = "" if report["complete"] else _callout(
        WARN, f'<span style="color:{t.status[WARN].fg};font-weight:600">Scan incomplete</span><br>'
              f'It was cancelled or timed out. Checks that did not finish are shown as Unknown.', t)
    counts = " · ".join(f"{tot[s]} {STATUS_PLURAL[s]}" for s in (ERROR, WARN, UNKNOWN, INFO, PASS))
    skipped = report.get("not_applicable") or []
    not_here = ""
    if skipped:
        not_here = (_section("Not applicable here", t)
                    + f'<p style="color:{t.muted};margin-top:0">These checks only run on other kinds of Linux:</p>'
                    + '<table cellspacing="0">' + "".join(
                        f'<tr><td style="color:{t.text};padding:2px 16px 2px 0">{_e(item["title"])}</td>'
                        f'<td style="color:{t.muted};padding:2px 0">only on {_e(item["applies_to"])}</td></tr>'
                        for item in skipped) + "</table>")
    return (_section("Overview", t).replace("margin-top:16px", "margin-top:0")
            + f'<h2 style="color:{t.status[state].fg};margin-top:0;font-weight:600">{_e(HEADLINE[state])}</h2>'
            f'<p style="color:{t.muted}">{counts}<br>A point-in-time observation, not a score.</p>'
            + incomplete + _section("Groups", t) + '<table cellspacing="0">' + "".join(rows) + "</table>"
            + f'<p style="color:{t.muted};margin-top:12px">Select a check to see its evidence and what you can do.</p>'
            + not_here + _legend(t))


def _legend(theme: Theme = DARK_THEME):
    t = theme
    return (_section("Status levels", t) + '<table cellspacing="0">' + "".join(
        f'<tr><td style="padding:3px 12px 3px 0">{pill(s, t)}</td>'
        f'<td style="color:{t.muted};padding:3px 0">{_e(SEVERITY_MEANING[s])}</td></tr>'
        for s in (ERROR, WARN, UNKNOWN, INFO, PASS)) + "</table>")
