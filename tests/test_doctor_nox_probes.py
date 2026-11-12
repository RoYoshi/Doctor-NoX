"""Doctor NoX probe behaviour on synthetic filesystems and command output.

Every command a probe asks for passes through FakeRunner, which enforces the
real allowlist. Nothing here runs bootc, systemctl, journalctl or flatpak.
"""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_testlib import (KERNEL, MISSING, TIMEOUT, FakeRunner, Tree, bootc_entry, bootc_host,  # noqa: E402
                            exit_, fe_xico_os_release, ok)

from doctor_nox.probes import hardware, logs, network, security, software, storage, system, updates  # noqa: E402

BOOTC = ("bootc", "status", "--json", "--format-version=1")


def by_id(findings):
    if not isinstance(findings, list):
        findings = [findings]
    return {f.id: f for f in findings}


class Base(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)

    def ctx(self, answers=None, default=MISSING, **kw):
        self.runner = FakeRunner(answers, default)
        return self.tree.context(self.runner, **kw)


# ── System & bootc ───────────────────────────────────────────────────────────
class IdentityTests(Base):
    def test_fe_xico_identity_and_host_summary(self):
        fe_xico_os_release(self.tree)
        self.tree.write("/usr/lib/modprobe.d/80-fe-xico-nvidia-open.conf", "blacklist nouveau\n")
        ctx = self.ctx()
        f = system.identity(ctx)
        self.assertEqual(f.severity, "pass")
        self.assertIn("44.20261030.0", f.summary)
        self.assertIn(KERNEL, f.summary)
        self.assertEqual(system.host_summary(ctx)["image_variant"], "nvidia-open")

    def test_other_os_and_missing_os_release(self):
        self.tree.write("/usr/lib/os-release", 'NAME="Fedora Linux"\nVERSION_ID=44\n')
        self.assertEqual(system.identity(self.ctx()).severity, "warn")
        self.tree.p("/usr/lib/os-release").unlink()
        self.assertEqual(system.identity(self.ctx()).severity, "unknown")


class OsReleaseDriftTests(Base):
    def test_symlink_matching_copy_and_stale_copy(self):
        fe_xico_os_release(self.tree)
        self.tree.link("/etc/os-release", "../usr/lib/os-release")
        self.assertEqual(system.os_release_drift(self.ctx()).severity, "pass")
        self.tree.p("/etc/os-release").unlink()
        self.tree.write("/etc/os-release", self.tree.p("/usr/lib/os-release").read_text())
        self.assertEqual(system.os_release_drift(self.ctx()).severity, "pass")
        # the FE-ROYO case: OSTree /etc merge kept an old pre-Fedora-native file
        self.tree.write("/etc/os-release", 'NAME="FE-Xico"\nVERSION_ID="1.0"\nVARIANT="NVIDIA Edition"\n')
        f = system.os_release_drift(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("VERSION_ID" in e and "'1.0'" in e for e in f.evidence))


