"""Text/JSON rendering and best-effort redaction for sharing.

Two kinds of report exist and are always labelled:
  FULL LOCAL REPORT       everything Doctor observed; for your own use
  REDACTED SHARING REPORT the same findings passed through redact_text()

Redaction is pattern-based and best-effort. It cannot recognise every secret
or identifier; the sharing header says so. See docs/DOCTOR_NOX.md.
"""
from __future__ import annotations

import copy
import ipaddress
import json
import re

from .model import CATEGORIES, CATEGORY_TITLE, PASS, SEVERITY_LABEL

FULL_NOTICE = ("FULL LOCAL REPORT — may contain your username, hostname, network addresses, image "
               "references and log messages. Use the redacted sharing report for posting online.")
SHARE_NOTICE = ("REDACTED SHARING REPORT — best-effort removal of usernames, hostnames, home paths, "
                "IP/MAC addresses, e-mail addresses, tokens and URL secrets. Review before posting; "
                "automatic redaction cannot catch everything.")

#: Not treated as identifying: replacing them would mangle ordinary text.
COMMON_NAMES = frozenset({"root", "admin", "user", "guest", "test", "live", "liveuser", "core",
                          "localhost", "localhost-live", "fedora", "fe-xico", "fexico", "bazzite"})

_RULES = [
    # private key material, whole block
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|$)", re.S),
     "<private-key>"),
    # credentials embedded in URLs: scheme://user:pass@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/:@]+:[^\s/@]+@"), r"\1<credentials>@"),
    # secrets in query strings
    (re.compile(r"(?i)([?&](?:access_token|token|auth|key|apikey|api_key|secret|password|passwd|pwd|"
                r"sig|signature|session|code|client_secret)=)[^&#\s]+"), r"\1<redacted>"),
    # key: value / key=value secrets
    (re.compile(r"(?i)\b(authorization|token|password|passwd|secret|api[_-]?key|client[_-]?secret)"
                r"(\s*[:=]\s*)(?!<)[^\s,;]+"), r"\1\2<redacted>"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/-]{8,}=*"), r"\1 <redacted>"),
    # well-known token formats
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"), "<token>"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b"), "<token>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "<token>"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "<token>"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"), "<token>"),
    # image digests: keep a short prefix (useful, not secret)
    (re.compile(r"\b(sha256:[0-9a-f]{12})[0-9a-f]{52}\b"), r"\1…"),
    # machine/boot ids and other long hex identifiers
    (re.compile(r"(?<![0-9a-fA-F:])[0-9a-fA-F]{32,}(?![0-9a-fA-F])"), "<hex-id>"),
    # e-mail addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\b"), "<email>"),
    # MAC addresses
    (re.compile(r"\b(?:[0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}\b"), "<mac>"),
    # home directories
    (re.compile(r"/(?:var/)?home/[^/\s\"']+"), "~"),
]
_IPV4 = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{1,3}){3})(?:/\d{1,2})?(?![\d.])")
_IPV6 = re.compile(r"(?<![0-9A-Za-z:])((?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4})(?:/\d{1,3})?(?![0-9A-Za-z:])")


def _ipv4(m):
    try:
        addr = ipaddress.IPv4Address(m.group(1))
    except ValueError:
        return m.group(0)
    return m.group(0) if addr.is_loopback or addr.is_unspecified else "<ipv4>"


def _ipv6(m):
    try:
        addr = ipaddress.IPv6Address(m.group(1))
    except ValueError:
        return m.group(0)
    return m.group(0) if addr.is_loopback or addr.is_unspecified else "<ipv6>"


def identity_terms(user: str, hostname: str) -> list[tuple[str, str]]:
    terms = []
    for value, label in ((user, "<user>"), (hostname, "<host>"), (hostname.split(".")[0], "<host>")):
        if value and len(value) >= 2 and value.lower() not in COMMON_NAMES:
            terms.append((value, label))
    # longest first so "host.example.org" goes before "host"
    return sorted(set(terms), key=lambda t: -len(t[0]))


