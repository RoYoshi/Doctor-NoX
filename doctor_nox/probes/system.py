"""Operating system identity, os-release consistency and image-based deployments.

Image-based systems (bootc, Fedora Atomic, Universal Blue and other OSTree
systems) are read from `bootc status`, which needs root, and otherwise from
`rpm-ostree status`, which any user may run. Both are read-only queries.
"""
from __future__ import annotations

import datetime
import json
import os
import re

from ..distro import FAMILY_TITLE, OSTREE
from ..model import INFO, PASS, UNKNOWN, WARN, Finding
from . import finding, probe

CAT = "system"
API = "org.containers.bootc/v1"
IMAGE_TITLE = {"ostree": "image-based (OSTree)", "steamos": "image-based (SteamOS)",
               "transactional": "transactional updates (read-only root)"}


@probe("system.identity", CAT, "Operating system")
def identity(ctx):
    p = ctx.platform
    data = p.os_release
    if not data:
        return finding("system.identity", CAT, "Operating system", UNKNOWN, "os-release could not be read",
                       reason="/usr/lib/os-release and /etc/os-release are missing or unreadable.",
                       evidence=[f"kernel={ctx.kernel}"])
    evidence = [f"NAME={data.get('NAME', '?')}", f"PRETTY_NAME={data.get('PRETTY_NAME', '?')}",
                f"ID={p.id or '?'}", f"ID_LIKE={' '.join(p.id_like) or '(unset)'}",
                f"VERSION_ID={p.version or '(unset)'}", f"family: {FAMILY_TITLE[p.family]}",
                f"image-based: {IMAGE_TITLE.get(p.image, 'no')}",
                f"package tools: {', '.join(p.package_managers) or 'none recognized'}",
                f"init: {p.init}", f"security module: {p.security or 'none active'}", f"kernel={ctx.kernel}"]
    summary = f"{p.name or p.id or 'Linux'} · kernel {ctx.kernel}"
    guidance = []
    if p.family == "other":
        guidance.append("Doctor NoX runs its checks for any Linux here. It has extra checks for the Fedora, "
                        "Debian/Ubuntu, Arch and openSUSE families.")
    return finding("system.identity", CAT, "Operating system", INFO, summary, evidence=evidence,
                   guidance=guidance)


def _parse_os_release(text):
    data = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            data[key.strip()] = val.strip().strip("\"'")
    return data


@probe("system.os_release", CAT, "os-release consistency")
def os_release_drift(ctx):
    """/etc/os-release is normally a link to the distribution's /usr/lib/os-release.

    A regular-file copy can go stale (OSTree's /etc merge keeps old files, and some
    upgrade paths leave one behind), so tools that read /etc report an old release.
    """
    title = "os-release consistency"
    etc = ctx.path("/etc/os-release")
    vendor = _parse_os_release(ctx.try_text("/usr/lib/os-release", 64 * 1024))
    local_text = None if etc.is_symlink() else ctx.try_text("/etc/os-release", 64 * 1024)
    if not vendor:
        if local_text:
            return finding("system.os_release", CAT, title, PASS, "/etc/os-release is the only copy",
                           evidence=["/usr/lib/os-release: absent", "/etc/os-release: regular file"])
        return finding("system.os_release", CAT, title, UNKNOWN, "os-release unreadable",
                       reason="/usr/lib/os-release and /etc/os-release are missing or unreadable.")
    if etc.is_symlink():
        target = os.readlink(etc)
        ok_link = target in ("../usr/lib/os-release", "/usr/lib/os-release")
        return finding("system.os_release", CAT, title, PASS if ok_link else INFO,
                       "/etc/os-release links to /usr/lib/os-release" if ok_link
                       else f"/etc/os-release links to {target}", evidence=[f"/etc/os-release -> {target}"])
    local = _parse_os_release(local_text)
    if not local:
        return finding("system.os_release", CAT, title, INFO, "/etc/os-release absent or unreadable",
                       evidence=["/usr/lib/os-release: present"])
    keys = ("NAME", "ID", "VERSION_ID", "VARIANT_ID")
    diff = [f"{k}: /etc={local.get(k, '(unset)')!r} /usr/lib={vendor.get(k, '(unset)')!r}"
            for k in keys if local.get(k) != vendor.get(k)]
    if not diff:
        return finding("system.os_release", CAT, title, PASS, "/etc/os-release is a copy that matches /usr/lib",
                       evidence=["/etc/os-release: regular file"])
    return finding("system.os_release", CAT, title, WARN,
                   "/etc/os-release is a stale copy that disagrees with /usr/lib/os-release",
                   evidence=["/etc/os-release: regular file (not the usual link)"] + diff,
                   guidance=["Programs that read /etc/os-release can show an old release or edition.",
                             "After checking it is not a deliberate local override, the standard layout is "
                             "`sudo ln -sf ../usr/lib/os-release /etc/os-release`. Doctor changes nothing."])