class BootcTests(Base):
    def scan(self, host):
        return by_id(system.bootc(self.ctx({BOOTC: ok(json.dumps(host))})))

    def test_booted_with_rollback_and_nothing_staged(self):
        f = self.scan(bootc_host())
        self.assertEqual(f["system.bootc.booted"].severity, "pass")
        self.assertIn("44.20261030.0", f["system.bootc.booted"].summary)
        self.assertEqual(f["system.bootc.rollback"].severity, "pass")
        self.assertEqual(f["system.bootc.pending"].severity, "pass")
        self.assertEqual(f["system.bootc.source"].severity, "pass")
        self.assertTrue(any("3 days ago" in e for e in f["system.bootc.booted"].evidence))

    def test_no_rollback_is_info_not_failure(self):
        f = self.scan(bootc_host(rollback=False))
        self.assertEqual(f["system.bootc.rollback"].severity, "info")

    def test_staged_update_applies_at_reboot(self):
        f = self.scan(bootc_host(staged=bootc_entry("44.20261031.0", "d", download_only=False)))
        self.assertEqual(f["system.bootc.pending"].severity, "info")
        self.assertIn("applies at the next reboot", f["system.bootc.pending"].summary)

    def test_download_only_is_never_called_reboot_applicable(self):
        f = self.scan(bootc_host(staged=bootc_entry("44.20261031.0", "d", download_only=True)))
        pending = f["system.bootc.pending"]
        self.assertIn("will NOT be applied at reboot", pending.summary)
        self.assertNotIn("applies at the next reboot", pending.summary)
        self.assertEqual(pending.severity, "info")

    def test_rollback_queued(self):
        f = self.scan(bootc_host(queued=True))
        self.assertIn("Rollback queued", f["system.bootc.pending"].summary)
        self.assertTrue(any("bootOrder: rollback" in e for e in f["system.bootc.pending"].evidence))

    def test_pinned_recovery_counted_across_all_slots(self):
        host = bootc_host(others=[bootc_entry("44.20260901.0", "e", pinned=True)])
        f = self.scan(host)
        self.assertEqual(f["system.bootc.pinned"].severity, "pass")
        self.assertIn("1 pinned", f["system.bootc.pinned"].summary)
        self.assertEqual(self.scan(bootc_host())["system.bootc.pinned"].severity, "info")

    def test_source_mismatch_and_pending_switch(self):
        other = "ghcr.io/royoshi/fe-xicoos:testing"
        self.assertEqual(self.scan(bootc_host(spec_ref=other))["system.bootc.source"].severity, "warn")
        f = self.scan(bootc_host(spec_ref=other, staged=bootc_entry("t", "d", ref=other)))
        self.assertEqual(f["system.bootc.source"].severity, "info")

    def test_incompatible_deployment(self):
        f = self.scan(bootc_host(booted=bootc_entry("44.1", "b", incompatible=True)))
        self.assertEqual(f["system.bootc.booted"].severity, "warn")

    def test_malformed_and_unsupported_documents_are_unknown(self):
        for payload in ("not json", json.dumps({"apiVersion": "org.containers.bootc/v2"}),
                        json.dumps({"apiVersion": "org.containers.bootc/v1", "status": {"booted": None}}),
                        json.dumps([1, 2])):
            f = by_id(system.bootc(self.ctx({BOOTC: ok(payload)})))
            self.assertEqual(f["system.bootc.booted"].severity, "unknown", payload)

    def test_missing_bootc(self):
        f = by_id(system.bootc(self.ctx()))
        self.assertEqual(f["system.bootc.booted"].severity, "unknown")
        self.assertIn("not a bootc system", f["system.bootc.booted"].summary)

    def test_permission_denied_uses_trusted_updater_snapshot(self):
        self.tree.mkdir("/run/fe-xicoos-updater", 0o755)
        self.tree.json("/run/fe-xicoos-updater/status.json",
                       {"protocol": 1, "action": "status", "outcome": "up-to-date", "status": bootc_host()})
        f = by_id(system.bootc(self.ctx({BOOTC: exit_(1, stderr="error: This command requires root privileges")})))
        self.assertEqual(f["system.bootc.booted"].severity, "pass")
        self.assertTrue(any("FE-Xico Updater `status` snapshot" in e for e in f["system.bootc.booted"].evidence))

    def test_untrusted_snapshot_is_ignored(self):
        self.tree.json("/run/fe-xicoos-updater/status.json",
                       {"protocol": 1, "action": "status", "status": bootc_host()}, mode=0o666)
        self.tree.write("/run/ostree/staged-deployment", "{}")
        f = by_id(system.bootc(self.ctx({BOOTC: exit_(1, stderr="requires root")})))
        self.assertEqual(f["system.bootc.booted"].severity, "unknown")
        self.assertIn("a deployment is staged", f["system.bootc.booted"].summary)
        self.assertTrue(f["system.bootc.booted"].guidance)


# ── Updates ──────────────────────────────────────────────────────────────────
def timers(safe, legacy, migrate="Result=success\nActiveState=inactive\nLoadState=loaded"):
    return {("systemctl", "is-enabled", "fe-xicoos-auto-update.timer"): safe,
            ("systemctl", "is-enabled", "bootc-fetch-apply-updates.timer"): legacy,
            ("systemctl", "show", "fe-xicoos-auto-update-migrate.service"): ok(migrate)}


class UpdatePolicyTests(Base):
    def run_timers(self, safe, legacy, **kw):
        return by_id(updates.timers(self.ctx(timers(safe, legacy, **kw))))

    def test_safe_enabled_legacy_masked(self):
        f = self.run_timers(ok("enabled\n"), exit_(1, "masked\n"))
        self.assertEqual(f["updates.auto_timer"].severity, "pass")
        self.assertEqual(f["updates.legacy_timer"].severity, "pass")
        self.assertEqual(f["updates.migration"].severity, "pass")

    def test_manual_mode_is_info_not_fault(self):
        f = self.run_timers(exit_(1, "masked\n"), exit_(1, "masked\n"))
        self.assertEqual(f["updates.auto_timer"].severity, "info")
        self.assertIn("Manual-update mode", f["updates.auto_timer"].summary)

    def test_legacy_enabled_is_error_and_unmasked_is_warn(self):
        self.assertEqual(self.run_timers(ok("enabled\n"), ok("enabled\n"))["updates.legacy_timer"].severity, "error")
        self.assertEqual(self.run_timers(ok("enabled\n"), exit_(1, "disabled\n"))["updates.legacy_timer"].severity,
                         "warn")
        self.assertEqual(self.run_timers(ok("enabled\n"),
                                         exit_(4, stderr="Failed to get unit file state: No such file"))
                         ["updates.legacy_timer"].severity, "pass")

    def test_unknown_timer_state(self):
        f = self.run_timers(TIMEOUT, TIMEOUT)
        self.assertEqual(f["updates.auto_timer"].severity, "unknown")
        self.assertEqual(f["updates.legacy_timer"].severity, "unknown")
        self.assertEqual(self.run_timers(ok("generated\n"), ok("masked\n"))["updates.auto_timer"].severity, "unknown")

    def test_failed_migration(self):
        f = self.run_timers(ok("enabled\n"), ok("masked\n"), migrate="LoadState=loaded\nActiveState=failed\nResult=exit-code")
        self.assertEqual(f["updates.migration"].severity, "warn")


