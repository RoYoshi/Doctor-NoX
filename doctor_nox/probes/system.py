"""OS identity and bootc deployments."""
from __future__ import annotations

import datetime
import json
import os

from ..model import INFO, PASS, UNKNOWN, WARN, Finding
from . import finding, probe

CAT = "system"
API = "org.containers.bootc/v1"
UPDATER_RUNTIME = "/run/fe-xicoos-updater"


@probe("system.identity", CAT, "OS identity")
def identity(ctx):
    data = ctx.os_release()
    name = data.get("NAME", "")
    version = data.get("FE_XICO_VERSION") or data.get("VERSION_ID", "")
    edition = data.get("FE_XICO_VARIANT") or data.get("VARIANT", "")
    evidence = [f"NAME={name or '?'}", f"FE_XICO_VERSION={data.get('FE_XICO_VERSION', '?')}",
                f"VARIANT={edition or '?'}", f"VERSION_ID={data.get('VERSION_ID', '?')}",
                f"kernel={ctx.kernel}"]
    if not data:
        return finding("system.identity", CAT, "OS identity", UNKNOWN, "os-release could not be read",
                       reason="/usr/lib/os-release and /etc/os-release are missing or unreadable.")
    if name != "FE-Xico":
        return finding("system.identity", CAT, "OS identity", WARN,
                       f"Not identified as FE-XicoOS (NAME={name or 'unset'})", evidence=evidence,
                       guidance=["Doctor's checks assume an FE-XicoOS image; results on other "
                                 "systems may not apply."])
    return finding("system.identity", CAT, "OS identity", PASS,
                   f"FE-XicoOS {version or '?'} · {edition or 'standard'} · kernel {ctx.kernel}",
                   evidence=evidence)


def _parse_os_release(text):
    data = {}
    for line in (text or "").splitlines():
        if "=" in line and not line.startswith("#"):
            key, val = line.split("=", 1)
            data[key.strip()] = val.strip().strip("\"'")
    return data


@probe("system.os_release", CAT, "os-release consistency")
def os_release_drift(ctx):
    """/etc/os-release should be a link to the image's /usr/lib/os-release.

    OSTree's /etc merge can preserve an old regular file, so tools that read /etc
    (including KDE's About page) may report an outdated version or edition.
    """
    etc = ctx.path("/etc/os-release")
    vendor = _parse_os_release(ctx.try_text("/usr/lib/os-release", 64 * 1024))
    if not vendor:
        return finding("system.os_release", CAT, "os-release consistency", UNKNOWN,
                       "/usr/lib/os-release unreadable")
    if etc.is_symlink():
        target = os.readlink(etc)
        ok_link = target in ("../usr/lib/os-release", "/usr/lib/os-release")
        return finding("system.os_release", CAT, "os-release consistency", PASS if ok_link else INFO,
                       "/etc/os-release links to the image's os-release" if ok_link
                       else f"/etc/os-release links to {target}", evidence=[f"/etc/os-release -> {target}"])
    local = _parse_os_release(ctx.try_text("/etc/os-release", 64 * 1024))
    if not local:
        return finding("system.os_release", CAT, "os-release consistency", INFO, "/etc/os-release absent or unreadable")
    keys = ("NAME", "VERSION_ID", "FE_XICO_VERSION", "VARIANT", "FE_XICO_VARIANT")
    diff = [f"{k}: /etc={local.get(k, '(unset)')!r} image={vendor.get(k, '(unset)')!r}"
            for k in keys if local.get(k) != vendor.get(k)]
    if not diff:
        return finding("system.os_release", CAT, "os-release consistency", PASS,
                       "/etc/os-release is a copy matching the image", evidence=["/etc/os-release: regular file"])
    return finding("system.os_release", CAT, "os-release consistency", WARN,
                   "/etc/os-release is a stale copy that disagrees with the booted image",
                   evidence=["/etc/os-release: regular file (not the usual symlink)"] + diff,
                   guidance=["Programs that read /etc/os-release can show an old version or edition.",
                             "After checking it is not a deliberate local override, the standard layout is "
                             "`sudo ln -sf ../usr/lib/os-release /etc/os-release`. Doctor changes nothing."])


def host_summary(ctx) -> dict[str, str]:
    data = ctx.os_release()
    variant = "nvidia-open" if ctx.exists("/usr/lib/modprobe.d/80-fe-xico-nvidia-open.conf") else "standard"
    return {"name": data.get("NAME", "unknown"),
            "edition": data.get("FE_XICO_VARIANT") or data.get("VARIANT", ""),
            "version": data.get("FE_XICO_VERSION") or data.get("VERSION_ID", ""),
            "kernel": ctx.kernel, "image_variant": variant}