def host_summary(ctx) -> dict:
    p = ctx.platform
    return {"name": p.name or "unknown", "id": p.id, "version": p.version, "family": p.family,
            "image": p.image, "kernel": ctx.kernel, "init": p.init, "security": p.security,
            "package_managers": list(p.package_managers)}


# ── image-based deployments ──────────────────────────────────────────────────
def _short(digest: str) -> str:
    return digest[:19] + "…" if digest.startswith("sha256:") and len(digest) > 19 else digest


def _age(when: datetime.datetime | None, now: datetime.datetime) -> str:
    if when is None:
        return ""
    days = (now - when).total_seconds() / 86400
    return f"{days:.0f} days" if days >= 0 else ""


#: Fractional seconds, which bootc prints with up to nine digits.
_FRACTION = re.compile(r"(T\d{2}:\d{2}:\d{2})\.(\d+)")


def _iso(timestamp) -> datetime.datetime | None:
    """An RFC 3339 timestamp (bootc's) as an aware datetime, or None.

    Python 3.10's fromisoformat takes neither "Z" nor more than six
    fractional digits, so both are normalised first.
    """
    text = str(timestamp).strip()
    if text[-1:] in ("Z", "z"):
        text = text[:-1] + "+00:00"
    text = _FRACTION.sub(lambda m: f"{m.group(1)}.{m.group(2)[:6].ljust(6, '0')}", text, count=1)
    try:
        ts = datetime.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None
    return ts if ts.tzinfo else None


class Deployment:
    """One deployment in the shape both bootc and rpm-ostree can fill."""

    def __init__(self, ref="", version="", digest="", built=None, pinned=False, download_only=False,
                 incompatible=False, layered=0, unlocked=""):
        self.ref, self.version, self.digest, self.built = ref, version, digest, built
        self.pinned, self.download_only, self.incompatible = pinned, download_only, incompatible
        self.layered, self.unlocked = layered, unlocked

    def describe(self, label: str) -> str:
        bits = [f"{label}: {self.ref or 'unknown source'}"]
        if self.version:
            bits.append(f"version {self.version}")
        if self.digest:
            bits.append(_short(self.digest))
        for flag, text in ((self.pinned, "pinned"), (self.download_only, "download-only"),
                           (self.incompatible, "incompatible")):
            if flag:
                bits.append(text)
        if self.layered:
            bits.append(f"{self.layered} layered package change(s)")
        return " · ".join(bits)


class Deployments:
    def __init__(self, source, booted, staged=None, rollback=None, others=(), configured="",
                 rollback_queued=False, default_is_other=False):
        self.source, self.booted, self.staged, self.rollback = source, booted, staged, rollback
        self.others, self.configured = list(others), configured
        self.rollback_queued, self.default_is_other = rollback_queued, default_is_other

    @property
    def all(self):
        return [d for d in (self.booted, self.staged, self.rollback, *self.others) if d is not None]


def _bootc_deployment(entry) -> Deployment | None:
    """bootc v1: entry.image.image is the ImageReference {image, transport, signature}."""
    if not isinstance(entry, dict):
        return None
    status = entry.get("image") if isinstance(entry.get("image"), dict) else {}
    ref = status.get("image") if isinstance(status.get("image"), dict) else {}
    return Deployment(ref=ref.get("image", "") or "", version=status.get("version") or "",
                      digest=status.get("imageDigest", "") or "", built=_iso(status.get("timestamp")),
                      pinned=bool(entry.get("pinned")), download_only=bool(entry.get("downloadOnly")),
                      incompatible=bool(entry.get("incompatible")))


def parse_bootc(host, source: str) -> Deployments:
    """Validate the parts of bootc's v1 Host document Doctor relies on.

    Raises ValueError with a specific reason for unsupported/inconsistent input.
    Unknown extra fields are ignored so newer bootc versions keep working.
    """
    if not isinstance(host, dict):
        raise ValueError("not a JSON object")
    if host.get("apiVersion") != API:
        raise ValueError(f"unsupported apiVersion {host.get('apiVersion')!r}")
    status = host.get("status")
    spec = host.get("spec") if isinstance(host.get("spec"), dict) else {}
    if not isinstance(status, dict):
        raise ValueError("missing status object")
    if not isinstance(status.get("booted"), dict):
        raise ValueError("no booted deployment (not a booted bootc system)")
    for slot in ("staged", "rollback"):
        if status.get(slot) is not None and not isinstance(status.get(slot), dict):
            raise ValueError(f"invalid {slot} entry")
    others = status.get("otherDeployments") or []
    if not isinstance(others, list):
        raise ValueError("invalid otherDeployments")
    queued = status.get("rollbackQueued")
    if queued is not None and not isinstance(queued, bool):
        raise ValueError("invalid rollbackQueued")
    configured = ((spec.get("image") or {}).get("image") if isinstance(spec.get("image"), dict) else "") or ""
    return Deployments(source, _bootc_deployment(status["booted"]), _bootc_deployment(status.get("staged")),
                       _bootc_deployment(status.get("rollback")),
                       [d for d in map(_bootc_deployment, others) if d], configured, bool(queued))