class ActiveHoursTests(Base):
    def test_default_valid_and_invalid(self):
        self.assertIn("09:00–23:00", updates.active_hours(self.ctx()).summary)
        self.tree.json("/var/lib/fe-xicoos-auto-updates/settings.json", {"active_start": 22, "active_end": 6})
        f = updates.active_hours(self.ctx())
        self.assertEqual(f.severity, "pass")
        self.assertIn("22:00–06:00", f.summary)
        self.tree.json("/var/lib/fe-xicoos-auto-updates/settings.json", {"active_start": "9", "active_end": 30})
        self.assertEqual(updates.active_hours(self.ctx()).severity, "warn")
        self.tree.write("/var/lib/fe-xicoos-auto-updates/settings.json", "{broken")
        self.assertEqual(updates.active_hours(self.ctx()).severity, "warn")
        self.tree.json("/var/lib/fe-xicoos-auto-updates/settings.json", [1, 2])
        self.assertEqual(updates.active_hours(self.ctx()).severity, "warn")


class UpdateResultTests(Base):
    def test_auto_event_kinds(self):
        self.assertEqual(by_id(updates.results(self.ctx()))["updates.auto_result"].severity, "info")
        for kind, sev in (("none", "pass"), ("ready", "info"), ("found", "info"), ("error", "warn"), ("weird", "unknown")):
            self.tree.json("/var/lib/fe-xicoos-auto-updates/event.json",
                           {"kind": kind, "title": "t", "message": "m", "created": 1793400000})
            self.assertEqual(by_id(updates.results(self.ctx()))["updates.auto_result"].severity, sev, kind)

    def test_failed_updater_operation_this_boot(self):
        self.tree.json("/run/fe-xicoos-updater/upgrade.json",
                       {"protocol": 1, "action": "upgrade", "outcome": "failed", "message": "network unreachable",
                        "status": None})
        f = by_id(updates.results(self.ctx()))["updates.updater_result"]
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("network unreachable" in e for e in f.evidence))


DEFAULT_CONFIG = "[Commands]\n" + "\n".join(f"{k}={v}" for k, v in updates.EXPECTED_COMMANDS.items()) + "\n"