# ── bootc ────────────────────────────────────────────────────────────────────
def _image(entry):
    """bootc v1: entry.image.image is the ImageReference {image, transport, signature}."""
    if not isinstance(entry, dict):
        return {}
    status = entry.get("image")
    if not isinstance(status, dict):
        return {}
    ref = status.get("image") if isinstance(status.get("image"), dict) else {}
    return {"ref": ref.get("image", ""), "transport": ref.get("transport", ""),
            "digest": status.get("imageDigest", ""), "version": status.get("version") or "",
            "timestamp": status.get("timestamp") or ""}


def _short(digest: str) -> str:
    return digest[:19] + "…" if digest.startswith("sha256:") and len(digest) > 19 else digest


def _describe(label, entry):
    img = _image(entry)
    bits = [f"{label}: {img.get('ref') or 'unknown image'}"]
    if img.get("version"):
        bits.append(f"version {img['version']}")
    if img.get("digest"):
        bits.append(_short(img["digest"]))
    if isinstance(entry, dict):
        if entry.get("pinned"):
            bits.append("pinned")
        if entry.get("downloadOnly"):
            bits.append("download-only")
        if entry.get("incompatible"):
            bits.append("incompatible")
    return " · ".join(bits)


def _age(timestamp: str, now: datetime.datetime) -> str:
    try:
        ts = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return ""
    if ts.tzinfo is None:
        return ""
    days = (now - ts).total_seconds() / 86400
    return f"{days:.0f} days" if days >= 0 else ""


def parse_host(host):
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
    return status, spec, others


def bootc_findings(host, source: str, ctx) -> list[Finding]:
    status, spec, others = parse_host(host)
    booted, staged, rollback = status["booted"], status.get("staged"), status.get("rollback")
    queued = bool(status.get("rollbackQueued"))
    order = spec.get("bootOrder", "")
    b = _image(booted)
    src = [f"source: {source}"]
    out = []

    evidence = src + [_describe("booted", booted)]
    age = _age(b.get("timestamp", ""), ctx.now)
    if age:
        evidence.append(f"booted image built {age} ago ({b['timestamp']})")
    if booted.get("incompatible"):
        out.append(finding("system.bootc.booted", CAT, "Booted deployment", WARN,
                           "Booted deployment is marked incompatible with bootc updates",
                           evidence=evidence, guidance=[
                               "The deployment was modified outside bootc (for example with "
                               "rpm-ostree package layering). Native bootc updates may refuse it.",
                               "Review `rpm-ostree status` in Foot before attempting updates."]))
    elif not b.get("ref"):
        out.append(finding("system.bootc.booted", CAT, "Booted deployment", WARN,
                           "Booted deployment has no container image source",
                           evidence=evidence, guidance=["Without an image source bootc cannot update this "
                                                        "deployment; it may have been installed from "
                                                        "an OSTree commit."]))
    else:
        out.append(finding("system.bootc.booted", CAT, "Booted deployment", PASS,
                           f"Booted from {b['ref']}" + (f" ({b['version']})" if b.get("version") else ""),
                           evidence=evidence))

    # Configured source (spec) vs booted image.
    spec_ref = ((spec.get("image") or {}).get("image") if isinstance(spec.get("image"), dict) else "") or ""
    if spec_ref and b.get("ref") and spec_ref != b["ref"]:
        pending_switch = bool(staged and _image(staged).get("ref") == spec_ref)
        out.append(finding("system.bootc.source", CAT, "Update source", INFO if pending_switch else WARN,
                           "Configured source differs from booted image",
                           evidence=src + [f"spec.image: {spec_ref}", f"booted: {b['ref']}"] +
                           ([f"staged deployment already uses {spec_ref}"] if pending_switch else []),
                           guidance=["Future updates follow the configured source (spec), not the booted image.",
                                     "If this is unexpected, compare with `bootc status` and the FE-Xico "
                                     "Updater before rebooting."]))
    else:
        out.append(finding("system.bootc.source", CAT, "Update source", PASS if spec_ref else INFO,
                           f"Updates follow {spec_ref}" if spec_ref else "No configured image source reported",
                           evidence=src + [f"spec.image: {spec_ref or 'not reported'}",
                                           f"spec.bootOrder: {order or 'not reported'}"]))

    # Pending changes at next boot — never claim a reboot applies a download-only deployment.
    pend_ev = src + [f"rollbackQueued: {queued}", f"bootOrder: {order or 'not reported'}"]
    if staged:
        pend_ev.append(_describe("staged", staged))
    if queued:
        pend = finding("system.bootc.pending", CAT, "Next boot", WARN if not rollback else INFO,
                       "Rollback queued: the next boot starts the previous deployment",
                       evidence=pend_ev + ([_describe("rollback", rollback)] if rollback else []),
                       reason="" if rollback else "rollbackQueued is set but no rollback deployment is listed.",
                       guidance=["This was requested earlier (for example from FE-Xico Updater). "
                                 "Doctor does not change it."])
    elif staged and staged.get("downloadOnly"):
        pend = finding("system.bootc.pending", CAT, "Next boot", INFO,
                       "Download-only deployment present: it will NOT be applied at reboot",
                       evidence=pend_ev,
                       guidance=["A locked download-only deployment is preserved for review; rebooting "
                                 "keeps the current deployment.",
                                 "Use FE-Xico Updater to review or apply it deliberately."])
    elif staged:
        pend = finding("system.bootc.pending", CAT, "Next boot", INFO,
                       "Update staged: it applies at the next reboot",
                       evidence=pend_ev, guidance=["Reboot when convenient; nothing reboots automatically."])
    else:
        pend = finding("system.bootc.pending", CAT, "Next boot", PASS,
                       "Nothing staged; the next boot uses the current deployment", evidence=pend_ev)
    out.append(pend)

    # Rollback availability.
    if rollback:
        out.append(finding("system.bootc.rollback", CAT, "Rollback deployment", PASS,
                           "Previous deployment available for rollback",
                           evidence=src + [_describe("rollback", rollback)]))
    else:
        out.append(finding("system.bootc.rollback", CAT, "Rollback deployment", INFO,
                           "No rollback deployment yet",
                           evidence=src + ["status.rollback: null"],
                           guidance=["Normal on a fresh install or after cleanup; one appears after the "
                                     "next completed update."]))

    # Pinned recovery deployments (bootc keeps these across updates).
    pinned = [e for e in (booted, staged, rollback, *others) if isinstance(e, dict) and e.get("pinned")]
    pin_ev = src + [_describe("pinned", e) for e in pinned] + [f"other deployments: {len(others)}"]
    out.append(finding("system.bootc.pinned", CAT, "Pinned recovery", PASS if pinned else INFO,
                       f"{len(pinned)} pinned deployment(s)" if pinned else "No pinned deployment",
                       evidence=pin_ev,
                       guidance=[] if pinned else [
                           "Optional. A pinned deployment survives updates as an extra recovery point "
                           "(`ostree admin pin`, run deliberately as administrator)."]))
    return out


