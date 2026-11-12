"""Doctor NoX probe behaviour on synthetic filesystems and command output, per Linux family.

Every command a probe asks for passes through FakeRunner, which enforces the
real allowlist. Nothing here runs bootc, rpm-ostree, systemctl, journalctl,
dpkg, pacman, rpm, flatpak or snap.
"""
import datetime
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_testlib import (KERNEL, MISSING, NOW, PRESETS, TIMEOUT, FakeRunner, Tree, bootc_entry,  # noqa: E402
                            bootc_host, exit_, install_distro, ok, ostree_entry, ostree_status)

from doctor_nox import distro, engine, model  # noqa: E402
from doctor_nox.probes import (Probe, hardware, load_all, logs, network, security, software, storage,  # noqa: E402
                               system, updates)

BOOTC = ("bootc", "status", "--json", "--format-version=1")
RPM_OSTREE = ("rpm-ostree", "status", "--json")
RPM_QF = "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n"
DAY = 86400


def by_id(findings):
    if not isinstance(findings, list):
        findings = [findings]
    return {f.id: f for f in findings}


def age(path, days):
    """Set a fixture file's modification time `days` before NOW."""
    stamp = NOW.timestamp() - days * DAY
    os.utime(path, (stamp, stamp))


class Base(unittest.TestCase):
    kernel = KERNEL

    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)

    def distro(self, name):
        self.kernel = install_distro(self.tree, name)
        return self.kernel

    def ctx(self, answers=None, default=MISSING, **kw):
        self.runner = FakeRunner(answers, default)
        kw.setdefault("kernel", self.kernel)
        return self.tree.context(self.runner, **kw)

    def modules(self, *releases, tree="/usr/lib/modules"):
        for release in releases:
            self.tree.write(f"{tree}/{release}/modules.dep", "kernel/fs/x.ko.xz:\n")


# ── which Linux this is ──────────────────────────────────────────────────────
class PlatformTests(Base):
    EXPECT = {  # family, image
        "fedora": ("fedora", ""), "silverblue": ("fedora", "ostree"), "bazzite": ("fedora", "ostree"),
        "fe-xicoos": ("fedora", "ostree"), "almalinux": ("fedora", ""), "ubuntu": ("debian", ""),
        "debian": ("debian", ""), "mint": ("debian", ""), "pop": ("debian", ""), "arch": ("arch", ""),
        "cachyos": ("arch", ""), "steamos": ("arch", "steamos"), "tumbleweed": ("suse", ""),
        "leap": ("suse", ""), "aeon": ("suse", "transactional"), "nixos": ("other", ""), "alpine": ("other", ""),
    }

    def test_every_preset_is_recognized(self):
        self.assertEqual(set(self.EXPECT), set(PRESETS))
        for name, (family, image) in self.EXPECT.items():
            with self.subTest(distro=name):
                tree = Tree()
                self.addCleanup(tree.cleanup)
                install_distro(tree, name)
                p = tree.context().platform
                self.assertEqual((p.family, p.image), (family, image))
                self.assertEqual(p.package_managers, tuple(t for t, _ in distro._TOOLS if t in PRESETS[name][3]))
                self.assertEqual(p.systemd, PRESETS[name][1])
                self.assertTrue(p.name)
                self.assertIn(distro.FAMILY_TITLE[family], p.describe())

    def test_family_words_and_order(self):
        self.assertEqual(distro.family_of("pop", ("ubuntu", "debian")), "debian")
        self.assertEqual(distro.family_of("rocky", ("rhel", "centos", "fedora")), "fedora")
        self.assertEqual(distro.family_of("opensuse-slowroll", ()), "suse")
        self.assertEqual(distro.family_of("sles", ()), "suse")
        self.assertEqual(distro.family_of("gentoo", ()), "other")
        self.assertEqual(distro.family_of("", ()), "other")

    def test_security_module_detection(self):
        self.tree.write("/sys/kernel/security/lsm", "lockdown,capability,landlock,yama,apparmor\n")
        self.tree.write("/sys/module/apparmor/parameters/enabled", "Y\n")
        self.assertEqual(self.ctx().platform.security, "apparmor")
        self.tree.write("/sys/kernel/security/lsm", "lockdown,capability,yama,selinux,bpf\n")
        self.assertEqual(self.ctx().platform.security, "selinux")
        self.tree.write("/sys/kernel/security/lsm", "lockdown,capability,yama\n")
        self.tree.write("/sys/module/apparmor/parameters/enabled", "N\n")
        self.assertEqual(self.ctx().platform.security, "")

    def test_platform_is_detected_once_per_scan(self):
        self.distro("arch")
        ctx = self.ctx()
        self.assertIs(ctx.platform, ctx.platform)


class ApplicabilityTests(Base):
    def test_checks_for_other_systems_are_listed_not_run(self):
        self.distro("arch")
        rep = engine.scan(self.ctx(), deadline=30)
        self.assertEqual(model.validate_report(rep), [])
        ran = {f["id"].split(".dup")[0] for f in rep["findings"]}
        skipped = {item["id"]: item for item in rep["not_applicable"]}
        self.assertIn("system.image", skipped)
        self.assertNotIn("system.image", ran)
        self.assertNotIn("system.image.booted", ran)
        self.assertIn("OSTree", skipped["system.image"]["applies_to"])
        self.assertIn("updates.packages", ran)
        self.assertEqual(rep["host"]["family"], "arch")
        self.assertEqual(rep["host"]["package_managers"], ["pacman"])

    def test_image_based_system_skips_package_checks(self):
        self.distro("silverblue")
        todo, skipped = engine.applicable(self.ctx(), load_all())
        ids = {p.id for p in todo}
        self.assertIn("system.image", ids)
        self.assertIn("updates.automatic", ids)
        for gone in ("updates.packages", "updates.last_update", "updates.reboot"):
            self.assertIn(gone, {s["id"] for s in skipped})

    def test_a_broken_rule_never_hides_a_check(self):
        broken = distro.Scope("broken", lambda p: 1 / 0)
        probe = Probe("system.x", "system", "X", lambda c: None, broken)
        todo, skipped = engine.applicable(self.ctx(), [probe])
        self.assertEqual((todo, skipped), ([probe], []))

    def test_every_check_has_a_scope_with_words(self):
        for p in load_all():
            self.assertIsInstance(p.applies, distro.Scope, p.id)
            self.assertTrue(p.applies.text, p.id)