class UpdaterConfigTests(Base):
    def installed(self):
        self.tree.write("/usr/share/fe-xicoos/updater-build-status", "installed\n")
        self.tree.write("/usr/bin/fe-xico-updater", "", 0o755)
        self.tree.write("/usr/libexec/fe-xicoos-updater-helper", "", 0o755)

    def test_trusted_default_configuration(self):
        self.installed()
        self.tree.write("/etc/fe-xico-updater/config.ini", DEFAULT_CONFIG, 0o644)
        ctx = self.ctx()
        f = updates.updater(ctx)
        self.assertEqual(f.severity, "pass")
        self.assertEqual(self.runner.calls, [], "configured commands must never be executed")

    def test_world_writable_etc_falls_back_to_image_default(self):
        self.installed()
        self.tree.write("/etc/fe-xico-updater/config.ini", DEFAULT_CONFIG, 0o777)
        self.tree.write("/usr/etc/fe-xico-updater/config.ini", DEFAULT_CONFIG, 0o644)
        f = updates.updater(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertIn("ignores /etc/fe-xico-updater/config.ini", f.summary)
        self.assertIn("/usr/etc/fe-xico-updater/config.ini", f.summary)
        self.assertTrue(any("chmod 0644" in g for g in f.guidance))
        self.assertTrue(os.stat(self.tree.p("/etc/fe-xico-updater/config.ini")).st_mode & 0o002, "Doctor must not chmod")

    def test_no_trusted_configuration_is_error(self):
        self.installed()
        self.tree.write("/etc/fe-xico-updater/config.ini", DEFAULT_CONFIG, 0o777)
        self.assertEqual(updates.updater(self.ctx()).severity, "error")

    def test_custom_missing_and_malformed_commands(self):
        self.installed()
        cfg = self.tree.write("/etc/fe-xico-updater/config.ini",
                              DEFAULT_CONFIG.replace("fe-xicoos-upgrade.service", "evil.service; rm -rf /"), 0o644)
        f = updates.updater(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertFalse(any("rm -rf" in e for e in f.evidence), "command values are never echoed")
        cfg.write_text("[Commands]\nsystemCheckCommand=x\n")
        self.assertIn("lacks", updates.updater(self.ctx()).summary)
        cfg.write_text("[Commands\nbroken")
        self.assertEqual(updates.updater(self.ctx()).severity, "warn")

    def test_skipped_and_unknown_build(self):
        self.tree.write("/usr/share/fe-xicoos/updater-build-status", "skipped\n")
        self.assertEqual(updates.updater(self.ctx()).severity, "info")
        self.tree.write("/usr/share/fe-xicoos/updater-build-status", "")
        self.assertEqual(updates.updater(self.ctx()).severity, "unknown")

    def test_notifier_without_user_bus(self):
        f = updates.notifier(self.ctx({("systemctl", "--user", "is-enabled"): exit_(1, stderr="Failed to connect to bus")}))
        self.assertEqual(f.severity, "unknown")
        f = updates.notifier(self.ctx({("systemctl", "--user", "is-enabled"): ok("enabled\n")}))
        self.assertEqual(f.severity, "pass")


# ── Security & services ─────────────────────────────────────────────────────
FAILED_ARGS = ("list-units", "--state=failed", "--all", "--no-legend", "--plain", "--no-pager", "--full")


class SecurityTests(Base):
    def test_selinux_modes(self):
        self.tree.write("/sys/fs/selinux/enforce", "1")
        self.assertEqual(security.selinux(self.ctx()).severity, "pass")
        self.tree.write("/sys/fs/selinux/enforce", "0")
        self.assertEqual(security.selinux(self.ctx()).severity, "warn")

    def test_selinux_disabled_and_unknown(self):
        f = security.selinux(self.ctx({("getenforce",): ok("Disabled\n")}))
        self.assertEqual(f.severity, "error")
        self.tree.mkdir("/sys/fs/selinux")
        self.assertEqual(security.selinux(self.ctx({("getenforce",): exit_(1)})).severity, "unknown")

    def test_failed_units(self):
        answers = {("systemctl", *FAILED_ARGS): ok(""), ("systemctl", "--user", *FAILED_ARGS): ok(""),
                   ("systemctl", "is-system-running"): ok("running\n")}
        self.assertEqual(security.failed_units(self.ctx(answers)).severity, "pass")
        answers[("systemctl", *FAILED_ARGS)] = ok(
            "fe-xicoos-flatpaks.service loaded failed failed Provision FE-XicoOS default Flatpaks\n")
        answers[("systemctl", "--user", *FAILED_ARGS)] = ok("app-x.service loaded failed failed X\n")
        f = security.failed_units(self.ctx(answers))
        self.assertEqual(f.severity, "warn")
        self.assertIn("2 failed unit(s)", f.summary)
        self.assertTrue(any("fe-xicoos-flatpaks.service" in g for g in f.guidance))
        self.assertEqual(security.failed_units(self.ctx({})).severity, "unknown")

    def test_state_directory_permissions(self):
        self.tree.mkdir("/var/lib/fe-xicoos-auto-updates", 0o755)
        self.assertEqual(security.state_permissions(self.ctx()).severity, "pass")
        self.tree.mkdir("/run/fe-xicoos-updater", 0o777)
        f = security.state_permissions(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertIn("/run/fe-xicoos-updater", f.summary)


# ── Logs ─────────────────────────────────────────────────────────────────────
def journal(*entries):
    return "\n".join(json.dumps(e) for e in entries) + "\n"


BOOT_ARGS = ("journalctl", "--boot=0", "--priority=0..3", "--no-pager", "--output=json", "--lines=400")


class LogTests(Base):
    def setUp(self):
        super().setUp()
        self.tree.mkdir("/var/log/journal/0123")
        self.tree.write("/var/log/journal/0123/system.journal", "x")

    def test_no_journal_is_unknown_not_pass(self):
        self.tree.cleanup()
        self.tree = Tree()
        self.assertEqual(logs.boot(self.ctx({BOOT_ARGS: ok("")})).severity, "unknown")

    def test_error_and_critical_classification(self):
        errs = journal({"PRIORITY": "3", "SYSLOG_IDENTIFIER": "kernel", "MESSAGE": "firmware bug"})
        self.assertEqual(logs.boot(self.ctx({BOOT_ARGS: ok(errs)})).severity, "info")
        crit = errs + journal({"PRIORITY": "2", "SYSLOG_IDENTIFIER": "kernel", "MESSAGE": [104, 105, 255]})
        f = logs.boot(self.ctx({BOOT_ARGS: ok(crit)}))
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("hi" in e for e in f.evidence), "byte-array messages are decoded")
        self.assertEqual(logs.boot(self.ctx({BOOT_ARGS: ok("")})).severity, "pass")

    def test_partial_access_is_reported(self):
        hint = "Hint: You are currently not seeing messages from other users and the system."
        self.assertEqual(logs.boot(self.ctx({BOOT_ARGS: ok("", hint)})).severity, "unknown")

    def test_unreadable_journal_and_service_logs(self):
        self.assertEqual(logs.boot(self.ctx({BOOT_ARGS: TIMEOUT})).severity, "unknown")
        unit_err = journal({"PRIORITY": "3", "SYSLOG_IDENTIFIER": "python3", "MESSAGE": "Failed upgrade"})
        f = logs.update_units(self.ctx({("journalctl", "--boot=0", "--unit=fe-xicoos-upgrade.service"): ok(unit_err),
                                         ("journalctl", "--boot=0"): ok("")}))
        self.assertEqual(f.severity, "warn")
        self.assertEqual(logs.update_units(self.ctx({("journalctl", "--boot=0"): ok("")})).severity, "pass")


# ── Hardware & gaming ───────────────────────────────────────────────────────
class GpuTests(Base):
    def gpu(self, vendor, driver, card="card1", slot="0000:01:00.0"):
        base = f"/sys/devices/pci0000:00/{slot}"
        self.tree.write(f"{base}/vendor", vendor + "\n")
        self.tree.write(f"{base}/device", "0x2684\n")
        if driver:
            self.tree.mkdir(f"/sys/bus/pci/drivers/{driver}")
            self.tree.link(f"{base}/driver", str(self.tree.p(f"/sys/bus/pci/drivers/{driver}")))
        self.tree.mkdir(f"/sys/class/drm/{card}")
        self.tree.link(f"/sys/class/drm/{card}/device", str(self.tree.p(base)))

    def test_nvidia_open_on_nvidia_image(self):
        self.gpu("0x10de", "nvidia")
        self.tree.write("/usr/lib/modprobe.d/80-fe-xico-nvidia-open.conf", "")
        self.tree.write("/proc/driver/nvidia/version",
                        "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.95.05\n")
        f = hardware.graphics(self.ctx())
        self.assertEqual(f.severity, "pass")
        self.assertIn("NVIDIA Open", f.summary)

    def test_open_detected_from_modinfo_license(self):
        self.gpu("0x10de", "nvidia")
        f = hardware.graphics(self.ctx({("modinfo", "-k", KERNEL, "-F", "license", "nvidia"): ok("Dual MIT/GPL\n")}))
        self.assertIn("NVIDIA Open", f.summary)

    def test_nouveau_nvk_is_valid_on_standard_image(self):
        self.gpu("0x10de", "nouveau")
        self.tree.write("/usr/share/vulkan/icd.d/nouveau_icd.x86_64.json", "{}")
        f = hardware.graphics(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertIn("NVK", f.summary)

    def test_nouveau_on_nvidia_open_image_warns(self):
        self.gpu("0x10de", "nouveau")
        self.tree.write("/usr/lib/modprobe.d/80-fe-xico-nvidia-open.conf", "")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "warn")

    def test_amd_intel_and_virtual_do_not_false_positive(self):
        self.gpu("0x1002", "amdgpu")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "pass")
        self.gpu("0x8086", "i915", card="card2", slot="0000:00:02.0")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "pass")
        self.gpu("0x1af4", "virtio-pci", card="card3", slot="0000:00:03.0")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "info")

    def test_unbound_gpu_warns(self):
        self.gpu("0x1002", "")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "warn")

    def test_nvidia_runtime(self):
        self.assertIn("not applicable", hardware.nvidia(self.ctx()).summary)
        self.assertNotIn(("nvidia-smi",), [c[:1] for c in self.runner.calls])
        self.gpu("0x10de", "nvidia")
        self.tree.write("/sys/module/nvidia/version", "580.95.05\n")
        smi = ("nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader")
        self.assertEqual(hardware.nvidia(self.ctx({smi: ok("NVIDIA GeForce RTX 4080, 580.95.05\n")})).severity, "pass")
        self.assertEqual(hardware.nvidia(self.ctx({smi: ok("NVIDIA GeForce RTX 4080, 575.64\n")})).severity, "warn")
        self.assertEqual(hardware.nvidia(self.ctx({smi: exit_(9, stderr="NVIDIA-SMI has failed")})).severity, "warn")


