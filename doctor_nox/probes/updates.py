"""FE-Xico Updater, automatic update policy and recent update results.

Doctor reads state only. It never starts the updater's services, never
touches its transaction lock, never changes timers or active hours.
"""
from __future__ import annotations

import configparser
import datetime
import os
import stat

from ..model import ERROR, INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "updates"
SAFE_TIMER = "fe-xicoos-auto-update.timer"
LEGACY_TIMER = "bootc-fetch-apply-updates.timer"
MIGRATE = "fe-xicoos-auto-update-migrate.service"
SETTINGS = "/var/lib/fe-xicoos-auto-updates/settings.json"
EVENT = "/var/lib/fe-xicoos-auto-updates/event.json"
RUNTIME = "/run/fe-xicoos-updater"
BUILD_MARKER = "/usr/share/fe-xicoos/updater-build-status"
HELPER = "/usr/libexec/fe-xicoos-updater-helper"
#: Same search order as FE-Xico Updater's findConfigFile() (src/components/kConfig/k_config.cpp).
CONFIG_BASES = ("/etc", "/usr/local/etc", "/usr/etc", "/etc/xdg", "/usr/etc/xdg", "/usr/local/etc/xdg")
CONFIG_REL = "fe-xico-updater/config.ini"
EXPECTED_COMMANDS = {
    "systemUpdateCommand": f"{HELPER} fe-xicoos-upgrade.service",
    "systemRollbackCommand": f"{HELPER} fe-xicoos-rollback.service",
    "systemCheckCommand": f"{HELPER} fe-xicoos-check.service",
    "systemStatusCommand": f"{HELPER} fe-xicoos-status.service",
}
DEFAULT_HOURS = (9, 23)  # fe-xicoos-auto-update-worker default when settings.json is absent


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


ENABLED = ("enabled", "enabled-runtime", "static", "indirect", "alias", "linked", "linked-runtime")
OFF = ("disabled", "masked", "masked-runtime")


@probe("updates.timers", CAT, "Automatic update timers")
def timers(ctx):
    out = []
    safe, res_safe = unit_file_state(ctx, SAFE_TIMER)
    ev = [f"{SAFE_TIMER}: {safe or 'unknown'}", res_safe.describe()]
    if not safe:
        out.append(finding("updates.auto_timer", CAT, "Automatic checks", UNKNOWN,
                           "Could not read the automatic-update timer", reason=res_safe.describe(), evidence=ev))
    elif safe in ENABLED:
        out.append(finding("updates.auto_timer", CAT, "Automatic checks", PASS,
                           "Automatic checks on: hourly outside active hours, never reboots", evidence=ev))
    elif safe in OFF:
        out.append(finding("updates.auto_timer", CAT, "Automatic checks", INFO,
                           "Manual-update mode (automatic checks are off)", evidence=ev,
                           guidance=["This is a choice, not a fault. Turn automatic checks on in "
                                     "FE-Xico Updater → Settings if you want them."]))
    elif safe == "not-found":
        out.append(finding("updates.auto_timer", CAT, "Automatic checks", WARN,
                           f"{SAFE_TIMER} is not installed in this image", evidence=ev,
                           guidance=["The image may predate safe automatic updates, or the build dropped "
                                     "the unit. Rebuild/update the image."]))
    else:
        out.append(finding("updates.auto_timer", CAT, "Automatic checks", UNKNOWN,
                           f"Unrecognized timer state: {safe}", evidence=ev,
                           reason="systemctl reported a state Doctor does not classify."))

    legacy, res_legacy = unit_file_state(ctx, LEGACY_TIMER)
    ev = [f"{LEGACY_TIMER}: {legacy or 'unknown'}", res_legacy.describe()]
    guidance_legacy = ["The legacy bootc timer applies updates AND reboots unattended. FE-XicoOS masks it "
                       "at every boot via " + MIGRATE + ".",
                       f"To inspect: `systemctl status {MIGRATE}` and `journalctl -b -u {MIGRATE}`.",
                       "Re-enabling automatic checks in FE-Xico Updater masks it again."]
    if not legacy:
        out.append(finding("updates.legacy_timer", CAT, "Legacy auto-reboot timer", UNKNOWN,
                           "Could not read the legacy bootc timer", reason=res_legacy.describe(), evidence=ev))
    elif legacy in ("masked", "masked-runtime", "not-found"):
        out.append(finding("updates.legacy_timer", CAT, "Legacy auto-reboot timer", PASS,
                           "Legacy bootc auto-reboot timer is masked" if legacy != "not-found"
                           else "Legacy bootc auto-reboot timer is not present", evidence=ev))
    elif legacy in ENABLED:
        out.append(finding("updates.legacy_timer", CAT, "Legacy auto-reboot timer", ERROR,
                           "Legacy bootc timer is ENABLED and can reboot this PC unattended",
                           evidence=ev, guidance=guidance_legacy))
    else:  # disabled but not masked: inactive now, but the boot-time migration did not mask it
        out.append(finding("updates.legacy_timer", CAT, "Legacy auto-reboot timer", WARN,
                           f"Legacy bootc timer is {legacy}, not masked", evidence=ev,
                           guidance=guidance_legacy))

    props, res_mig = unit_show(ctx, MIGRATE)
    ev = [f"{MIGRATE}: " + ", ".join(f"{k}={props[k]}" for k in ("LoadState", "ActiveState", "Result",
                                                                   "ExecMainStatus") if k in props)
          if props else res_mig.describe()]
    if not props:
        out.append(finding("updates.migration", CAT, "Update-policy migration", UNKNOWN,
                           "Could not read the migration service", reason=res_mig.describe(), evidence=ev))
    elif props.get("LoadState") == "not-found":
        out.append(finding("updates.migration", CAT, "Update-policy migration", INFO,
                           "Migration service not present in this image", evidence=ev))
    elif props.get("Result") not in (None, "", "success") or props.get("ActiveState") == "failed":
        out.append(finding("updates.migration", CAT, "Update-policy migration", WARN,
                           "The boot-time update-policy migration failed", evidence=ev,
                           guidance=[f"`journalctl -b -u {MIGRATE}` shows why."]))
    else:
        out.append(finding("updates.migration", CAT, "Update-policy migration", PASS,
                           "Boot-time update-policy migration completed", evidence=ev))
    return out