def redact_text(text: str, terms: list[tuple[str, str]] = ()) -> str:
    out = str(text)
    for pattern, repl in _RULES:
        out = pattern.sub(repl, out)
    out = _IPV4.sub(_ipv4, out)
    out = _IPV6.sub(_ipv6, out)
    for value, label in terms:
        out = re.sub(rf"(?<![A-Za-z0-9_-]){re.escape(value)}(?![A-Za-z0-9_])", label, out, flags=re.I)
    return out


def redact_report(report: dict, user: str = "", hostname: str = "") -> dict:
    terms = identity_terms(user, hostname)
    shared = copy.deepcopy(report)
    for f in shared["findings"]:
        for key in ("title", "summary", "reason"):
            f[key] = redact_text(f[key], terms)
        f["evidence"] = [redact_text(line, terms) for line in f["evidence"]]
        f["guidance"] = [redact_text(line, terms) for line in f["guidance"]]
    shared["host"] = {k: redact_text(v, terms) for k, v in shared["host"].items()}
    shared["redacted"] = True
    return shared


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"


def _head(report):
    h = report["host"]
    t = report["totals"]
    lines = [
        f"Doctor NoX {report['doctor_version']}  //  read-only system report  //  schema {report['schema_version']}",
        SHARE_NOTICE if report.get("redacted") else FULL_NOTICE,
        f"Snapshot: {report['generated_at']}  ({report['duration_ms']} ms"
        + ("" if report["complete"] else ", INCOMPLETE: scan cancelled or timed out") + ")",
        f"System:   {h.get('name', '?')} {h.get('version', '')} · edition {h.get('edition') or 'unspecified'} · "
        f"image {h.get('image_variant') or '?'} · kernel {h.get('kernel', '?')}",
        f"Overall:  {SEVERITY_LABEL[report['overall']]}   "
        + "  ".join(f"{SEVERITY_LABEL[s]} {t[s]}" for s in ("error", "warn", "unknown", "info", "pass")),
    ]
    return lines


def format_text(report: dict, *, evidence: bool = True) -> str:
    out = _head(report)
    for key, title in CATEGORIES:
        items = [f for f in report["findings"] if f["category"] == key]
        if not items:
            continue
        out += ["", f"── {title.upper()} " + "─" * max(4, 60 - len(title))]
        for f in items:
            out.append(f"[{SEVERITY_LABEL[f['severity']]:<7}] {f['title']}: {f['summary']}")
            if not evidence:
                continue
            if f["reason"]:
                out.append(f"          reason: {f['reason']}")
            out += [f"          · {line}" for line in f["evidence"]]
            out += [f"          → {line}" for line in f["guidance"]]
    out += ["", "No changes were made to the system. Nothing was uploaded."]
    return "\n".join(out) + "\n"


def format_summary(report: dict) -> str:
    """Concise text for the clipboard: header plus everything that is not PASS."""
    out = _head(report) + [""]
    problems = [f for f in report["findings"] if f["severity"] != PASS]
    order = {"error": 0, "warn": 1, "unknown": 2, "info": 3}
    for f in sorted(problems, key=lambda f: order.get(f["severity"], 9)):
        out.append(f"[{SEVERITY_LABEL[f['severity']]:<7}] {CATEGORY_TITLE[f['category']]} / {f['title']}: "
                   f"{f['summary']}")
    if not problems:
        out.append("All checks passed.")
    return "\n".join(out) + "\n"


def export(path, report: dict, *, redacted: bool, fmt: str, user: str = "", hostname: str = "") -> str:
    """Write a report only where the user chose. Atomic; full reports are created mode 0600.

    Returns the text written. fmt is "json" or "text".
    """
    import os
    import tempfile
    if fmt not in ("json", "text"):
        raise ValueError("fmt must be json or text")
    data = redact_report(report, user, hostname) if redacted else report
    text = to_json(data) if fmt == "json" else format_text(data)
    target = os.path.abspath(os.fspath(path))
    directory = os.path.dirname(target) or "."
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".doctor-nox-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            os.fchmod(stream.fileno(), 0o644 if redacted else 0o600)
            stream.write(text)
        os.replace(tmp, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return text