EXTRA_EXACT = ("rpm", "-q", "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n", f"kernel-modules-extra-{KERNEL}")
EXTRA_ANY = ("rpm", "-q", "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n", "kernel-modules-extra")


class ControllerTests(Base):
    def usb(self, dev, vid, pid, product, iface_key, driver=""):
        base = f"/sys/bus/usb/devices/{dev}"
        self.tree.write(f"{base}/idVendor", vid)
        self.tree.write(f"{base}/idProduct", pid)
        self.tree.write(f"{base}/product", product)
        ib = f"/sys/bus/usb/devices/{dev}:1.0"
        for attr, val in zip(("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"), iface_key, strict=True):
            self.tree.write(f"{ib}/{attr}", val)
        if driver:
            self.tree.mkdir(f"/sys/bus/usb/drivers/{driver}")
            self.tree.link(f"{ib}/driver", str(self.tree.p(f"/sys/bus/usb/drivers/{driver}")))

    def modules(self, xpad):
        self.tree.write(f"/usr/lib/modules/{KERNEL}/modules.builtin", "kernel/drivers/hid/hid-generic.ko\n")
        dep = "kernel/drivers/input/joystick/xpad.ko.xz: kernel/drivers/input/ff-memless.ko.xz\n" if xpad else ""
        self.tree.write(f"/usr/lib/modules/{KERNEL}/modules.dep", dep + "kernel/fs/x.ko.xz:\n")

    def test_xpad_loaded(self):
        self.tree.mkdir("/sys/module/xpad")
        f = hardware.controllers(self.ctx({EXTRA_EXACT: ok("kernel-modules-extra 6.17.4-300.fc44.x86_64\n")}))
        self.assertEqual(f.severity, "pass")
        self.assertIn("xpad loaded", f.summary)

    def test_xpad_available_not_loaded(self):
        self.modules(xpad=True)
        f = hardware.controllers(self.ctx({EXTRA_EXACT: ok("kernel-modules-extra x\n")}))
        self.assertEqual(f.severity, "pass")
        self.assertIn("available", f.summary)

    def test_missing_xpad_without_xbox_device_is_info(self):
        self.modules(xpad=False)
        f = hardware.controllers(self.ctx({EXTRA_EXACT: exit_(1), EXTRA_ANY: exit_(1)}))
        self.assertEqual(f.severity, "info")
        self.assertIn("kernel-modules-extra not installed", f.summary)

    def test_xbox_protocol_device_without_xpad_warns_with_kernel_specific_reason(self):
        self.modules(xpad=False)
        self.usb("1-4", "2e24", "0652", "Hyperkin Duke", ("ff", "5d", "01"))
        f = hardware.controllers(self.ctx({EXTRA_EXACT: exit_(1), EXTRA_ANY: ok("kernel-modules-extra 6.16.9\n")}))
        self.assertEqual(f.severity, "warn")
        self.assertIn(f"kernel-modules-extra-{KERNEL}", f.reason)
        self.assertTrue(any("(Hyperkin)" in e for e in f.evidence))
        self.assertTrue(any("installed for other kernel" in e for e in f.evidence))

    def test_hid_mode_hyperkin_is_not_assumed_to_need_xpad(self):
        self.modules(xpad=False)
        self.usb("1-4", "2e24", "1688", "Hyperkin pad", ("03", "00", "00"), driver="usbhid")
        f = hardware.controllers(self.ctx({EXTRA_EXACT: exit_(1), EXTRA_ANY: exit_(1)}))
        self.assertEqual(f.severity, "info")

    def test_joysticks_listed(self):
        self.tree.mkdir("/sys/module/xpad")
        self.tree.write("/proc/bus/input/devices",
                        'I: Bus=0003 Vendor=045e Product=028e Version=0110\nN: Name="Microsoft X-Box 360 pad"\n'
                        "H: Handlers=event20 js0\n\n")
        f = hardware.controllers(self.ctx())
        self.assertIn("1 gamepad", f.summary)