def _rpm_ostree_deployment(entry) -> Deployment:
    ref = entry.get("container-image-reference") or entry.get("origin") or ""
    layered = sum(len(entry.get(k) or []) for k in ("requested-packages", "requested-local-packages",
                                                     "requested-base-removals", "requested-base-local-replacements"))
    built = None
    if isinstance(entry.get("timestamp"), int):
        built = datetime.datetime.fromtimestamp(entry["timestamp"], datetime.timezone.utc)
    unlocked = str(entry.get("unlocked") or "none")
    return Deployment(ref=str(ref), version=str(entry.get("version") or ""),
                      digest=str(entry.get("container-image-reference-digest") or ""), built=built,
                      pinned=bool(entry.get("pinned")), layered=layered,
                      unlocked="" if unlocked == "none" else unlocked)


def parse_rpm_ostree(doc, source: str) -> Deployments:
    """`rpm-ostree status --json`: deployments in boot order; [0] is what the next boot starts."""
    if not isinstance(doc, dict) or not isinstance(doc.get("deployments"), list):
        raise ValueError("no deployments list")
    entries = [d for d in doc["deployments"] if isinstance(d, dict)]
    booted = [d for d in entries if d.get("booted") is True]
    if len(booted) != 1:
        raise ValueError("no single booted deployment")
    staged = next((d for d in entries if d.get("staged") is True), None)
    rest = [d for d in entries if d is not booted[0] and d is not staged]
    default = entries[0]
    default_is_other = default is not booted[0] and default is not staged
    rollback = rest[0] if rest else None
    return Deployments(source, _rpm_ostree_deployment(booted[0]),
                       _rpm_ostree_deployment(staged) if staged else None,
                       _rpm_ostree_deployment(rollback) if rollback else None,
                       [_rpm_ostree_deployment(d) for d in rest[1:]],
                       configured="", rollback_queued=default_is_other and rollback is default,
                       default_is_other=default_is_other)