def _is_active_hour(hour, start, end):
    if start == end:
        return True
    return start <= hour < end if start < end else hour >= start or hour < end


@probe("updates.active_hours", CAT, "Active hours")
def active_hours(ctx):
    data, why = ctx.json_file(SETTINGS, limit=4096)
    local_hour = ctx.now.astimezone().hour
    if why == "missing":
        start, end = DEFAULT_HOURS
        source = f"{SETTINGS} absent → worker default"
    elif data is None:
        return finding("updates.active_hours", CAT, "Active hours", WARN,
                       "Active-hours settings are not usable", reason=f"{SETTINGS}: {why}",
                       evidence=[f"{SETTINGS}: {why}"],
                       guidance=["The automatic-update worker refuses unsafe or malformed settings and "
                                 "records an error. Set active hours again in FE-Xico Updater."])
    else:
        start, end = (data.get("active_start"), data.get("active_end")) if isinstance(data, dict) else (None, None)
        if type(start) is not int or type(end) is not int or not (0 <= start <= 23 and 0 <= end <= 23):
            return finding("updates.active_hours", CAT, "Active hours", WARN,
                           "Active-hours settings are invalid", evidence=[f"{SETTINGS}: {data!r}"[:300]],
                           guidance=["Expected whole hours 0-23. Set them again in FE-Xico Updater."])
        source = SETTINGS
    inside = _is_active_hour(local_hour, start, end)
    span = "all day (no automatic checks)" if start == end else f"{start:02d}:00–{end:02d}:00"
    return finding("updates.active_hours", CAT, "Active hours", PASS,
                   f"Protected {span}; now {'inside' if inside else 'outside'} active hours",
                   evidence=[f"source: {source}", f"active_start={start} active_end={end}",
                             f"local hour now: {local_hour:02d}"],
                   guidance=["Automatic checks and staging only run outside active hours."])