class OtherHardwareTests(Base):
    def test_cpu_memory(self):
        self.tree.write("/proc/cpuinfo", "processor\t: 0\nmodel name\t: AMD Ryzen 7\n\nprocessor\t: 1\n")
        self.tree.write("/proc/meminfo", "MemTotal:       32000000 kB\nMemAvailable:   20000000 kB\n")
        self.assertEqual(hardware.cpu_memory(self.ctx()).severity, "pass")
        self.tree.write("/proc/meminfo", "MemTotal:       32000000 kB\nMemAvailable:   100000 kB\n")
        self.assertEqual(hardware.cpu_memory(self.ctx()).severity, "warn")

    def test_session_audio_sensors(self):
        self.assertEqual(hardware.session(self.ctx(env={"XDG_SESSION_TYPE": "wayland",
                                                        "XDG_CURRENT_DESKTOP": "KDE"})).severity, "pass")
        self.assertEqual(hardware.session(self.ctx(env={"XDG_SESSION_TYPE": "x11"})).severity, "info")
        self.tree.write("/proc/asound/cards", " 0 [Generic        ]: HDA-Intel - HD-Audio Generic\n")
        self.tree.write("/run/user/1000/pipewire-0", "")
        self.assertEqual(hardware.audio(self.ctx(env={"XDG_RUNTIME_DIR": "/run/user/1000"})).severity, "pass")
        self.tree.p("/run/user/1000/pipewire-0").unlink()
        self.assertEqual(hardware.audio(self.ctx(env={"XDG_RUNTIME_DIR": "/run/user/1000"})).severity, "warn")
        self.tree.write("/sys/class/hwmon/hwmon0/name", "k10temp")
        self.tree.write("/sys/class/hwmon/hwmon0/temp1_input", "61000")
        self.tree.write("/sys/class/hwmon/hwmon0/temp1_crit", "100000")
        self.assertEqual(hardware.sensors(self.ctx()).severity, "pass")
        self.tree.write("/sys/class/hwmon/hwmon0/temp1_input", "101000")
        self.assertEqual(hardware.sensors(self.ctx()).severity, "warn")


# ── Storage ─────────────────────────────────────────────────────────────────
BOOTC_MOUNTS = """\
22 1 0:21 / / ro,relatime shared:1 - overlay composefs ro,lowerdir+=/run/ostree/.private/cfsroot-lower
23 22 259:3 / /sysroot ro,relatime shared:2 - btrfs /dev/nvme0n1p3 ro,ssd,space_cache=v2
24 22 259:3 /root/ostree/deploy/default/deploy/abc.0/etc /etc rw,relatime shared:3 - btrfs /dev/nvme0n1p3 rw,ssd
25 22 259:3 /root/ostree/deploy/default/var /var rw,relatime shared:4 - btrfs /dev/nvme0n1p3 rw,ssd
"""