def deployment_findings(deps: Deployments, ctx) -> list[Finding]:
    booted, staged, rollback = deps.booted, deps.staged, deps.rollback
    src = [f"source: {deps.source}"]
    out = []

    evidence = src + [booted.describe("booted")]
    age = _age(booted.built, ctx.now)
    if age:
        evidence.append(f"booted image built {age} ago ({booted.built.isoformat()})")
    if booted.incompatible:
        out.append(finding("system.image.booted", CAT, "Booted deployment", WARN,
                           "Booted deployment is marked incompatible with bootc updates",
                           evidence=evidence, guidance=[
                               "The deployment was changed outside bootc (for example with rpm-ostree "
                               "package layering). bootc updates may refuse it.",
                               "Review `rpm-ostree status` before updating."]))
    elif not booted.ref:
        out.append(finding("system.image.booted", CAT, "Booted deployment", WARN,
                           "Booted deployment has no update source",
                           evidence=evidence, guidance=["Without a source the system cannot fetch updates "
                                                        "for this deployment."]))
    else:
        out.append(finding("system.image.booted", CAT, "Booted deployment", PASS,
                           f"Booted from {booted.ref}" + (f" ({booted.version})" if booted.version else ""),
                           evidence=evidence))

    # Configured source (bootc spec) vs booted image.
    spec = deps.configured
    if spec and booted.ref and spec != booted.ref:
        pending_switch = bool(staged and staged.ref == spec)
        out.append(finding("system.image.source", CAT, "Update source", INFO if pending_switch else WARN,
                           "Configured source differs from the booted image",
                           evidence=src + [f"configured: {spec}", f"booted: {booted.ref}"] +
                           ([f"staged deployment already uses {spec}"] if pending_switch else []),
                           guidance=["Future updates follow the configured source, not the booted image.",
                                     "If this is unexpected, compare with `sudo bootc status` before rebooting."]))
    else:
        ref = spec or booted.ref
        out.append(finding("system.image.source", CAT, "Update source", PASS if ref else INFO,
                           f"Updates follow {ref}" if ref else "No update source reported", evidence=src))

    # What the next boot starts. Never claim a reboot applies a download-only deployment.
    pend_ev = src + [f"rollback queued: {deps.rollback_queued}"]
    if staged:
        pend_ev.append(staged.describe("staged"))
    if deps.rollback_queued or (deps.default_is_other and not staged):
        pend = finding("system.image.pending", CAT, "Next boot", INFO,
                       "Rollback queued: the next boot starts the previous deployment",
                       evidence=pend_ev + ([rollback.describe("rollback")] if rollback else []),
                       guidance=["This was requested earlier (`bootc rollback` or `rpm-ostree rollback`). "
                                 "Doctor does not change it."])
    elif staged and staged.download_only:
        pend = finding("system.image.pending", CAT, "Next boot", INFO,
                       "Download-only deployment present: it will NOT be applied at reboot",
                       evidence=pend_ev,
                       guidance=["A download-only deployment is kept for review; rebooting keeps the current "
                                 "deployment. Apply it deliberately with your update tool."])
    elif staged:
        pend = finding("system.image.pending", CAT, "Next boot", INFO,
                       "Update staged: it applies at the next reboot",
                       evidence=pend_ev, guidance=["Reboot when convenient. Doctor never reboots."])
    else:
        pend = finding("system.image.pending", CAT, "Next boot", PASS,
                       "Nothing staged; the next boot uses the current deployment", evidence=pend_ev)
    out.append(pend)

    if rollback:
        out.append(finding("system.image.rollback", CAT, "Rollback deployment", PASS,
                           "Previous deployment available for rollback",
                           evidence=src + [rollback.describe("rollback")]))
    else:
        out.append(finding("system.image.rollback", CAT, "Rollback deployment", INFO, "No rollback deployment yet",
                           evidence=src + ["no previous deployment listed"],
                           guidance=["Normal on a fresh install or after cleanup; one appears after the next "
                                     "completed update."]))

    pinned = [d for d in deps.all if d.pinned]
    out.append(finding("system.image.pinned", CAT, "Pinned recovery", PASS if pinned else INFO,
                       f"{len(pinned)} pinned deployment(s)" if pinned else "No pinned deployment",
                       evidence=src + [d.describe("pinned") for d in pinned] +
                       [f"other deployments: {len(deps.others)}"],
                       guidance=[] if pinned else [
                           "Optional. A pinned deployment survives updates as an extra recovery point "
                           "(`sudo ostree admin pin 0`, run deliberately)."]))

    changes = []
    if booted.layered:
        changes.append(f"{booted.layered} layered package change(s) on the booted deployment")
    if booted.unlocked:
        changes.append(f"/usr is unlocked ({booted.unlocked}): changes there vanish or bypass updates")
    if changes:
        out.append(finding("system.image.local_changes", CAT, "Local changes", INFO,
                           "; ".join(changes), evidence=src + changes,
                           guidance=["Layered packages work, but each update has to reapply them, which "
                                     "slows updates and can block them when a package conflicts.",
                                     "`rpm-ostree status` lists them."]))
    return out


@probe("system.image", CAT, "Image deployments", applies=OSTREE)
def image(ctx):
    res = ctx.runner.run("bootc", "status", "--json", "--format-version=1")
    reasons = [res.describe()]
    if res.ok:
        try:
            return deployment_findings(parse_bootc(json.loads(res.stdout),
                                                   "`bootc status --json` (live)"), ctx)
        except ValueError as exc:
            reasons.append(f"bootc status not understood: {exc}")
    # bootc refuses non-root users; rpm-ostree answers any user, read-only.
    res = ctx.runner.run("rpm-ostree", "status", "--json")
    reasons.append(res.describe())
    if res.ok:
        try:
            return deployment_findings(parse_rpm_ostree(json.loads(res.stdout),
                                                        "`rpm-ostree status --json` (live)"), ctx)
        except ValueError as exc:
            reasons.append(f"rpm-ostree status not understood: {exc}")
    staged = ctx.exists("/run/ostree/staged-deployment")
    ev = reasons + [f"/run/ostree/staged-deployment: {'present' if staged else 'absent'}"]
    return [finding("system.image.booted", CAT, "Booted deployment", UNKNOWN,
                    "Deployment status unavailable to this user" + ("; a deployment is staged" if staged else ""),
                    reason="Neither bootc nor rpm-ostree answered.", evidence=ev,
                    guidance=["Run `rpm-ostree status`, or `sudo bootc status`, yourself. Doctor never elevates."])]