# ── System ───────────────────────────────────────────────────────────────────
class IdentityTests(Base):
    def test_identity_is_information_with_family_and_tools(self):
        self.distro("ubuntu")
        ctx = self.ctx()
        f = system.identity(ctx)
        self.assertEqual(f.severity, "info")
        self.assertIn("Ubuntu 24.04.3 LTS", f.summary)
        self.assertIn("6.8.0-49-generic", f.summary)
        self.assertTrue(any("Debian / Ubuntu family" in e for e in f.evidence))
        self.assertTrue(any("apt, dpkg, snap" in e for e in f.evidence))
        host = system.host_summary(ctx)
        self.assertEqual((host["family"], host["id"], host["version"]), ("debian", "ubuntu", "24.04"))
        self.assertEqual(host["package_managers"], ["apt", "dpkg", "snap"])

    def test_other_linux_and_missing_os_release(self):
        self.distro("nixos")
        f = system.identity(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertTrue(f.guidance, "other distributions are told what extra checks exist")
        self.tree.p("/usr/lib/os-release").unlink()
        self.assertEqual(system.identity(self.ctx()).severity, "unknown")


class OsReleaseDriftTests(Base):
    def test_symlink_matching_copy_and_stale_copy(self):
        self.distro("silverblue")
        self.tree.link("/etc/os-release", "../usr/lib/os-release")
        self.assertEqual(system.os_release_drift(self.ctx()).severity, "pass")
        self.tree.p("/etc/os-release").unlink()
        self.tree.write("/etc/os-release", self.tree.p("/usr/lib/os-release").read_text())
        self.assertEqual(system.os_release_drift(self.ctx()).severity, "pass")
        # an OSTree /etc merge kept a copy from an older release
        self.tree.write("/etc/os-release", 'NAME="Fedora Linux"\nID=fedora\nVERSION_ID=42\nVARIANT_ID=silverblue\n')
        f = system.os_release_drift(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("VERSION_ID" in e and "'42'" in e for e in f.evidence))

    def test_only_an_etc_copy(self):
        self.tree.write("/etc/os-release", 'NAME="Alpine Linux"\nID=alpine\n')
        self.assertEqual(system.os_release_drift(self.ctx()).severity, "pass")


class BootcTests(Base):
    def setUp(self):
        super().setUp()
        self.distro("bazzite")

    def scan(self, host):
        return by_id(system.image(self.ctx({BOOTC: ok(json.dumps(host))})))

    def test_booted_with_rollback_and_nothing_staged(self):
        f = self.scan(bootc_host())
        self.assertEqual(f["system.image.booted"].severity, "pass")
        self.assertIn("44.20261030.0", f["system.image.booted"].summary)
        self.assertEqual(f["system.image.rollback"].severity, "pass")
        self.assertEqual(f["system.image.pending"].severity, "pass")
        self.assertEqual(f["system.image.source"].severity, "pass")
        self.assertTrue(any("built 3 days ago" in e for e in f["system.image.booted"].evidence))
        self.assertNotIn(RPM_OSTREE, self.runner.calls, "bootc answered; rpm-ostree is not asked")

    def test_no_rollback_is_info_not_failure(self):
        self.assertEqual(self.scan(bootc_host(rollback=False))["system.image.rollback"].severity, "info")

    def test_staged_update_applies_at_reboot(self):
        f = self.scan(bootc_host(staged=bootc_entry("44.20261031.0", "d", download_only=False)))
        self.assertEqual(f["system.image.pending"].severity, "info")
        self.assertIn("applies at the next reboot", f["system.image.pending"].summary)

    def test_download_only_is_never_called_reboot_applicable(self):
        pending = self.scan(bootc_host(staged=bootc_entry("44.20261031.0", "d", download_only=True)))["system.image.pending"]
        self.assertIn("will NOT be applied at reboot", pending.summary)
        self.assertNotIn("applies at the next reboot", pending.summary)

    def test_rollback_queued(self):
        self.assertIn("Rollback queued", self.scan(bootc_host(queued=True))["system.image.pending"].summary)

    def test_pinned_recovery_counted_across_all_slots(self):
        f = self.scan(bootc_host(others=[bootc_entry("44.20260901.0", "e", pinned=True)]))
        self.assertEqual(f["system.image.pinned"].severity, "pass")
        self.assertIn("1 pinned", f["system.image.pinned"].summary)
        self.assertEqual(self.scan(bootc_host())["system.image.pinned"].severity, "info")

    def test_source_mismatch_and_pending_switch(self):
        other = "ghcr.io/ublue-os/bazzite:testing"
        self.assertEqual(self.scan(bootc_host(spec_ref=other))["system.image.source"].severity, "warn")
        f = self.scan(bootc_host(spec_ref=other, staged=bootc_entry("t", "d", ref=other)))
        self.assertEqual(f["system.image.source"].severity, "info")

    def test_incompatible_deployment(self):
        f = self.scan(bootc_host(booted=bootc_entry("44.1", "b", incompatible=True)))
        self.assertEqual(f["system.image.booted"].severity, "warn")

    def test_timestamps_parse_on_every_supported_python(self):
        """bootc prints up to nine fractional digits and "Z"; Python 3.10 takes neither as is."""
        utc = datetime.timezone.utc
        expect = {
            "2026-10-28T04:00:00Z": datetime.datetime(2026, 10, 28, 4, 0, 0, tzinfo=utc),
            "2026-10-28T04:00:00.123456789Z": datetime.datetime(2026, 10, 28, 4, 0, 0, 123456, tzinfo=utc),
            "2026-10-28T04:00:00.5z": datetime.datetime(2026, 10, 28, 4, 0, 0, 500000, tzinfo=utc),
            "2026-10-28T06:00:00.1234+02:00": datetime.datetime(2026, 10, 28, 4, 0, 0, 123400, tzinfo=utc),
        }
        for text, when in expect.items():
            self.assertEqual(system._iso(text), when, text)
        for bad in ("2026-10-28T04:00:00", "not a date", "", None, 1793404800):
            self.assertIsNone(system._iso(bad), bad)

    def test_malformed_bootc_falls_back_to_rpm_ostree_then_unknown(self):
        for payload in ("not json", json.dumps({"apiVersion": "org.containers.bootc/v2"}),
                        json.dumps({"apiVersion": "org.containers.bootc/v1", "status": {"booted": None}}),
                        json.dumps([1, 2])):
            f = by_id(system.image(self.ctx({BOOTC: ok(payload)})))
            self.assertEqual(f["system.image.booted"].severity, "unknown", payload)
            self.assertIn(RPM_OSTREE, self.runner.calls)
            self.assertTrue(any("not understood" in e for e in f["system.image.booted"].evidence))


class RpmOstreeTests(Base):
    """bootc needs root; rpm-ostree answers any user, so most desktops are read through it."""

    def setUp(self):
        super().setUp()
        self.distro("silverblue")

    def scan(self, doc):
        return by_id(system.image(self.ctx({BOOTC: exit_(1, stderr="error: This command requires root privileges"),
                                             RPM_OSTREE: ok(json.dumps(doc))})))

    def test_booted_rollback_and_layered_packages(self):
        f = self.scan(ostree_status(ostree_entry("44.20261030.0", "b", booted=True, layered=("htop", "steam")),
                                    ostree_entry("44.20261023.0", "c")))
        self.assertEqual(f["system.image.booted"].severity, "pass")
        self.assertIn("44.20261030.0", f["system.image.booted"].summary)
        self.assertTrue(any("rpm-ostree status" in e for e in f["system.image.booted"].evidence))
        self.assertEqual(f["system.image.rollback"].severity, "pass")
        self.assertEqual(f["system.image.pending"].severity, "pass")
        self.assertEqual(f["system.image.local_changes"].severity, "info")
        self.assertIn("2 layered package change(s)", f["system.image.local_changes"].summary)

    def test_staged_update_and_unlocked_usr(self):
        f = self.scan(ostree_status(ostree_entry("44.20261031.0", "d", staged=True),
                                    ostree_entry("44.20261030.0", "b", booted=True, unlocked="development"),
                                    ostree_entry("44.20261023.0", "c")))
        self.assertIn("applies at the next reboot", f["system.image.pending"].summary)
        self.assertIn("unlocked", f["system.image.local_changes"].summary)

    def test_rollback_queued_when_the_previous_deployment_boots_next(self):
        f = self.scan(ostree_status(ostree_entry("44.20261023.0", "c"),
                                    ostree_entry("44.20261030.0", "b", booted=True)))
        self.assertIn("Rollback queued", f["system.image.pending"].summary)

    def test_pinned_and_inconsistent_documents(self):
        f = self.scan(ostree_status(ostree_entry("44.2", "b", booted=True),
                                    ostree_entry("43.9", "e", pinned=True)))
        self.assertEqual(f["system.image.pinned"].severity, "pass")
        for doc in ({"deployments": []}, {"deployments": [ostree_entry("1", "a"), ostree_entry("2", "b")]},
                    {"nope": 1}):
            self.assertEqual(self.scan(doc)["system.image.booted"].severity, "unknown", doc)

    def test_neither_tool_answers(self):
        self.tree.write("/run/ostree/staged-deployment", "{}")
        f = by_id(system.image(self.ctx({BOOTC: exit_(1, stderr="requires root"), RPM_OSTREE: TIMEOUT})))
        booted = f["system.image.booted"]
        self.assertEqual(booted.severity, "unknown")
        self.assertIn("a deployment is staged", booted.summary)
        self.assertTrue(any("rpm-ostree status" in g for g in booted.guidance))


# ── Updates ──────────────────────────────────────────────────────────────────
class RebootTests(Base):
    def test_debian_flag_lists_packages_and_ages(self):
        kernel = self.distro("ubuntu")
        self.modules(kernel)
        flag = self.tree.write("/run/reboot-required", "*** System restart required ***\n")
        self.tree.write("/run/reboot-required.pkgs", "linux-image-6.8.0-50-generic\nlibc6\nlibc6\n")
        age(flag, 1)
        f = updates.reboot(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertIn("apt/dpkg asked for a restart", f.summary)
        self.assertTrue(any("libc6, linux-image-6.8.0-50-generic" in e for e in f.evidence))
        age(flag, 9)
        self.assertEqual(updates.reboot(self.ctx()).severity, "warn", "a restart put off for over a week")

    def test_kernel_flavours(self):
        for release, family, flavour in (("6.8.0-49-generic", "debian", "generic"),
                                         ("6.12.48+deb13-rt-amd64", "debian", "rt-amd64"),
                                         ("6.12.48+deb13-amd64", "debian", "amd64"),
                                         ("6.17.5-1-default", "suse", "default"),
                                         ("6.17.4-300.fc44.x86_64+debug", "fedora", "debug"),
                                         ("6.12.0-55.9.1.el10_0.x86_64", "fedora", ""),
                                         ("6.12.55", "other", "")):
            self.assertEqual(updates.kernel_flavour(release, family), flavour, release)

    def test_newer_kernel_of_the_same_flavour(self):
        kernel = self.distro("ubuntu")
        self.modules(kernel, "6.8.0-50-lowlatency")
        self.assertEqual(updates.reboot(self.ctx()).severity, "pass", "another flavour is not an update")
        self.modules("6.8.0-51-generic")
        f = updates.reboot(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertIn("6.8.0-51-generic", f.summary)

    def test_fedora_kernels_sort_naturally_and_debug_is_separate(self):
        self.distro("fedora")
        self.modules(KERNEL, "6.17.10-300.fc44.x86_64+debug", "6.16.9-200.fc44.x86_64")
        self.assertEqual(updates.reboot(self.ctx()).severity, "pass")
        self.modules("6.17.10-300.fc44.x86_64")
        self.assertIn("6.17.10-300.fc44.x86_64)", updates.reboot(self.ctx()).summary)

    def test_arch_removed_modules_warn_and_other_kernels_do_not_count(self):
        kernel = self.distro("arch")
        self.modules(kernel, "6.12.55-1-lts")
        self.assertEqual(updates.reboot(self.ctx()).severity, "pass", "linux-lts beside linux is not an update")
        self.tree.p(f"/usr/lib/modules/{kernel}/modules.dep").unlink()
        self.modules("6.17.6-arch1-1")
        f = updates.reboot(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertIn("removed", f.summary)
        self.assertTrue(any("pacman -Syu" in g for g in f.guidance))

    def test_opensuse_flag_and_unmerged_lib_modules(self):
        kernel = self.distro("leap")
        self.modules(kernel, tree="/lib/modules")
        self.assertEqual(updates.reboot(self.ctx()).severity, "pass")
        self.tree.write("/run/reboot-needed", "")
        f = updates.reboot(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertIn("zypper", f.summary)

    def test_container_without_kernels_is_unknown(self):
        self.distro("debian")
        f = updates.reboot(self.ctx())
        self.assertEqual(f.severity, "unknown")
        self.assertTrue(f.guidance)


class PackageDatabaseTests(Base):
    RPM = ("rpm", "-q", RPM_QF, "rpm")

    def test_rpm(self):
        self.distro("fedora")
        self.assertEqual(updates.packages(self.ctx({self.RPM: ok("rpm 4.20.1-1.fc44.x86_64\n")})).severity, "pass")
        self.assertEqual(updates.packages(self.ctx({self.RPM: exit_(1, stderr="error: rpmdb open failed")})).severity,
                         "error")
        self.assertEqual(updates.packages(self.ctx()).severity, "unknown")

    def test_dpkg(self):
        self.distro("debian")
        audit = ("dpkg", "--audit")
        self.assertEqual(updates.packages(self.ctx({audit: ok("")})).severity, "pass")
        f = updates.packages(self.ctx({audit: ok("The following packages are only half configured:\n foo\n")}))
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("dpkg --configure -a" in g for g in f.guidance))
        self.tree.write("/var/lib/dpkg/updates/0003", "")
        self.assertEqual(updates.packages(self.ctx({audit: ok("")})).severity, "warn", "an interrupted dpkg run")

    def test_pacman_lock_orphans_and_busy(self):
        self.distro("arch")
        q = ("pacman", "-Q", "pacman")
        orphans = ("pacman", "-Qdtq")
        healthy = {q: ok("pacman 7.0.0.r6.gc685ae6-6\n"), orphans: exit_(1)}
        self.assertEqual(updates.packages(self.ctx(healthy)).severity, "pass")
        f = updates.packages(self.ctx({**healthy, orphans: ok("libfoo\nbar-git\n")}))
        self.assertEqual(f.severity, "info")
        self.assertIn("2 orphaned", f.summary)
        self.tree.write("/var/lib/pacman/db.lck", "")
        f = updates.packages(self.ctx(healthy))
        self.assertEqual(f.severity, "warn")
        self.assertIn("lock", f.summary)
        self.tree.write("/proc/4242/comm", "pamac-daemon\n")
        f = updates.packages(self.ctx(healthy))
        self.assertEqual(f.severity, "info", "a frontend holds the lock right now")
        self.assertIn("pamac-daemon", f.summary)
        self.assertEqual(updates.packages(self.ctx({q: exit_(1, stderr="error: failed to initialize alpm")})).severity,
                         "error")

    def test_zypper_live_lock_only(self):
        self.distro("tumbleweed")
        healthy = {self.RPM: ok("rpm 4.20.1-2.1.x86_64\n")}
        self.tree.write("/run/zypp.pid", "777\n")
        self.assertEqual(updates.packages(self.ctx(healthy)).severity, "pass", "libzypp takes over a dead lock")
        self.tree.mkdir("/proc/777")
        self.assertEqual(updates.packages(self.ctx(healthy)).severity, "info")


class ConfigFileTests(Base):
    def test_leftovers_per_family(self):
        for name, path, tool in (("arch", "/etc/pacman.d/mirrorlist.pacnew", "pacdiff"),
                                 ("debian", "/etc/ssh/sshd_config.dpkg-dist", "diff"),
                                 ("fedora", "/etc/dnf/dnf.conf.rpmnew", "rpmconf"),
                                 ("tumbleweed", "/etc/zypp/zypp.conf.rpmnew", "diff")):
            with self.subTest(distro=name):
                tree = Tree()
                self.addCleanup(tree.cleanup)
                self.tree = tree
                self.distro(name)
                tree.write("/etc/hostname", "x\n")
                self.assertEqual(updates.config_files(self.ctx()).severity, "pass")
                tree.write(path, "")
                f = updates.config_files(self.ctx())
                self.assertEqual(f.severity, "info")
                self.assertIn(path, f.evidence)
                self.assertTrue(any(tool in g for g in f.guidance))

    def test_other_families_suffixes_are_ignored(self):
        self.distro("arch")
        self.tree.write("/etc/foo.rpmnew", "")
        self.assertEqual(updates.config_files(self.ctx()).severity, "pass")

    def test_unreadable_etc_is_unknown(self):
        self.distro("debian")
        self.assertEqual(updates.config_files(self.ctx()).severity, "unknown")


class LastUpdateTests(Base):
    def pacman_log(self, days_ago, line="[PACMAN] starting full system upgrade"):
        when = (NOW - datetime.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        old = (NOW - datetime.timedelta(days=400)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        self.tree.write("/var/log/pacman.log", f"[{old}] [PACMAN] starting full system upgrade\n"
                                               + "[2020-01-01T00:00:00+0000] [ALPM] noise\n" * 50
                                               + f"[{when}] {line}\n[{when}] [ALPM] upgraded foo (1-1 -> 1-2)\n")

    def test_arch_full_upgrade_from_pacman_log(self):
        self.distro("arch")
        self.pacman_log(3)
        f = updates.last_update(self.ctx())
        self.assertEqual(f.severity, "pass")
        self.assertIn("Last full system upgrade 3 days ago", f.summary)
        self.pacman_log(45)
        f = updates.last_update(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("pacman -Syu" in g for g in f.guidance))
        self.pacman_log(2, "[PACMAN] Running 'pacman -S foo'")
        f = updates.last_update(self.ctx())
        self.assertIn("Last full system upgrade 400 days ago", f.summary, "installing one package is not an upgrade")
        when = (NOW - datetime.timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        self.tree.write("/var/log/pacman.log", f"[{when}] [ALPM] installed foo (1-1)\n")
        f = updates.last_update(self.ctx())
        self.assertIn("No full system upgrade recently; packages last changed 2 days ago", f.summary)

    def test_arch_reads_only_the_end_of_a_huge_log(self):
        self.distro("arch")
        when = (NOW - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S+0000")
        with open(self.tree.write("/var/log/pacman.log"), "w") as log:
            log.write("[2020-01-01T00:00:00+0000] [ALPM] filler line for a long history\n" * 120000)
            log.write(f"[{when}] [PACMAN] starting full system upgrade\n")
        self.assertGreater(self.tree.p("/var/log/pacman.log").stat().st_size, 4 * 1024 * 1024)
        self.assertEqual(updates.last_update(self.ctx()).severity, "pass")

    def test_debian_dpkg_status_age(self):
        self.distro("debian")
        self.assertEqual(updates.last_update(self.ctx()).severity, "unknown")
        status = self.tree.write("/var/lib/dpkg/status", "Package: x\n")
        age(status, 2)
        self.assertEqual(updates.last_update(self.ctx()).severity, "pass")
        age(status, 31)
        self.assertEqual(updates.last_update(self.ctx()).severity, "warn")

    def test_rpm_install_times(self):
        self.distro("fedora")
        qa = ("rpm", "-qa", "--queryformat=%{INSTALLTIME}\\n")
        recent = int(NOW.timestamp()) - 5 * DAY
        self.assertIn("5 days ago", updates.last_update(self.ctx({qa: ok(f"1700000000\n{recent}\n(none)\n")})).summary)
        self.assertEqual(updates.last_update(self.ctx({qa: ok("1700000000\n")})).severity, "warn")
        self.assertEqual(updates.last_update(self.ctx({qa: TIMEOUT})).severity, "unknown")


def enabled(*units, others="disabled"):
    answers = {("systemctl", "is-enabled"): exit_(1, f"{others}\n")}
    for unit in units:
        answers[("systemctl", "is-enabled", unit)] = ok("enabled\n")
    return answers


class AutomaticUpdateTests(Base):
    def test_arch_needs_no_commands(self):
        self.distro("cachyos")
        f = updates.automatic(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertIn("only when you run them", f.summary)
        self.assertEqual(self.runner.calls, [])

    def test_unattended_upgrades(self):
        self.distro("ubuntu")
        self.tree.write("/usr/bin/unattended-upgrade", "", 0o755)
        self.tree.write("/etc/apt/apt.conf.d/20auto-upgrades",
                        'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n')
        f = updates.automatic(self.ctx(enabled("apt-daily-upgrade.timer")))
        self.assertIn("on", f.summary)
        self.assertTrue(any("security updates" in g for g in f.guidance))
        self.tree.write("/etc/apt/apt.conf.d/99local", '// APT::Periodic::Unattended-Upgrade "1";\n'
                                                       'APT::Periodic::Unattended-Upgrade "0";\n')
        self.assertIn("No automatic update service", updates.automatic(self.ctx(enabled("apt-daily-upgrade.timer"))).summary)

    def test_timer_without_unattended_upgrades_is_off(self):
        self.distro("debian")
        f = updates.automatic(self.ctx(enabled("apt-daily-upgrade.timer")))
        self.assertIn("No automatic update service", f.summary)
        self.assertTrue(any("software centers" in g for g in f.guidance))

    def test_dnf_automatic_reads_apply_updates(self):
        self.distro("fedora")
        self.tree.write("/etc/dnf/automatic.conf", "[commands]\napply_updates = yes\n")
        f = updates.automatic(self.ctx(enabled("dnf5-automatic.timer")))
        self.assertTrue(any("installs updates" in e for e in f.evidence))
        self.tree.write("/etc/dnf/automatic.conf", "[commands]\napply_updates = no\n")
        f = updates.automatic(self.ctx(enabled("dnf-automatic.timer")))
        self.assertTrue(any("downloads updates for you to install" in e for e in f.evidence))

    def test_rpm_ostree_policy_decides(self):
        self.distro("silverblue")
        self.tree.write("/etc/rpm-ostreed.conf", "[Daemon]\n#AutomaticUpdatePolicy=none\n")
        self.assertIn("No automatic", updates.automatic(self.ctx(enabled("rpm-ostreed-automatic.timer"))).summary)
        self.tree.write("/etc/rpm-ostreed.conf", "[Daemon]\nAutomaticUpdatePolicy=stage\n")
        f = updates.automatic(self.ctx(enabled("rpm-ostreed-automatic.timer")))
        self.assertIn("Automatic updates are on", f.summary)
        self.assertTrue(any("stages updates" in e for e in f.evidence))

    def test_image_updaters_and_unattended_restarts(self):
        self.distro("bazzite")
        f = updates.automatic(self.ctx(enabled("uupd.timer")))
        self.assertIn("Automatic updates are on", f.summary)
        self.assertNotIn("restart this computer", f.summary)
        f = updates.automatic(self.ctx(enabled("bootc-fetch-apply-updates.timer")))
        self.assertIn("restart this computer by themselves", f.summary)
        self.assertTrue(any("disable --now bootc-fetch-apply-updates.timer" in g for g in f.guidance))
        self.distro("fe-xicoos")
        self.assertIn("on", updates.automatic(self.ctx(enabled("fe-xicoos-auto-update.timer"))).summary)

    def test_transactional_update_and_unknown(self):
        self.distro("aeon")
        self.assertIn("on", updates.automatic(self.ctx(enabled("transactional-update.timer"))).summary)
        self.assertEqual(updates.automatic(self.ctx(default=TIMEOUT)).severity, "unknown")

    def test_steamos_is_not_applicable(self):
        self.distro("steamos")
        self.assertFalse(updates.AUTO_UPDATES(self.ctx().platform))


# ── Security & services ─────────────────────────────────────────────────────
FAILED_ARGS = ("list-units", "--state=failed", "--all", "--no-legend", "--plain", "--no-pager", "--full")


class MacTests(Base):
    def lsm(self, *modules):
        self.tree.write("/sys/kernel/security/lsm", ",".join(("lockdown", "capability", *modules)) + "\n")

    def test_selinux_modes(self):
        self.distro("fedora")
        self.lsm("selinux")
        self.tree.write("/sys/fs/selinux/enforce", "1")
        self.assertEqual(security.mac(self.ctx()).severity, "pass")
        self.tree.write("/sys/fs/selinux/enforce", "0")
        self.tree.write("/proc/cmdline", "BOOT_IMAGE=/vmlinuz root=UUID=x enforcing=0 quiet\n")
        f = security.mac(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("enforcing=0" in e for e in f.evidence))

    def test_selinux_disabled_on_fedora_warns(self):
        self.distro("almalinux")
        self.lsm("yama")
        self.tree.write("/usr/sbin/getenforce", "", 0o755)
        f = security.mac(self.ctx({("getenforce",): ok("Disabled\n")}))
        self.assertEqual(f.severity, "warn")
        self.assertIn("SELinux is disabled", f.summary)

    def test_apparmor(self):
        self.distro("ubuntu")
        self.lsm("landlock", "yama", "apparmor")
        self.tree.write("/sys/module/apparmor/parameters/enabled", "Y\n")
        self.assertEqual(security.mac(self.ctx()).severity, "pass")
        self.lsm("landlock", "yama")
        self.tree.write("/sys/module/apparmor/parameters/enabled", "N\n")
        self.assertEqual(security.mac(self.ctx()).severity, "warn", "Debian and Ubuntu ship AppArmor on")

    def test_arch_without_mac_is_its_default(self):
        self.distro("arch")
        self.lsm("landlock", "yama", "bpf")
        f = security.mac(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertTrue(f.guidance)

    def test_nothing_readable_is_unknown(self):
        self.distro("tumbleweed")
        self.assertEqual(security.mac(self.ctx()).severity, "unknown")


class FirewallTests(Base):
    SHOW = ("systemctl", "show", "firewalld.service")

    def test_firewalld_ufw_and_none(self):
        self.distro("fedora")
        self.assertEqual(security.firewall(self.ctx({self.SHOW: ok("LoadState=loaded\nActiveState=active\n")})).severity,
                         "pass")
        off = {self.SHOW: ok("LoadState=not-found\nActiveState=inactive\n")}
        self.tree.write("/etc/ufw/ufw.conf", "# comment\nENABLED=no\nLOGLEVEL=low\n")
        f = security.firewall(self.ctx(off))
        self.assertEqual(f.severity, "info")
        self.assertTrue(any("installed, disabled" in e for e in f.evidence))
        self.tree.write("/etc/ufw/ufw.conf", "ENABLED=yes\n")
        self.assertEqual(security.firewall(self.ctx(off)).severity, "pass")
        self.tree.p("/etc/ufw/ufw.conf").unlink()
        self.assertEqual(security.firewall(self.ctx({self.SHOW: TIMEOUT})).severity, "unknown")


class FailedUnitTests(Base):
    def test_failed_units(self):
        answers = {("systemctl", *FAILED_ARGS): ok(""), ("systemctl", "--user", *FAILED_ARGS): ok(""),
                   ("systemctl", "is-system-running"): ok("running\n")}
        self.assertEqual(security.failed_units(self.ctx(answers)).severity, "pass")
        answers[("systemctl", "--user", *FAILED_ARGS)] = ok("app-x.service loaded failed failed X\n")
        f = security.failed_units(self.ctx(answers))
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("--user status app-x.service" in g and "--user-unit app-x.service" in g
                            for g in f.guidance))
        answers[("systemctl", *FAILED_ARGS)] = ok("cups.service loaded failed failed CUPS Scheduler\n")
        f = security.failed_units(self.ctx(answers))
        self.assertIn("2 failed unit(s)", f.summary)
        self.assertTrue(any("`systemctl status cups.service`" in g for g in f.guidance))
        self.assertEqual(security.failed_units(self.ctx({})).severity, "unknown")


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

    def test_partial_access_and_unreadable(self):
        hint = "Hint: You are currently not seeing messages from other users and the system."
        self.assertEqual(logs.boot(self.ctx({BOOT_ARGS: ok("", hint)})).severity, "unknown")
        f = logs.boot(self.ctx({BOOT_ARGS: TIMEOUT}))
        self.assertEqual(f.severity, "unknown")
        self.assertTrue(any("adm" in g for g in f.guidance))


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

    def test_nvidia_open_from_banner_or_modinfo(self):
        self.gpu("0x10de", "nvidia")
        self.tree.write("/proc/driver/nvidia/version",
                        "NVRM version: NVIDIA UNIX Open Kernel Module for x86_64  580.95.05\n")
        f = hardware.graphics(self.ctx())
        self.assertEqual(f.severity, "pass")
        self.assertIn("NVIDIA Open", f.summary)
        self.tree.p("/proc/driver/nvidia/version").unlink()
        f = hardware.graphics(self.ctx({("modinfo", "-k", KERNEL, "-F", "license", "nvidia"): ok("Dual MIT/GPL\n")}))
        self.assertIn("NVIDIA Open", f.summary)

    def test_proprietary_nvidia_is_fine(self):
        self.gpu("0x10de", "nvidia")
        self.tree.write("/proc/driver/nvidia/version", "NVRM version: NVIDIA UNIX x86_64 Kernel Module  580.95.05\n")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "pass")

    def test_nouveau_is_a_valid_choice_unless_the_nvidia_driver_is_installed(self):
        self.gpu("0x10de", "nouveau")
        self.tree.write("/usr/share/vulkan/icd.d/nouveau_icd.x86_64.json", "{}")
        self.tree.write(f"/usr/lib/modules/{KERNEL}/modules.dep", "kernel/drivers/gpu/drm/nouveau/nouveau.ko.xz:\n")
        f = hardware.graphics(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertIn("NVK", f.summary)
        self.tree.write(f"/usr/lib/modules/{KERNEL}/modules.dep",
                        "kernel/drivers/gpu/drm/nouveau/nouveau.ko.xz:\nextra/nvidia/nvidia.ko.xz:\n")
        f = hardware.graphics(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("restart first" in g for g in f.guidance))

    def test_amd_intel_and_virtual_do_not_false_positive(self):
        self.gpu("0x1002", "amdgpu")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "pass")
        self.gpu("0x8086", "i915", card="card2", slot="0000:00:02.0")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "pass")
        self.gpu("0x1af4", "virtio-pci", card="card3", slot="0000:00:03.0")
        self.assertEqual(hardware.graphics(self.ctx()).severity, "info")

    def test_unbound_gpu_warns(self):
        self.gpu("0x1002", "")
        f = hardware.graphics(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertTrue(f.guidance)

    def test_nvidia_runtime(self):
        self.distro("arch")
        self.kernel = KERNEL
        self.assertIn("not applicable", hardware.nvidia(self.ctx()).summary)
        self.gpu("0x10de", "nvidia")
        self.tree.write("/sys/module/nvidia/version", "580.95.05\n")
        smi = ("nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader")
        self.assertEqual(hardware.nvidia(self.ctx({smi: ok("NVIDIA GeForce RTX 4080, 580.95.05\n")})).severity, "pass")
        f = hardware.nvidia(self.ctx({smi: ok("NVIDIA GeForce RTX 4080, 575.64\n")}))
        self.assertEqual(f.severity, "warn")
        self.assertIn("restart", f.guidance[0])
        self.assertEqual(hardware.nvidia(self.ctx({smi: exit_(9, stderr="NVIDIA-SMI has failed")})).severity, "warn")
        f = hardware.nvidia(self.ctx())
        self.assertIn("nvidia-utils", f.guidance[0], "the package named is the distribution's own")


EXTRA_EXACT = ("rpm", "-q", RPM_QF, f"kernel-modules-extra-{KERNEL}")
EXTRA_ANY = ("rpm", "-q", RPM_QF, "kernel-modules-extra")


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

    def mods(self, xpad):
        self.tree.write(f"/usr/lib/modules/{self.kernel}/modules.builtin", "kernel/drivers/hid/hid-generic.ko\n")
        dep = "kernel/drivers/input/joystick/xpad.ko.xz: kernel/drivers/input/ff-memless.ko.xz\n" if xpad else ""
        self.tree.write(f"/usr/lib/modules/{self.kernel}/modules.dep", dep + "kernel/fs/x.ko.xz:\n")

    def test_xpad_loaded_and_available(self):
        self.distro("fedora")
        self.tree.mkdir("/sys/module/xpad")
        f = hardware.controllers(self.ctx({EXTRA_EXACT: ok("kernel-modules-extra 6.17.4-300.fc44.x86_64\n")}))
        self.assertEqual(f.severity, "pass")
        self.assertIn("xpad loaded", f.summary)
        self.tree.p("/sys/module/xpad").rmdir()
        self.mods(xpad=True)
        self.assertIn("available", hardware.controllers(self.ctx({EXTRA_EXACT: ok("x\n")})).summary)

    def test_fedora_names_kernel_modules_extra(self):
        self.distro("fedora")
        self.mods(xpad=False)
        f = hardware.controllers(self.ctx({EXTRA_EXACT: exit_(1), EXTRA_ANY: exit_(1)}))
        self.assertEqual(f.severity, "info")
        self.assertIn("kernel-modules-extra not installed", f.summary)
        self.assertTrue(any("dnf install kernel-modules-extra" in g for g in f.guidance))

    def test_xbox_protocol_device_without_xpad_warns_with_kernel_specific_reason(self):
        self.distro("fedora")
        self.mods(xpad=False)
        self.usb("1-4", "2e24", "0652", "Hyperkin Duke", ("ff", "5d", "01"))
        f = hardware.controllers(self.ctx({EXTRA_EXACT: exit_(1), EXTRA_ANY: ok("kernel-modules-extra 6.16.9\n")}))
        self.assertEqual(f.severity, "warn")
        self.assertIn(f"kernel-modules-extra-{KERNEL}", f.reason)
        self.assertTrue(any("(Hyperkin)" in e for e in f.evidence))
        self.assertTrue(any("installed for other kernel" in e for e in f.evidence))

    def test_image_based_fix_belongs_in_the_image(self):
        self.distro("silverblue")
        self.mods(xpad=False)
        self.usb("1-4", "045e", "02ea", "Xbox Wireless Controller", ("ff", "47", "d0"))
        f = hardware.controllers(self.ctx({EXTRA_EXACT: exit_(1), EXTRA_ANY: exit_(1)}))
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("image has to include xpad" in g for g in f.guidance))

    def test_ubuntu_and_arch_never_query_rpm(self):
        for name, needle in (("ubuntu", "linux-modules-extra"), ("arch", "standard kernel")):
            with self.subTest(distro=name):
                tree = Tree()
                self.addCleanup(tree.cleanup)
                self.tree = tree
                self.distro(name)
                self.mods(xpad=False)
                f = hardware.controllers(self.ctx())
                self.assertEqual(f.severity, "info")
                self.assertFalse(any(c[0] == "rpm" for c in self.runner.calls))
                self.assertTrue(any(needle in g for g in f.guidance))

    def test_hid_mode_hyperkin_is_not_assumed_to_need_xpad(self):
        self.distro("fedora")
        self.mods(xpad=False)
        self.usb("1-4", "2e24", "1688", "Hyperkin pad", ("03", "00", "00"), driver="usbhid")
        self.assertEqual(hardware.controllers(self.ctx({EXTRA_EXACT: exit_(1), EXTRA_ANY: exit_(1)})).severity, "info")

    def test_joysticks_listed(self):
        self.tree.mkdir("/sys/module/xpad")
        self.tree.write("/proc/bus/input/devices",
                        'I: Bus=0003 Vendor=045e Product=028e Version=0110\nN: Name="Microsoft X-Box 360 pad"\n'
                        "H: Handlers=event20 js0\n\n")
        self.assertIn("1 gamepad", hardware.controllers(self.ctx()).summary)


class OtherHardwareTests(Base):
    def test_cpu_memory(self):
        self.tree.write("/proc/cpuinfo", "processor\t: 0\nmodel name\t: AMD Ryzen 7\n\nprocessor\t: 1\n")
        self.tree.write("/proc/meminfo", "MemTotal:       32000000 kB\nMemAvailable:   20000000 kB\n")
        self.assertEqual(hardware.cpu_memory(self.ctx()).severity, "pass")
        self.tree.write("/proc/meminfo", "MemTotal:       32000000 kB\nMemAvailable:   100000 kB\n")
        self.assertEqual(hardware.cpu_memory(self.ctx()).severity, "warn")

    def test_session(self):
        self.assertEqual(hardware.session(self.ctx(env={"XDG_SESSION_TYPE": "wayland",
                                                        "XDG_CURRENT_DESKTOP": "GNOME"})).severity, "pass")
        f = hardware.session(self.ctx(env={"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "XFCE"}))
        self.assertEqual(f.severity, "info")
        self.assertNotIn("Plasma", " ".join(f.guidance))

    def test_audio_pipewire_or_pulseaudio(self):
        env = {"XDG_RUNTIME_DIR": "/run/user/1000"}
        self.tree.write("/proc/asound/cards", " 0 [Generic        ]: HDA-Intel - HD-Audio Generic\n")
        self.tree.write("/run/user/1000/pulse/native", "")
        f = hardware.audio(self.ctx(env=env))
        self.assertEqual(f.severity, "pass")
        self.assertIn("PulseAudio", f.summary)
        self.tree.write("/run/user/1000/pipewire-0", "")
        self.assertIn("PipeWire", hardware.audio(self.ctx(env=env)).summary)
        self.tree.p("/run/user/1000/pipewire-0").unlink()
        self.tree.p("/run/user/1000/pulse/native").unlink()
        self.assertEqual(hardware.audio(self.ctx(env=env)).severity, "warn")
        self.assertEqual(hardware.audio(self.ctx()).severity, "pass", "no runtime dir: no claim about the server")

    def test_sensors(self):
        self.tree.write("/sys/class/hwmon/hwmon0/name", "k10temp")
        self.tree.write("/sys/class/hwmon/hwmon0/temp1_input", "61000")
        self.tree.write("/sys/class/hwmon/hwmon0/temp1_crit", "100000")
        self.assertEqual(hardware.sensors(self.ctx()).severity, "pass")
        self.tree.write("/sys/class/hwmon/hwmon0/temp1_input", "101000")
        self.assertEqual(hardware.sensors(self.ctx()).severity, "warn")


# ── Storage ─────────────────────────────────────────────────────────────────
OSTREE_MOUNTS = """\
22 1 0:21 / / ro,relatime shared:1 - overlay composefs ro,lowerdir+=/run/ostree/.private/cfsroot-lower
23 22 259:3 / /sysroot ro,relatime shared:2 - btrfs /dev/nvme0n1p3 ro,ssd,space_cache=v2
24 22 259:3 /root/ostree/deploy/default/deploy/abc.0/etc /etc rw,relatime shared:3 - btrfs /dev/nvme0n1p3 rw,ssd
25 22 259:3 /root/ostree/deploy/default/var /var rw,relatime shared:4 - btrfs /dev/nvme0n1p3 rw,ssd
26 22 259:2 / /boot rw,relatime shared:5 - ext4 /dev/nvme0n1p2 rw
"""
PACKAGE_MOUNTS = """\
22 1 259:3 / / rw,relatime shared:1 - ext4 /dev/nvme0n1p3 rw,errors=remount-ro
23 22 259:2 / /boot rw,relatime shared:2 - ext4 /dev/nvme0n1p2 rw
24 22 259:4 / /home rw,relatime shared:3 - ext4 /dev/nvme0n1p4 rw
"""
BTRFS_MOUNTS = """\
22 1 0:30 /@ / rw,relatime shared:1 - btrfs /dev/nvme0n1p2 rw,ssd,subvol=/@
23 22 0:31 /@var /var rw,relatime shared:2 - btrfs /dev/nvme0n1p2 rw,ssd,subvol=/@var
24 22 0:32 /@home /home rw,relatime shared:3 - btrfs /dev/nvme0n1p2 rw,ssd,subvol=/@home
"""
STEAMOS_MOUNTS = """\
22 1 259:5 / / ro,relatime shared:1 - btrfs /dev/nvme0n1p4 ro
23 22 0:40 / /etc rw,relatime shared:2 - overlay overlay rw,lowerdir=/etc,upperdir=/var/lib/overlays/etc/upper
24 22 259:7 / /var rw,relatime shared:3 - ext4 /dev/nvme0n1p6 rw
25 22 259:8 / /home rw,relatime shared:4 - ext4 /dev/nvme0n1p8 rw
"""


def fs(free, total):
    return SimpleNamespace(f_blocks=int(total) // 4096, f_bavail=int(free) // 4096, f_frsize=4096, f_fsid=0)


GiB, MiB = 1024 ** 3, 1024 ** 2


class StorageLayoutTests(Base):
    def test_ostree_readonly_usr_is_normal_and_home_on_var(self):
        self.distro("silverblue")
        self.tree.mkdir("/var/home/alice")
        self.tree.link("/home", "var/home")
        self.tree.write("/proc/self/mountinfo", OSTREE_MOUNTS)
        f = by_id(storage.layout(self.ctx()))
        self.assertEqual(f["storage.layout"].severity, "pass")
        self.assertIn("/usr read-only by design", f["storage.layout"].summary)
        self.assertEqual(f["storage.home"].severity, "pass")
        self.assertIn("/var", f["storage.home"].summary)

    def test_ostree_problems(self):
        self.distro("silverblue")
        self.tree.mkdir("/var/home/alice")
        self.tree.link("/home", "var/home")
        self.tree.write("/proc/self/mountinfo", OSTREE_MOUNTS.replace("/var rw,relatime", "/var ro,relatime"))
        f = by_id(storage.layout(self.ctx()))
        self.assertEqual(f["storage.layout"].severity, "error")
        self.assertEqual(f["storage.home"].severity, "error")
        self.tree.write("/proc/self/mountinfo", OSTREE_MOUNTS.replace("/dev/nvme0n1p3 rw,ssd\n", "/dev/nvme0n1p3 ro,ssd\n"))
        self.assertEqual(by_id(storage.layout(self.ctx()))["storage.layout"].severity, "error", "superblock ro")
        self.tree.write("/proc/self/mountinfo", OSTREE_MOUNTS +
                        "30 22 0:40 / /usr rw,relatime shared:9 - overlay overlay rw,lowerdir=/usr\n")
        self.assertEqual(by_id(storage.layout(self.ctx()))["storage.layout"].severity, "info")

    def test_ostree_home_off_var(self):
        self.distro("silverblue")
        self.tree.mkdir("/home/alice")
        self.tree.write("/proc/self/mountinfo", OSTREE_MOUNTS)
        self.assertEqual(by_id(storage.layout(self.ctx()))["storage.home"].severity, "error",
                         "a home on the read-only composefs root cannot be written")
        # older OSTree: / is the writable deployment checkout, which an update replaces
        legacy = OSTREE_MOUNTS.replace(
            "22 1 0:21 / / ro,relatime shared:1 - overlay composefs ro,lowerdir+=/run/ostree/.private/cfsroot-lower",
            "22 1 259:3 /root/ostree/deploy/default/deploy/abc.0 / rw,relatime shared:1 - btrfs /dev/nvme0n1p3 rw\n"
            "27 22 259:3 /root/ostree/deploy/default/deploy/abc.0/usr /usr ro,relatime shared:6 - btrfs "
            "/dev/nvme0n1p3 rw")
        self.tree.write("/proc/self/mountinfo", legacy)
        f = by_id(storage.layout(self.ctx()))
        self.assertEqual(f["storage.layout"].severity, "pass")
        self.assertEqual(f["storage.home"].severity, "warn")

    def test_package_based_layout(self):
        self.distro("debian")
        self.tree.mkdir("/home/alice")
        self.tree.write("/proc/self/mountinfo", PACKAGE_MOUNTS)
        f = by_id(storage.layout(self.ctx()))
        self.assertEqual(f["storage.layout"].severity, "pass")
        self.assertIn("/, /etc and /var are writable", f["storage.layout"].summary)
        self.assertEqual(f["storage.home"].severity, "pass")
        self.tree.write("/proc/self/mountinfo", PACKAGE_MOUNTS.replace("/ / rw,", "/ / ro,"))
        self.assertEqual(by_id(storage.layout(self.ctx()))["storage.layout"].severity, "error",
                         "a read-only root on a package-based system is an error remount")
        self.tree.write("/proc/self/mountinfo", PACKAGE_MOUNTS + "25 22 259:9 / /usr ro,relatime - ext4 /dev/sda1 ro\n")
        self.assertEqual(by_id(storage.layout(self.ctx()))["storage.layout"].severity, "warn")

    def test_steamos_and_tmpfs_home(self):
        self.distro("steamos")
        self.tree.mkdir("/home/alice")
        self.tree.write("/proc/self/mountinfo", STEAMOS_MOUNTS)
        f = by_id(storage.layout(self.ctx()))
        self.assertEqual(f["storage.layout"].severity, "pass")
        self.assertIn("System partition read-only by design", f["storage.layout"].summary)
        self.tree.write("/proc/self/mountinfo", STEAMOS_MOUNTS.replace("/ / ro,", "/ / rw,").replace(" ro\n", " rw\n", 1))
        self.assertIn("read-only mode is off", by_id(storage.layout(self.ctx()))["storage.layout"].summary)
        self.tree.write("/proc/self/mountinfo", STEAMOS_MOUNTS + "26 25 0:50 / /home/alice rw - tmpfs tmpfs rw\n")
        self.assertEqual(by_id(storage.layout(self.ctx()))["storage.home"].severity, "warn")


class FreeSpaceTests(Base):
    def space(self, mounts, sizes, name="debian"):
        self.distro(name)
        self.tree.mkdir("/home/alice")
        self.tree.write("/proc/self/mountinfo", mounts)
        ctx = self.ctx()
        asked = []

        def statvfs(path):
            asked.append(path)
            for prefix in sorted(sizes, key=len, reverse=True):
                if path == prefix or path.startswith(prefix.rstrip("/") + "/"):
                    return fs(*sizes[prefix])
            raise PermissionError(13, "Permission denied")
        ctx.statvfs = statvfs
        return storage.space(ctx), asked

    def test_thresholds(self):
        self.assertEqual(storage.judge("/", 500 * GiB, 200 * GiB)[0], "pass")
        self.assertEqual(storage.judge("/", 500 * GiB, 19 * GiB)[0], "warn", "below 5%, capped at 20 GiB")
        self.assertEqual(storage.judge("/", 2000 * GiB, 30 * GiB)[0], "pass", "big disks are not held to 5%")
        self.assertEqual(storage.judge("/", 60 * GiB, 4 * GiB)[0], "warn", "never less than 5 GiB")
        self.assertEqual(storage.judge("/sysroot", 60 * GiB, 8 * GiB, deployments=True)[0], "warn")
        self.assertEqual(storage.judge("/", 500 * GiB, 1.5 * GiB)[0], "error")
        self.assertEqual(storage.judge("/var", 256 * MiB, 100 * MiB)[0], "pass", "small filesystems by share")
        self.assertEqual(storage.judge("/var", 256 * MiB, 20 * MiB)[0], "warn")
        self.assertEqual(storage.judge("/boot", 1 * GiB, 150 * MiB)[0], "warn")
        self.assertEqual(storage.judge("/boot", 1 * GiB, 60 * MiB)[0], "error")
        self.assertEqual(storage.judge("/boot", 1 * GiB, 500 * MiB)[0], "pass")

    def test_separate_boot_is_checked(self):
        f, _ = self.space(PACKAGE_MOUNTS, {"/": (100 * GiB, 400 * GiB), "/boot": (61 * MiB, 943 * MiB),
                                           "/home": (300 * GiB, 900 * GiB)})
        self.assertEqual(f.severity, "error")
        self.assertEqual(f.summary, "Low free space: /boot")
        self.assertTrue(any("old kernels" in g for g in f.guidance))
        self.assertTrue(any("61 MiB free" in e for e in f.evidence))

    def test_btrfs_subvolumes_are_one_filesystem(self):
        f, _ = self.space(BTRFS_MOUNTS, {"/": (40 * GiB, 900 * GiB)}, name="tumbleweed")
        self.assertEqual(f.severity, "pass")
        self.assertEqual(len([e for e in f.evidence if "free of" in e]), 1)
        self.assertTrue(any("/ + /var + /home/alice" in e for e in f.evidence))
        f, _ = self.space(BTRFS_MOUNTS, {"/": (3 * GiB, 900 * GiB)}, name="tumbleweed")
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("snapper" in g for g in f.guidance))

    def test_ostree_skips_the_image_root(self):
        f, asked = self.space(OSTREE_MOUNTS, {"/sysroot": (80 * GiB, 400 * GiB), "/var": (80 * GiB, 400 * GiB),
                                              "/boot": (600 * MiB, 1 * GiB), "/home": (80 * GiB, 400 * GiB)},
                              name="silverblue")
        self.assertEqual(f.severity, "pass")
        self.assertNotIn("/", asked)
        self.assertTrue(any(e.startswith("/sysroot + /var") for e in f.evidence))

    def test_steamos_small_var_and_unknown(self):
        f, asked = self.space(STEAMOS_MOUNTS, {"/var": (120 * MiB, 256 * MiB), "/home": (200 * GiB, 460 * GiB)},
                              name="steamos")
        self.assertEqual(f.severity, "pass")
        self.assertNotIn("/", asked, "the SteamOS system partition is full by design")
        f, _ = self.space(PACKAGE_MOUNTS, {})
        self.assertEqual(f.severity, "unknown")


# ── Apps ─────────────────────────────────────────────────────────────────────
SYS_LIST = ("flatpak", "list", "--system", "--app", "--columns=application")
USER_LIST = ("flatpak", "list", "--user", "--app", "--columns=application")
SYS_REMOTES = ("flatpak", "remotes", "--system", "--columns=name")
USER_REMOTES = ("flatpak", "remotes", "--user", "--columns=name")
SESSION = {"XDG_SESSION_TYPE": "wayland", "XDG_DATA_DIRS": "/home/alice/.local/share/flatpak/exports/share:"
                                                           "/var/lib/flatpak/exports/share/:/usr/local/share:/usr/share"}


class FlatpakTests(Base):
    def run_fp(self, sys_apps="org.mozilla.firefox\ncom.valvesoftware.Steam\n", user_apps="", remotes="flathub\n",
               env=None):
        return software.flatpak(self.ctx({SYS_LIST: ok(sys_apps), USER_LIST: ok(user_apps),
                                          SYS_REMOTES: ok(remotes), USER_REMOTES: ok("")}, env=env or {}))

    def test_apps_and_remotes(self):
        f = self.run_fp()
        self.assertEqual(f.severity, "pass")
        self.assertIn("2 app(s) from 1 source(s) including Flathub", f.summary)
        f = self.run_fp(sys_apps="", remotes="")
        self.assertEqual(f.severity, "info")
        self.assertIn("no app sources", f.summary)

    def test_menu_integration_in_a_session(self):
        self.assertEqual(self.run_fp(env=SESSION).severity, "pass")
        f = self.run_fp(env={"XDG_SESSION_TYPE": "x11", "XDG_DATA_DIRS": "/usr/local/share:/usr/share"})
        self.assertEqual(f.severity, "warn")
        self.assertTrue(any("/var/lib/flatpak/exports/share" in e for e in f.evidence))
        f = self.run_fp(sys_apps="", user_apps="org.gimp.GIMP\n", env={"XDG_SESSION_TYPE": "wayland"})
        self.assertEqual(f.severity, "warn", "unset XDG_DATA_DIRS means the spec default, without Flatpak")
        f = self.run_fp(sys_apps="", user_apps="org.gimp.GIMP\n",
                        env={"XDG_SESSION_TYPE": "wayland",
                             "XDG_DATA_DIRS": "/var/home/alice/.local/share/flatpak/exports/share:/usr/share"})
        self.assertEqual(f.severity, "pass", "the user folder follows $HOME")
        self.tree.write("/.flatpak-info", "[Application]\n")
        self.assertEqual(self.run_fp(env={"XDG_SESSION_TYPE": "x11"}).severity, "pass", "no judgement from a sandbox")

    def test_flatpak_not_answering(self):
        self.assertEqual(software.flatpak(self.ctx(default=TIMEOUT)).severity, "unknown")


SNAP_LIST = ("snap", "list")
SNAPS = """Name                       Version          Rev    Tracking         Publisher   Notes
core22                     20250923         2133   latest/stable    canonical✓  base
firefox                    144.0-2          7024   latest/stable/…  mozilla✓    -
snapd                      2.72             25202  latest/stable    canonical✓  snapd
"""


class SnapTests(Base):
    def test_listing_broken_and_snapd_down(self):
        f = software.snap(self.ctx({SNAP_LIST: ok(SNAPS)}))
        self.assertEqual(f.severity, "pass")
        self.assertIn("3 snap(s)", f.summary)
        f = software.snap(self.ctx({SNAP_LIST: ok(SNAPS.replace("mozilla✓    -", "mozilla✓    broken"))}))
        self.assertEqual(f.severity, "warn")
        self.assertIn("firefox", f.summary)
        f = software.snap(self.ctx({SNAP_LIST: exit_(1, stderr='error: cannot communicate with server: Get '
                                                                '"http://localhost/v2/snaps": dial unix /run/snapd.socket')}))
        self.assertEqual(f.severity, "warn")
        self.assertIn("snapd is not running", f.summary)
        self.assertEqual(software.snap(self.ctx({SNAP_LIST: ok("", "No snaps are installed yet.")})).severity, "info")
        self.assertEqual(software.snap(self.ctx({SNAP_LIST: TIMEOUT})).severity, "unknown")

    def test_menu_integration(self):
        env = {"XDG_SESSION_TYPE": "wayland", "XDG_DATA_DIRS": "/usr/share/ubuntu:/usr/local/share:/usr/share"}
        self.assertEqual(software.snap(self.ctx({SNAP_LIST: ok(SNAPS)}, env=env)).severity, "warn")
        env["XDG_DATA_DIRS"] += ":/var/lib/snapd/desktop"
        self.assertEqual(software.snap(self.ctx({SNAP_LIST: ok(SNAPS)}, env=env)).severity, "pass")


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
        f = network.dns(self.ctx())
        self.assertEqual(f.severity, "warn")
        self.assertNotIn("Fedora", " ".join(f.guidance))
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
        f = network.manager(self.ctx())
        self.assertEqual(f.severity, "info")
        self.assertTrue(any("systemd-networkd" in g for g in f.guidance))


# ── Robustness: every probe, every family, hostile command results ──────────
class RobustnessTests(Base):
    def test_every_applicable_probe_survives_missing_tools_timeouts_and_garbage(self):
        from doctor_nox.model import Finding
        for name in PRESETS:
            tree = Tree()
            self.addCleanup(tree.cleanup)
            self.tree = tree
            self.distro(name)
            for default in (MISSING, TIMEOUT, ok("\x00\xff garbage {not json"), exit_(1, "", "denied")):
                ctx = self.ctx(default=default)
                ctx.statvfs = lambda path: fs(100 * GiB, 400 * GiB)
                todo, _ = engine.applicable(ctx, load_all())
                for p in todo:
                    with self.subTest(distro=name, probe=p.id, default=default[0]):
                        result = p.func(ctx)
                        for f in (result if isinstance(result, list) else [result]):
                            self.assertIsInstance(f, Finding)
                            if default[0] in ("missing", "timeout"):
                                self.assertNotEqual(f.severity, "error", f"{f.id} claims ERROR without evidence")


if __name__ == "__main__":
    unittest.main()