class StorageTests(Base):
    def setUp(self):
        super().setUp()
        self.tree.mkdir("/var/home/alice")
        self.tree.link("/home", "var/home")

    def test_bootc_layout_readonly_usr_is_normal(self):
        self.tree.write("/proc/self/mountinfo", BOOTC_MOUNTS)
        f = by_id(storage.layout(self.ctx()))
        self.assertEqual(f["storage.layout"].severity, "pass")
        self.assertIn("/usr read-only by design", f["storage.layout"].summary)
        self.assertEqual(f["storage.home"].severity, "pass")
        self.assertIn("/var", f["storage.home"].summary)

    def test_readonly_var_is_error(self):
        self.tree.write("/proc/self/mountinfo", BOOTC_MOUNTS.replace("/var rw,relatime", "/var ro,relatime"))
        f = by_id(storage.layout(self.ctx()))
        self.assertEqual(f["storage.layout"].severity, "error")
        self.assertEqual(f["storage.home"].severity, "error")

    def test_superblock_readonly_detected(self):
        self.tree.write("/proc/self/mountinfo", BOOTC_MOUNTS.replace("/dev/nvme0n1p3 rw,ssd\n", "/dev/nvme0n1p3 ro,ssd\n"))
        self.assertEqual(by_id(storage.layout(self.ctx()))["storage.layout"].severity, "error")

    def test_usr_overlay_is_info(self):
        self.tree.write("/proc/self/mountinfo", BOOTC_MOUNTS +
                        "30 22 0:40 / /usr rw,relatime shared:9 - overlay overlay rw,lowerdir=/usr\n")
        self.assertEqual(by_id(storage.layout(self.ctx()))["storage.layout"].severity, "info")

    def test_free_space_thresholds_and_units(self):
        def fs(free_gib, total_gib=500, fsid=1):
            return SimpleNamespace(f_blocks=total_gib * 1024 ** 3 // 4096, f_bavail=int(free_gib * 1024 ** 3) // 4096,
                                   f_frsize=4096, f_fsid=fsid)
        ctx = self.ctx()
        for free, sev in ((200, "pass"), (20, "warn"), (8, "warn"), (1.5, "error")):
            ctx.statvfs = lambda path, free=free: fs(free)
            f = storage.space(ctx)
            self.assertEqual(f.severity, sev, free)
            self.assertTrue(any("GiB" in e for e in f.evidence))
        ctx.statvfs = lambda path: (_ for _ in ()).throw(PermissionError(13, "Permission denied"))
        self.assertEqual(storage.space(ctx).severity, "unknown")


# ── Apps & Flatpaks ─────────────────────────────────────────────────────────
SYS_LIST = ("flatpak", "list", "--system", "--app", "--columns=application")
USER_LIST = ("flatpak", "list", "--user", "--app", "--columns=application")
SHOW = ("systemctl", "show", "fe-xicoos-flatpaks.service")
APPS = "com.vysp3r.ProtonPlus\nio.gitlab.librewolf-community\ncom.usebottles.bottles\nio.github.kolunmi.Bazaar\n"


class FlatpakTests(Base):
    def setUp(self):
        super().setUp()
        self.tree.write("/usr/share/fe-xicoos/default-flatpaks.txt", "# defaults\n" + APPS)

    def run_fp(self, system_apps, user_apps="", service="ActiveState=inactive\nResult=success", marker=False):
        if marker:
            self.tree.write("/var/lib/fe-xicoos/default-flatpaks-v4.done", "")
        return software.flatpaks(self.ctx({SYS_LIST: ok(system_apps), USER_LIST: ok(user_apps), SHOW: ok(service)}))

    def test_all_installed_including_user_scope(self):
        self.assertEqual(self.run_fp(APPS).severity, "pass")
        f = self.run_fp("com.vysp3r.ProtonPlus\nio.gitlab.librewolf-community\ncom.usebottles.bottles\n",
                        "io.github.kolunmi.Bazaar\n")
        self.assertEqual(f.severity, "pass")
        self.assertIn("1 installed per-user", f.summary)

    def test_first_boot_pending_is_not_a_fault(self):
        f = self.run_fp("")
        self.assertEqual(f.severity, "info")
        self.assertIn("not provisioned yet", f.summary)
        f = self.run_fp("", service="ActiveState=activating\nSubState=start\nResult=success")
        self.assertIn("being installed now", f.summary)

    def test_failed_provisioning_warns(self):
        f = self.run_fp("", service="ActiveState=failed\nSubState=failed\nResult=exit-code")
        self.assertEqual(f.severity, "warn")

    def test_missing_after_provisioning_is_user_choice(self):
        f = self.run_fp("com.vysp3r.ProtonPlus\n", marker=True)
        self.assertEqual(f.severity, "info")
        self.assertIn("removed on purpose", f.guidance[0])

    def test_flatpak_missing_or_manifest_missing(self):
        self.assertEqual(software.flatpaks(self.ctx()).severity, "unknown")
        self.tree.p("/usr/share/fe-xicoos/default-flatpaks.txt").unlink()
        self.assertEqual(software.flatpaks(self.ctx({SYS_LIST: ok("")})).severity, "unknown")