def _age(created, now):
    try:
        then = datetime.datetime.fromtimestamp(int(created), datetime.timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return "unknown time"
    hours = (now - then).total_seconds() / 3600
    when = then.astimezone().strftime("%Y-%m-%d %H:%M")
    return f"{when} ({hours:.0f} h ago)" if hours < 48 else f"{when} ({hours / 24:.0f} days ago)"


@probe("updates.results", CAT, "Recent update results")
def results(ctx):
    out = []
    event, why = ctx.json_file(EVENT, limit=8192)
    if why == "missing":
        out.append(finding("updates.auto_result", CAT, "Last automatic run", INFO,
                           "No automatic update result recorded yet", evidence=[f"{EVENT}: absent"]))
    elif not isinstance(event, dict):
        out.append(finding("updates.auto_result", CAT, "Last automatic run", UNKNOWN,
                           "Automatic update result is unreadable", reason=f"{EVENT}: {why}"))
    else:
        kind, msg = str(event.get("kind", "")), str(event.get("message", ""))[:280]
        ev = [f"kind={kind}", f"title={str(event.get('title', ''))[:120]}", f"message={msg}",
              f"recorded {_age(event.get('created'), ctx.now)}"]
        sev, summary = {
            "none": (PASS, "Last automatic check found no update"),
            "found": (INFO, "Last automatic check found an update"),
            "ready": (INFO, "An automatically staged update is ready; restart when convenient"),
            "error": (WARN, "The last automatic update run failed"),
        }.get(kind, (UNKNOWN, f"Unrecognized result kind {kind!r}"))
        out.append(finding("updates.auto_result", CAT, "Last automatic run", sev, summary, evidence=ev,
                           guidance=["`journalctl -u fe-xicoos-auto-update.service` has the details."]
                           if kind == "error" else []))

    seen = []
    failed = []
    for action in ("status", "check", "upgrade", "rollback"):
        path = f"{RUNTIME}/{action}.json"
        data, why = ctx.json_file(path, limit=4 * 1024 * 1024)
        if why == "missing":
            continue
        if not isinstance(data, dict):
            seen.append(f"{action}: unreadable ({why})")
            continue
        mtime = ctx.mtime(path)
        stamp = mtime.astimezone().strftime("%H:%M") if mtime else "?"
        outcome = str(data.get("outcome", "?"))
        seen.append(f"{action} at {stamp}: outcome={outcome}, rebootRequired={data.get('rebootRequired')}")
        if outcome == "failed":
            failed.append(f"{action}: {str(data.get('message', ''))[:200]}")
    if not seen:
        out.append(finding("updates.updater_result", CAT, "Updater operations this boot", INFO,
                           "No FE-Xico Updater operation has run since boot",
                           evidence=[f"{RUNTIME}: no result files"]))
    elif failed:
        out.append(finding("updates.updater_result", CAT, "Updater operations this boot", WARN,
                           f"{len(failed)} updater operation(s) failed this boot", evidence=seen + failed,
                           guidance=["`journalctl -b -u fe-xicoos-upgrade.service` (or check/status/"
                                     "rollback) explains the failure.", "Doctor does not retry operations."]))
    else:
        out.append(finding("updates.updater_result", CAT, "Updater operations this boot", PASS,
                           "Updater operations this boot completed", evidence=seen))
    return out


def _config_candidates(ctx):
    """Mirror the updater: first existing, root-owned, not group/world-writable, readable file wins.

    Like QFileInfo, ownership/permissions are those of the symlink target.
    """
    rows, chosen = [], None
    for base in CONFIG_BASES:
        path = f"{base}/{CONFIG_REL}"
        p = ctx.path(path)
        try:
            info = os.stat(p)
        except FileNotFoundError:
            continue
        except OSError as exc:
            rows.append((path, f"unreadable ({exc.strerror})", False))
            continue
        mode = stat.S_IMODE(info.st_mode)
        if info.st_uid != ctx.trusted_uid or mode & 0o022:
            rows.append((path, f"IGNORED by updater: uid={info.st_uid} mode={mode:04o} "
                               f"(needs root owner, no group/world write)", False))
            continue
        if not os.access(p, os.R_OK):
            rows.append((path, "not readable", False))
            continue
        rows.append((path, f"trusted (uid=0 mode={mode:04o})", chosen is None))
        if chosen is None:
            chosen = path
    return rows, chosen


@probe("updates.updater", CAT, "FE-Xico Updater")
def updater(ctx):
    marker = (ctx.try_text(BUILD_MARKER, 64) or "").strip()
    exe = ctx.exists("/usr/bin/fe-xico-updater")
    ev = [f"{BUILD_MARKER}: {marker or 'absent'}", f"/usr/bin/fe-xico-updater: {'present' if exe else 'absent'}",
          f"{HELPER}: {'present' if ctx.exists(HELPER) else 'absent'}"]
    if marker == "skipped" and not exe:
        return finding("updates.updater", CAT, "FE-Xico Updater", INFO,
                       "FE-Xico Updater was not included in this image build (optional)", evidence=ev,
                       guidance=["Updates still work with `sudo bootc upgrade` in Foot."])
    if not exe:
        return finding("updates.updater", CAT, "FE-Xico Updater", WARN if marker == "installed" else UNKNOWN,
                       "Updater marked installed but its executable is missing" if marker == "installed"
                       else "FE-Xico Updater installation state unknown", evidence=ev)
    rows, chosen = _config_candidates(ctx)
    ev += [f"{path}: {state}" + (" ← used" if used else "") for path, state, used in rows]
    ignored = [path for path, state, _ in rows if state.startswith("IGNORED")]
    if not chosen:
        return finding("updates.updater", CAT, "FE-Xico Updater", ERROR,
                       "Updater has no trusted configuration; its update actions are unavailable",
                       evidence=ev, reason="No config.ini passed the updater's ownership/permission check.",
                       guidance=_perm_guidance(ignored))
    text = ctx.try_text(chosen, 64 * 1024)
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    try:
        parser.read_string(text or "")
    except configparser.Error as exc:
        return finding("updates.updater", CAT, "FE-Xico Updater", WARN, "Updater configuration is malformed",
                       evidence=ev, reason=str(exc).splitlines()[0][:200])
    commands = dict(parser.items("Commands")) if parser.has_section("Commands") else {}
    missing = [k for k in EXPECTED_COMMANDS if not commands.get(k, "").strip()]
    custom = [k for k, v in EXPECTED_COMMANDS.items() if commands.get(k, "").strip() not in ("", v)]
    # Show command *names* only; values are never executed or echoed.
    ev += [f"[Commands] {k}: " + ("missing" if k in missing else "custom value" if k in custom else "FE-XicoOS default")
           for k in EXPECTED_COMMANDS]
    if missing:
        return finding("updates.updater", CAT, "FE-Xico Updater", WARN,
                       f"Updater configuration lacks {len(missing)} command(s)", evidence=ev,
                       guidance=["Compare with the image default in /usr/etc/fe-xico-updater/config.ini."])
    if ignored:
        return finding("updates.updater", CAT, "FE-Xico Updater", WARN,
                       f"Updater ignores {ignored[0]} (unsafe permissions) and uses {chosen}",
                       evidence=ev, guidance=_perm_guidance(ignored))
    if custom:
        return finding("updates.updater", CAT, "FE-Xico Updater", WARN,
                       "Updater uses customized system commands", evidence=ev,
                       guidance=["Custom commands run with the updater's authorization. Make sure you "
                                 "set them deliberately. Doctor never runs them."])
    return finding("updates.updater", CAT, "FE-Xico Updater", PASS,
                   "Installed; trusted configuration with FE-XicoOS default commands", evidence=ev)


def _perm_guidance(ignored):
    steps = ["The updater only trusts config files owned by root and not writable by group/others "
             "(it refuses e.g. mode 0777)."]
    for path in ignored[:2]:
        steps.append(f"Fix by hand if you agree: `sudo chown root:root {path} && sudo chmod 0644 {path}`. "
                     "Doctor never changes permissions.")
    return steps


@probe("updates.notifier", CAT, "Update notifications")
def notifier(ctx):
    state, res = unit_file_state(ctx, "fe-xicoos-update-notifier.timer", user=True)
    ev = [f"user fe-xicoos-update-notifier.timer: {state or 'unknown'}", res.describe()]
    if not state:
        return finding("updates.notifier", CAT, "Update notifications", UNKNOWN,
                       "User session manager not reachable", reason=res.describe(), evidence=ev,
                       guidance=["Expected when Doctor runs outside a desktop session (e.g. over SSH)."])
    if state in ENABLED:
        return finding("updates.notifier", CAT, "Update notifications", PASS,
                       "Desktop notifications for automatic updates are on", evidence=ev)
    if state == "not-found":
        return finding("updates.notifier", CAT, "Update notifications", INFO,
                       "Update notifier not installed for this user", evidence=ev)
    return finding("updates.notifier", CAT, "Update notifications", INFO,
                   f"Update notifier is {state}; automatic results will not pop up", evidence=ev)