def _from_updater(ctx):
    """Newest trusted bootc status recorded by FE-Xico Updater's root service this boot."""
    best = None
    for action in ("status", "check", "upgrade", "rollback"):
        path = f"{UPDATER_RUNTIME}/{action}.json"
        data, why = ctx.json_file(path, limit=4 * 1024 * 1024)
        if not isinstance(data, dict) or data.get("protocol") != 1 or not isinstance(data.get("status"), dict):
            continue
        mtime = ctx.mtime(path)
        if mtime and (best is None or mtime > best[0]):
            best = (mtime, action, data["status"])
    return best


@probe("system.bootc", CAT, "bootc deployments")
def bootc(ctx):
    res = ctx.runner.run("bootc", "status", "--json", "--format-version=1")
    direct_reason = res.describe()
    if res.ok:
        try:
            return bootc_findings(json.loads(res.stdout), "`bootc status --json --format-version=1` (live)", ctx)
        except ValueError as exc:
            return [finding("system.bootc.booted", CAT, "Booted deployment", UNKNOWN,
                            "bootc status returned an unsupported document",
                            reason=f"{exc}", evidence=[direct_reason],
                            guidance=["A newer or older bootc may use a different schema. "
                                      "Run `bootc status` in Foot to read it directly."])]
    if res.status == "missing":
        return [finding("system.bootc.booted", CAT, "Booted deployment", UNKNOWN,
                        "bootc is not installed; not a bootc system",
                        reason=direct_reason)]
    # Unprivileged users are commonly refused. Fall back to the updater's root-written snapshot.
    cached = _from_updater(ctx)
    if cached:
        mtime, action, host = cached
        try:
            when = mtime.astimezone().strftime("%Y-%m-%d %H:%M")
            return bootc_findings(host, f"FE-Xico Updater `{action}` snapshot from this boot, {when} "
                                        f"(live query unavailable: {direct_reason})", ctx)
        except ValueError:
            pass
    staged = ctx.exists("/run/ostree/staged-deployment")
    locked = ctx.exists("/run/ostree/staged-deployment-locked")
    booted = ctx.exists("/run/ostree-booted")
    ev = [direct_reason, f"/run/ostree-booted: {'present' if booted else 'absent'}",
          f"/run/ostree/staged-deployment: {'present' if staged else 'absent'}",
          f"/run/ostree/staged-deployment-locked: {'present' if locked else 'absent'}"]
    return [finding("system.bootc.booted", CAT, "Booted deployment", UNKNOWN,
                    "bootc status unavailable to this user" + ("; a deployment is staged" if staged else ""),
                    reason="bootc refused the unprivileged query and no FE-Xico Updater snapshot exists "
                           "for this boot.", evidence=ev,
                    guidance=["Open FE-Xico Updater → Status (it queries bootc through its own "
                              "authorized service), then press Refresh here.",
                              "Or run `sudo bootc status` yourself in Foot. Doctor never elevates."])]