class CoreAppTests(Base):
    PART = ("rpm", "-q", "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n", "konsole-part")

    def test_core_apps(self):
        for path in ("/usr/bin/foot", "/usr/bin/dolphin", "/usr/bin/steam", "/usr/bin/doctor-nox"):
            self.tree.write(path, "", 0o755)
        self.assertEqual(software.core_apps(self.ctx({self.PART: ok("konsole-part 25.08.2-1.fc44.x86_64\n")})).severity,
                         "pass")
        self.assertEqual(software.core_apps(self.ctx({self.PART: exit_(1, "package konsole-part is not installed\n")})).severity,
                         "warn")
        self.tree.write("/usr/bin/konsole", "", 0o755)
        self.assertEqual(software.core_apps(self.ctx({self.PART: ok("konsole-part x\n")})).severity, "info")
        self.tree.p("/usr/bin/foot").unlink()
        f = software.core_apps(self.ctx({self.PART: ok("konsole-part x\n")}))
        self.assertEqual(f.severity, "warn")
        self.assertIn("Foot", f.summary)


# ── Network ─────────────────────────────────────────────────────────────────
class NetworkTests(Base):
    def iface(self, name, oper, carrier, virtual=False, v4=()):
        kind = "virtual/net" if virtual else "pci0000:00/0000:05:00.0/net"
        target = self.tree.mkdir(f"/sys/devices/{kind}/{name}")
        (target / "operstate").write_text(oper + "\n")
        (target / "carrier").write_text(("1" if carrier else "0") + "\n")
        self.tree.link(f"/sys/class/net/{name}", str(target))
        if v4:
            self.tree.write(f"/fixture/ipv4/{name}", "\n".join(v4))

    def test_offline_is_info_not_error(self):
        self.iface("enp5s0", "down", False)
        self.iface("docker0", "up", True, virtual=True, v4=("172.17.0.1",))
        f = network.link(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertIn("Offline", f.summary)
        self.assertEqual(network.route(self.ctx()).severity, "unknown")
        self.tree.write("/proc/net/route", "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n")
        self.assertEqual(network.route(self.ctx()).severity, "info")

    def test_connected_route_and_addresses(self):
        self.iface("enp5s0", "up", True, v4=("192.168.1.44",))
        self.tree.write("/proc/net/if_inet6", "2a028108000100020000000000000005 02 40 00 80 enp5s0\n"
                                              "fe800000000000000000000000000001 02 40 20 80 enp5s0\n")
        f = network.link(self.ctx())
        self.assertEqual(f.severity, "pass")
        self.assertTrue(any("192.168.1.44" in e and "2a02:8108:1:2::5" in e for e in f.evidence))
        self.tree.write("/proc/net/route", "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\n"
                                           "enp5s0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\n")
        r = network.route(self.ctx())
        self.assertEqual(r.severity, "pass")
        self.assertIn("192.168.1.1", r.evidence[0])

    def test_dns(self):
        self.assertEqual(network.dns(self.ctx()).severity, "warn")
        self.tree.write("/etc/resolv.conf", "nameserver 127.0.0.53\nsearch corp.example\n")
        show = ("systemctl", "show", "systemd-resolved.service")
        self.assertEqual(network.dns(self.ctx({show: ok("ActiveState=active\n")})).severity, "pass")
        f = network.dns(self.ctx({show: ok("ActiveState=inactive\n")}))
        self.assertEqual(f.severity, "warn")
        self.assertFalse(any("corp.example" in e for e in f.evidence), "search domains are never shown")

    def test_network_manager_states(self):
        nm = ("nmcli", "-t", "-f", "STATE,CONNECTIVITY", "general")
        self.assertEqual(network.manager(self.ctx({nm: ok("connected:full\n")})).severity, "pass")
        self.assertEqual(network.manager(self.ctx({nm: ok("disconnected:none\n")})).severity, "info")
        self.assertEqual(network.manager(self.ctx()).severity, "info")


# ── Robustness: every probe on an empty system with hostile command results ───
class RobustnessTests(Base):
    def test_every_probe_survives_missing_tools_timeouts_and_garbage(self):
        from doctor_nox import probes
        from doctor_nox.model import Finding
        for default in (MISSING, TIMEOUT, ok("\x00\xff garbage {not json"), exit_(1, "", "denied")):
            ctx = self.ctx(default=default)
            for p in probes.load_all():
                with self.subTest(probe=p.id, default=default[0]):
                    result = p.func(ctx)
                    for f in (result if isinstance(result, list) else [result]):
                        self.assertIsInstance(f, Finding)
                        if default[0] in ("missing", "timeout"):
                            self.assertNotEqual(f.severity, "error", f"{f.id} claims ERROR without evidence")


if __name__ == "__main__":
    unittest.main()
