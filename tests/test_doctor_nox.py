"""Doctor NoX core: runner safety, engine, reports/redaction, CLI, view model, GUI, packaging.

Nothing here runs Doctor's host probes or any mutating command. Runner tests
execute only a temporary Python helper script through a test-only allowlist entry.
"""
import ast
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_testlib import ROOT, Tree  # noqa: E402

from doctor_nox import (cli, clinic, engine, model, probes, record, report, runner, settings, theme,  # noqa: E402
                        viewmodel as vm)
from doctor_nox.model import Finding  # noqa: E402
from doctor_nox.probes import Probe  # noqa: E402

PKG = ROOT / "doctor_nox"
APP = ROOT / "bin/doctor-nox"
CLI = PKG / "cli.py"
DESKTOP = ROOT / "data/io.github.royoshi.doctornox.desktop"
SCHEMA = ROOT / "data/doctor-nox-report.schema.json"

HELPER = r'''
import os, subprocess, sys, time
mode = sys.argv[1]
if mode == "flood":
    sys.stdout.write("x" * (8 * 1024 * 1024)); sys.stdout.flush(); time.sleep(30)
elif mode == "sleep":
    time.sleep(30)
elif mode == "echo":
    sys.stdout.buffer.write("café ".encode() + b"\xff\xfe ok\n"); sys.stderr.write("warn\n")
elif mode == "child":
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    open(sys.argv[2], "w").write(str(child.pid)); time.sleep(60)
elif mode == "env":
    print("\n".join(sorted(os.environ)))
'''


def fixture_report(**kw):
    findings = [
        Finding("system.image.booted", "system", "Booted deployment", "pass", "Booted from ghcr.io/ublue-os/bazzite:stable",
                evidence=["digest sha256:" + "a" * 64]),
        Finding("updates.packages", "updates", "Package database", "error", "The RPM database could not be read"),
        Finding("network.link", "network", "Network links", "pass", "Connected on enp5s0",
                evidence=["enp5s0 (wired): up, addresses: 192.168.1.44, 2a02:8108:1:2::5",
                          "router mac 3c:7c:3f:aa:bb:cc"]),
        Finding("logs.boot", "logs", "Current boot errors", "warn", "1 critical entry",
                evidence=["sshd: user alice from fe-royo.lan", "home /var/home/alice/.config/app.ini",
                          "fetched https://bob:hunter2@example.com/feed?token=s3cr3tvalue&page=2",
                          "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJlMTIzNDU2",
                          "gh token ghp_" + "Z" * 36, "mail alice@example.org", "machine 0123456789abcdef0123456789abcdef",
                          "host FE-ROYO booted"]),
    ]
    import datetime
    now = datetime.datetime(2026, 10, 30, 20, 0, tzinfo=datetime.timezone.utc)
    host = {"name": "Bazzite 44 (FROM Fedora Kinoite)", "id": "bazzite", "version": "44", "family": "fedora",
            "image": "ostree", "kernel": "6.17.4-300.fc44.x86_64", "init": "systemd", "security": "selinux",
            "package_managers": ["rpm", "rpm-ostree", "bootc", "flatpak"]}
    skipped = [{"id": "software.snap", "category": "software", "title": "Snap",
                "applies_to": "systems with Snap"}] if kw.get("skipped") else []
    return model.build_report(findings, host, started=now, finished=now, complete=kw.get("complete", True),
                              not_applicable=skipped)


SECRETS = ("alice", "fe-royo", "FE-ROYO", "192.168.1.44", "2a02:8108:1:2::5", "3c:7c:3f:aa:bb:cc", "hunter2",
           "s3cr3tvalue", "eyJhbGciOiJIUzI1NiJ9", "ghp_ZZZZ", "alice@example.org", "0123456789abcdef0123456789abcdef",
           "/var/home/alice")


# ── runner ───────────────────────────────────────────────────────────────────
class RunnerAllowlistTests(unittest.TestCase):
    def test_unlisted_commands_and_arguments_are_refused_before_execution(self):
        r = runner.Runner()
        for name, args in (("bash", ("-c", "id")), ("systemctl", ("start", "fe-xicoos-upgrade.service")),
                           ("systemctl", ("is-enabled", "sshd.service")), ("systemctl", ("restart", "NetworkManager.service")),
                           ("bootc", ("upgrade",)), ("bootc", ("rollback",)), ("bootc", ("switch", "x")),
                           ("bootc", ("status", "--json")), ("rpm", ("-e", "foot")), ("rpm", ("-q", "--qf", "x", "foot")),
                           ("journalctl", ("--vacuum-size=1M",)), ("flatpak", ("install", "x")),
                           ("modinfo", ("-k", "6.1; rm -rf /", "-F", "filename", "xpad")),
                           ("modinfo", ("-k", "6.17", "-F", "filename", "evil")), ("nmcli", ("networking", "off")),
                           ("systemctl", ("show", "fe-xicoos-upgrade.service", "--property=ExecStart")),
                           ("systemctl", ("is-enabled", "fe-xicoos-upgrade.service")),
                           ("pacman", ("-Syu",)), ("pacman", ("-Rns", "foo")), ("pacman", ("-Q",)),
                           ("dpkg", ("--configure", "-a")), ("dpkg", ("-i", "x.deb")), ("apt-get", ("upgrade",)),
                           ("zypper", ("dup",)), ("snap", ("refresh",)), ("snap", ("remove", "firefox")),
                           ("rpm-ostree", ("upgrade",)), ("rpm-ostree", ("rollback",)), ("rpm-ostree", ("status",)),
                           ("flatpak", ("remotes", "--show-details")), ("flatpak", ("update",)),
                           ("rpm", ("-qa",)), ("rpm", ("-q", "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n",
                                                     "bash"))):
            with self.subTest(name=name, args=args), mock.patch("subprocess.Popen") as popen:
                with self.assertRaises(runner.CommandNotAllowed):
                    r.run(name, *args)
                popen.assert_not_called()

    def test_no_allowlisted_pattern_contains_a_mutating_verb(self):
        for name, spec in runner.ALLOWED.items():
            for pattern in spec.patterns:
                for token in pattern:
                    values = [token] if isinstance(token, str) else list(token) if isinstance(token, frozenset) else []
                    for value in values:
                        self.assertNotIn(value, runner.FORBIDDEN_VERBS, f"{name}: {pattern}")
                    if hasattr(token, "fullmatch"):
                        for verb in runner.FORBIDDEN_VERBS:
                            self.assertIsNone(token.fullmatch(verb), f"{name}: regex accepts {verb}")
            for path in spec.paths:
                self.assertTrue(path.startswith("/usr/"), path)

    def test_multi_call_tools_keep_the_name_they_were_found_under(self):
        """modinfo is a link to kmod: kmod picks its job from argv[0], so argv[0] must stay "modinfo"."""
        with tempfile.TemporaryDirectory() as tmp:
            link = os.path.join(tmp, "modinfo-like")
            os.symlink(os.path.realpath("/bin/sh"), link)
            spec = runner.CommandSpec((link,), (("-c", "echo $0"),), timeout=5.0)
            with mock.patch.dict(runner.ALLOWED, {"multicall": spec}):
                r = runner.Runner(resolve=lambda paths: (os.path.realpath(paths[0]), "ok"))
                res = r.run("multicall", "-c", "echo $0")
        self.assertTrue(res.ok, res.describe())
        self.assertEqual(res.stdout.strip(), link)

    def test_untrusted_and_missing_binaries(self):
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmp:
            os.chmod(tmp.name, 0o755)
        self.addCleanup(os.unlink, tmp.name)
        self.assertEqual(runner.trusted_executable((tmp.name,)), (None, "untrusted"))
        self.assertEqual(runner.trusted_executable(("/usr/bin/definitely-not-here-doctor",)), (None, "missing"))
        r = runner.Runner(resolve=lambda paths: (None, "untrusted"))
        res = r.run("getenforce")
        self.assertEqual(res.status, "untrusted")
        self.assertIn("refused", res.describe())


class RunnerProcessTests(unittest.TestCase):
    """Executes ONLY a temp helper script via a test-only allowlist entry."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.script = Path(self.dir.name) / "helper.py"
        self.script.write_text(HELPER)
        self.pidfile = str(Path(self.dir.name) / "child.pid")
        spec = runner.CommandSpec((sys.executable,), (
            (str(self.script), frozenset({"flood", "sleep", "echo", "env"})),
            (str(self.script), "child", self.pidfile)), timeout=2.0, max_stdout=64 * 1024)
        patcher = mock.patch.dict(runner.ALLOWED, {"helper": spec})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.cancel = threading.Event()
        self.r = runner.Runner(self.cancel, resolve=lambda paths: (paths[0], "ok"))

    def test_output_is_capped_and_producer_stopped(self):
        start = time.monotonic()
        res = self.r.run("helper", str(self.script), "flood")
        self.assertEqual(res.status, "ok", "a producer Doctor stopped at the cap is truncated output, not a failure")
        self.assertTrue(res.truncated)
        self.assertIn("truncated", res.describe())
        self.assertEqual(len(res.stdout), 64 * 1024)
        self.assertLess(time.monotonic() - start, 1.9, "flooding helper must be killed, not waited for")

    def test_timeout_kills_process(self):
        res = self.r.run("helper", str(self.script), "sleep", timeout=0.4)
        self.assertEqual(res.status, "timeout")
        self.assertLess(res.duration, 1.5)

    def test_cancel_kills_process_group_including_grandchildren(self):
        threading.Timer(0.6, self.cancel.set).start()
        res = self.r.run("helper", str(self.script), "child", self.pidfile)
        self.assertEqual(res.status, "cancelled")
        child = int(Path(self.pidfile).read_text())
        time.sleep(0.2)
        with contextlib.suppress(ChildProcessError):
            os.waitpid(child, os.WNOHANG)
        alive = Path(f"/proc/{child}").exists() and "zombie" not in Path(f"/proc/{child}/status").read_text().lower() \
            if Path(f"/proc/{child}").exists() else False
        self.assertFalse(alive, "grandchild survived cancellation")
        self.assertEqual(self.r.run("helper", str(self.script), "echo").status, "cancelled",
                         "nothing starts after cancellation")

    def test_unicode_invalid_bytes_and_stderr(self):
        res = self.r.run("helper", str(self.script), "echo")
        self.assertTrue(res.ok)
        self.assertIn("café", res.stdout)
        self.assertIn("�", res.stdout)
        self.assertEqual(res.stderr.strip(), "warn")

    def test_environment_is_minimal(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "secret", "LD_PRELOAD": "/tmp/x.so", "PYTHONPATH": "/tmp"}):
            res = self.r.run("helper", str(self.script), "env")
        names = set(res.stdout.split())
        self.assertFalse(names & {"GITHUB_TOKEN", "LD_PRELOAD", "PYTHONPATH"})
        self.assertIn("PATH", names)


# ── engine ───────────────────────────────────────────────────────────────────
class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tree = Tree()
        self.addCleanup(self.tree.cleanup)

    def test_probe_failure_is_isolated_and_report_validates(self):
        def good(ctx):
            return Finding("system.identity", "system", "OS identity", "pass", "ok")

        def broken(ctx):
            raise RuntimeError("boom")

        seen = []
        rep = engine.scan(self.tree.context(), probes=[Probe("system.identity", "system", "OS", good),
                                                       Probe("hardware.broken", "hardware", "Broken", broken)],
                          progress=lambda d, t, title: seen.append((d, t)))
        self.assertEqual(model.validate_report(rep), [])
        broken_f = next(f for f in rep["findings"] if f["id"] == "hardware.broken")
        self.assertEqual(broken_f["severity"], "unknown")
        self.assertIn("RuntimeError: boom", broken_f["reason"])
        self.assertEqual(sorted(seen), [(1, 2), (2, 2)])
        self.assertEqual(rep["overall"], "unknown", "UNKNOWN never rolls up to PASS")

    def test_deadline_cancels_and_marks_incomplete(self):
        def slow(ctx):
            ctx.cancel.wait(30)
            return Finding("logs.boot", "logs", "Logs", "pass", "late")

        start = time.monotonic()
        rep = engine.scan(self.tree.context(), probes=[Probe("logs.boot", "logs", "Logs", slow)], deadline=0.3)
        self.assertLess(time.monotonic() - start, 5)
        self.assertFalse(rep["complete"])

    def test_hung_probe_cannot_keep_the_process_alive(self):
        code = (
            "import sys, time; sys.path.insert(0, %r)\n"
            "from doctor_nox import engine\n"
            "from doctor_nox.context import Context\n"
            "from doctor_nox.probes import Probe\n"
            "def hung(ctx):\n    time.sleep(3600)  # ignores cancellation, like a hung NFS statvfs\n"
            "ctx = Context(root=__import__('pathlib').Path('/nonexistent-doctor-root'))\n"
            "rep = engine.scan(ctx, probes=[Probe('storage.hung', 'storage', 'Hung', hung)], deadline=0.5)\n"
            "print(rep['complete'], rep['findings'][0]['severity'], ctx.cancel.is_set())\n"
        ) % str(ROOT)
        start = time.monotonic()
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.split(), ["False", "unknown", "True"])
        self.assertLess(time.monotonic() - start, 15, "interpreter exit was blocked by a probe thread")

    def test_cancel_is_set_after_every_scan(self):
        ctx = self.tree.context()
        rep = engine.scan(ctx, probes=[Probe("system.x", "system", "X",
                                             lambda c: Finding("system.x", "system", "X", "pass", "ok"))])
        self.assertTrue(rep["complete"])
        self.assertTrue(ctx.cancel.is_set(), "stragglers and in-flight commands are told to stop")

    def test_registry_ids_unique_and_selection(self):
        ids = [p.id for p in probes.load_all()]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 20)
        rep = engine.scan(self.tree.context(), selected={"network"})
        self.assertTrue(rep["findings"])
        self.assertTrue(all(f["category"] == "network" for f in rep["findings"]))

    def test_full_scan_on_fixture_root_validates_against_json_schema(self):
        rep = engine.scan(self.tree.context(), deadline=30)
        self.assertEqual(model.validate_report(rep), [])
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed (structural validator above still ran)")
        schema = json.loads(SCHEMA.read_text())
        jsonschema.validate(rep, schema)
        jsonschema.validate(report.redact_report(rep, "alice", "fe-royo"), schema)

    def test_overall_rollup(self):
        f = lambda sev: Finding("system.x", "system", "X", sev, "s")  # noqa: E731
        self.assertEqual(model.overall([f("pass"), f("info")]), "pass")
        self.assertEqual(model.overall([f("pass"), f("unknown")]), "unknown")
        self.assertEqual(model.overall([f("unknown"), f("warn")]), "warn")
        self.assertEqual(model.overall([f("warn"), f("error")]), "error")
        self.assertEqual(model.overall([]), "unknown")
        with self.assertRaises(ValueError):
            Finding("bad id", "system", "X", "pass", "s")
        with self.assertRaises(ValueError):
            Finding("system.x", "system", "X", "ok", "s")


# ── reports & redaction ─────────────────────────────────────────────────────
class ReportTests(unittest.TestCase):
    def test_redacted_report_removes_identity_and_secrets(self):
        rep = fixture_report()
        shared = report.redact_report(rep, "alice", "fe-royo.lan")
        for text in (report.to_json(shared), report.format_text(shared), report.format_summary(shared)):
            for secret in SECRETS:
                self.assertNotIn(secret, text)
        self.assertTrue(shared["redacted"])
        self.assertIn("REDACTED SHARING REPORT", report.format_text(shared))
        self.assertIn("sha256:aaaaaaaaaaaa…", report.to_json(shared), "digests are shortened, not destroyed")
        self.assertIn("ghcr.io/ublue-os/bazzite:stable", report.to_json(shared))
        self.assertIn("alice", report.to_json(rep), "the full local report is not modified")
        self.assertEqual(shared["host"]["package_managers"], ["rpm", "rpm-ostree", "bootc", "flatpak"],
                         "list values stay lists")
        self.assertEqual(model.validate_report(shared), [])

    def test_redaction_keeps_ordinary_text(self):
        terms = report.identity_terms("alice", "fe-xico")
        for text in ("09:30:15 on 01:00.0", "fe-xicoos-auto-update.timer", "/usr/bin/doctor-nox", "apt-daily-upgrade.timer",
                     "nameserver 127.0.0.53", "No token found", "kernel 6.17.4-300.fc44.x86_64"):
            self.assertEqual(report.redact_text(text, terms), text)
        self.assertEqual(report.identity_terms("root", "localhost"), [])
        self.assertEqual(report.identity_terms("deck", "steamdeck"), [], "everyone's Steam Deck has these names")
        self.assertEqual(report.redact_text("Fedora Linux 44 on fedora", report.identity_terms("x", "fedora")),
                         "Fedora Linux 44 on fedora")

    def test_full_report_labelled_and_summary_lists_problems_only(self):
        rep = fixture_report(complete=False)
        text = report.format_text(rep)
        self.assertIn("FULL LOCAL REPORT", text)
        self.assertIn("INCOMPLETE", text)
        self.assertIn("No changes were made", text)
        summary = report.format_summary(rep)
        self.assertIn("Package database", summary)
        self.assertNotIn("Booted deployment", summary)
        self.assertIn("Bazzite 44 (FROM Fedora Kinoite) · Fedora / RHEL family · image-based (OSTree) · kernel", text)
        rep = fixture_report(skipped=True)
        self.assertIn("NOT APPLICABLE HERE", report.format_text(rep))
        self.assertIn("only on systems with Snap", report.format_text(rep))
        self.assertIn("1 check(s) do not apply", report.format_summary(rep))

    def test_export_permissions_and_atomic_write(self):
        rep = fixture_report()
        with tempfile.TemporaryDirectory() as tmp:
            full = Path(tmp) / "full.json"
            report.export(full, rep, redacted=False, fmt="json")
            self.assertEqual(full.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(full.read_text())["findings"][0]["id"], "system.image.booted")
            shared = Path(tmp) / "shared.txt"
            report.export(shared, rep, redacted=True, fmt="text", user="alice", hostname="fe-royo")
            self.assertEqual(shared.stat().st_mode & 0o777, 0o644)
            self.assertNotIn("192.168.1.44", shared.read_text())
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["full.json", "shared.txt"])
            with self.assertRaises(ValueError):
                report.export(full, rep, redacted=False, fmt="xml")


# ── CLI ──────────────────────────────────────────────────────────────────────
class CliTests(unittest.TestCase):
    def setUp(self):
        self.cli = cli

    def run_cli(self, *argv, scan=None):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(engine, "scan", scan or (lambda ctx, **kw: fixture_report())), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = self.cli.main(list(argv))
            except SystemExit as exc:
                code = exc.code
        return code, out.getvalue(), err.getvalue()

    def test_json_text_summary_and_exit_status(self):
        code, out, _ = self.run_cli("--json")
        self.assertEqual(code, 1, "an ERROR finding gives exit status 1")
        self.assertEqual(json.loads(out)["schema_version"], model.SCHEMA_VERSION)
        ok_report = lambda ctx, **kw: model.build_report(  # noqa: E731
            [Finding("system.x", "system", "X", "warn", "w")], {}, started=fixture_report and __import__("datetime").datetime.now(),
            finished=__import__("datetime").datetime.now(), complete=True)
        self.assertEqual(self.run_cli("--text", scan=ok_report)[0], 0, "WARN does not fail the CLI")
        code, out, _ = self.run_cli("--summary")
        self.assertIn("FULL LOCAL REPORT", out)

    def test_redact_flag_and_implicit_text(self):
        with mock.patch("doctor_nox.context.Context.__post_init__", lambda self: (
                setattr(self, "user", "alice"), setattr(self, "hostname", "fe-royo"), setattr(self, "runner", None))):
            code, out, _ = self.run_cli("--redact")
        self.assertIn("REDACTED SHARING REPORT", out)
        for secret in SECRETS:
            self.assertNotIn(secret, out)

    def test_usage_errors_and_internal_failure(self):
        self.assertEqual(self.run_cli("--only", "bogus")[0], 2)
        self.assertEqual(self.run_cli("--deadline", "1", "--text")[0], 2)
        self.assertEqual(self.run_cli("--json", "--text")[0], 2)

        def explode(ctx, **kw):
            raise RuntimeError("fixture failure")
        code, _, err = self.run_cli("--json", scan=explode)
        self.assertEqual(code, 3)
        self.assertIn("fixture failure", err)
        code, out, _ = self.run_cli("--list-checks")
        self.assertEqual(code, 0)
        self.assertIn("system.image", out)
        self.assertTrue(out.startswith("# "), "the first line names the system the list is for")

    def test_launcher_and_module_run_the_same_cli(self):
        for argv in ([sys.executable, str(APP), "--version"], [sys.executable, "-m", "doctor_nox", "--version"]):
            res = subprocess.run(argv, capture_output=True, text=True, timeout=60, cwd=ROOT)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertIn("Doctor NoX", res.stdout)
        self.assertIn("doctor_nox.cli import main", APP.read_text())

    def test_window_without_pyside_names_each_distributions_package(self):
        with mock.patch.dict(sys.modules, {"doctor_nox.gui": None}):
            code, _, err = self.run_cli()
        self.assertEqual(code, 3)
        for package in ("python3-pyside6", "pyside6 (Arch)", "python3-PySide6", "pipx"):
            self.assertIn(package, err)

    def test_cli_does_not_import_qt_for_reports(self):
        code = subprocess.run([sys.executable, "-c",
                               f"import runpy,sys; sys.argv=['doctor-nox','--list-checks'];"
                               f"runpy.run_path({str(APP)!r}, run_name='__main__')"],
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(code.returncode, 0, code.stderr)
        self.assertIn("hardware.controllers", code.stdout)


# ── view model (GUI logic without Qt) ───────────────────────────────────────
class ViewModelTests(unittest.TestCase):
    def test_grouping_filtering_and_ordering(self):
        rep = fixture_report()
        rows = {key: (total, worst) for key, _t, total, _c, worst in vm.category_rows(rep)}
        self.assertEqual(rows["overview"], (4, "error"))
        self.assertEqual(rows["security"], (0, None))
        items = vm.visible(rep)
        self.assertEqual([f["severity"] for f in items], ["error", "warn", "pass", "pass"])
        self.assertEqual([f["id"] for f in vm.visible(rep, query="is:problem")],
                         ["updates.packages", "logs.boot"])
        self.assertEqual(vm.header_text(rep)[0],
                         "Bazzite 44 (FROM Fedora Kinoite), image-based (OSTree), kernel 6.17.4-300.fc44.x86_64")
        self.assertEqual(len(vm.visible(rep, query="enp5s0 connected")), 1)
        self.assertEqual(vm.visible(rep, "network", severities=frozenset({"error"})), [])
        self.assertIn("No tests match", vm.empty_state(rep, False, "network", "", frozenset({"error"})))
        self.assertIn("Press Start checkup", vm.empty_state(None, False, "overview", "", frozenset(model.SEVERITIES)))
        self.assertEqual([f["id"] for f in vm.visible(rep, query="is:treat")], ["updates.packages"])
        self.assertEqual({f["severity"] for f in vm.visible(rep, query="is:healthy")}, {"pass"})

    def test_detail_html_escapes_and_explains(self):
        f = Finding("logs.boot", "logs", "Boot <b>", "unknown", "x <script>alert(1)</script>",
                    evidence=["<img src=x onerror=alert(1)>"], reason="denied", guidance=["run `x`"]).to_dict()
        html = vm.detail_html(f)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("never carries out these steps", html)
        self.assertIn("Couldn't examine", html)  # the condition is always spelled out, not colour alone
        for heading in ("Diagnosis", "What I found", "Treatment", "Test ID"):
            self.assertIn(heading, html)
        self.assertIn("not a score", vm.overview_html(fixture_report()))
        self.assertIn("only on systems with Snap", vm.overview_html(fixture_report(skipped=True)))
        self.assertNotIn("NOT APPLICABLE HERE", vm.overview_html(fixture_report()))
        self.assertIn("any Linux", vm.overview_html(None))
        self.assertIn(clinic.MOTTO, vm.overview_html(None))

    def test_sidebar_badges_count_problems_and_mark_healthy_groups(self):
        zero = {sev: 0 for sev in model.SEVERITIES}
        self.assertEqual(vm.category_badge(zero, 0), ("", None))
        self.assertEqual(vm.category_badge(dict(zero, error=1, warn=2, unknown=1, info=1, pass_=0), 5), ("4", "error"))
        self.assertEqual(vm.category_badge(dict(zero, warn=1, unknown=2), 3), ("3", "warn"))
        self.assertEqual(vm.category_badge(dict(zero, unknown=1, info=1), 2), ("1", "unknown"))
        self.assertEqual(vm.category_badge(dict(zero, info=2, **{"pass": 3}), 5), ("OK", "pass"))
        self.assertEqual(vm.category_accessible("Logs", dict(zero, warn=2), 4), "Logs: 4 tests, 2 to keep an eye on")
        self.assertEqual(vm.category_accessible("Logs", dict(zero, **{"pass": 4}), 4), "Logs: 4 tests, all fine")
        self.assertEqual(vm.category_accessible("Logs", dict(zero, error=1), 1), "Logs: 1 test, 1 needs treatment")

    def test_counts_and_durations_read_naturally(self):
        totals = {"error": 1, "warn": 2, "unknown": 1, "info": 4, "pass": 6}
        self.assertEqual(vm.count_summary(totals),
                         "1 needs treatment, 2 to keep an eye on, 1 couldn't be examined, 4 notes and 6 healthy")
        self.assertEqual(theme.status_count("error", 2), "2 need treatment")
        self.assertEqual(theme.status_count("info", 1), "1 note")
        self.assertEqual([vm.duration_text(ms) for ms in (420, 1840, 12345, -5)], ["0.4 s", "1.8 s", "12 s", "0.0 s"])

    def test_html_uses_the_requested_theme_and_never_neon(self):
        rep = fixture_report()
        finding = rep["findings"][0]
        for t in theme.THEMES.values():
            for html in (vm.detail_html(finding, t, "Hack"), vm.overview_html(rep, False, t),
                         vm.overview_html(None, True, t), vm.overview_html(fixture_report(complete=False), False, t),
                         vm.error_html("boom <script>", t)):
                self.assertIn(t.text, html)
                for neon in ("#00ff00", "#ff00ff", "#000000"):
                    self.assertNotIn(neon, html.lower())
        self.assertIn("Checkup stopped early", vm.overview_html(fixture_report(complete=False)))
        self.assertNotIn("<script>", vm.error_html("boom <script>"))
        self.assertIn("font-family:'Hack'", vm.detail_html(finding, theme.DARK_THEME, "Hack"))


# ── the doctor: verdict, what's wrong, follow-ups, the note ─────────────────
def checkup(*findings, complete=True, at=(2026, 11, 16, 18, 0)):
    """A report of (id, severity[, guidance]) findings; titles are the IDs' last word, capitalised."""
    import datetime
    when = datetime.datetime(*at, tzinfo=datetime.timezone.utc)
    items = [Finding(i, i.split(".")[0], i.split(".")[-1].replace("_", " ").capitalize(), sev, f"{i} summary",
                     guidance=list(extra[:1])) for i, sev, *extra in findings]
    return model.build_report(items, {"name": "Test Linux", "kernel": "6.1"}, started=when, finished=when,
                              complete=complete)


class ClinicTests(unittest.TestCase):
    def test_the_verdict_says_the_most_important_thing_first(self):
        well = clinic.verdict(checkup(("storage.space", "pass"), ("hardware.audio", "info")))
        self.assertEqual(well, ["Your PC is in good health: 2 tests, nothing to treat."])

        ill = clinic.verdict(checkup(("storage.space", "error", "free some"), ("security.failed_units", "warn"),
                                     ("hardware.audio", "pass"), ("logs.boot", "unknown")))
        self.assertEqual(ill[0], "Your PC needs treatment.")
        self.assertEqual(ill[1], "1 thing needs treatment: Space.")
        self.assertIn("Keep an eye on Failed units.", ill)
        self.assertIn("Start with Space.", ill)
        self.assertIn("I couldn't examine Boot, so I can't call it healthy.", ill)
        self.assertLessEqual(max(map(len, ill)), 60, "the verdict is short sentences")
        self.assertEqual(ill[-1], "The other 1 test came back fine.")

        watch = clinic.verdict(checkup(("hardware.sensors", "warn"), ("hardware.audio", "pass")))
        self.assertEqual(watch, ["Your PC is mostly well.", "Keep an eye on Sensors.", "The other 1 test came back fine."])
        self.assertFalse(any(line.startswith("Start with") for line in watch), "one finding needs no order")
        unseen = clinic.verdict(checkup(("logs.boot", "unknown")))
        self.assertEqual(unseen[0], "Nothing wrong that I could see, but I couldn't examine everything.")
        self.assertIn("stopped early", clinic.verdict(checkup(("logs.boot", "unknown"), complete=False))[-1])
        many = clinic.verdict(checkup(*[(f"storage.t{n}", "error") for n in range(5)]))
        self.assertEqual(many[1], "5 things need treatment: T0, T1 and 3 more.")
        self.assertEqual(clinic.verdict(checkup()), ["I had no tests to run on this system."])

    def test_whats_wrong_examines_the_tests_for_the_complaint(self):
        rep = checkup(("hardware.audio", "pass"), ("hardware.session", "pass"), ("storage.space", "error"),
                      ("network.link", "warn"), ("network.dns", "pass"), ("system.image.pending", "info"))
        self.assertEqual([s.key for s in clinic.SYMPTOMS][0], clinic.GENERAL)
        self.assertEqual(len({s.label for s in clinic.SYMPTOMS}), len(clinic.SYMPTOMS))
        shown = lambda symptom: [f["id"] for f in vm.visible(rep, symptom=symptom)]  # noqa: E731
        self.assertEqual(len(shown(clinic.GENERAL)), 6)
        self.assertEqual(shown("sound"), ["hardware.audio", "hardware.session"])
        self.assertEqual(shown("network"), ["network.link", "network.dns"])
        self.assertEqual(shown("updates"), ["storage.space", "system.image.pending", "network.dns"])
        self.assertFalse(clinic.related("hardware.audio_extra", "sound"), "an ID is matched whole, not by its start")
        self.assertTrue(clinic.related("anything.at_all", "no such complaint"), "an unknown complaint hides nothing")

        sound = clinic.verdict(rep, "sound")
        self.assertEqual(sound[0], "For “No sound” I looked at 2 tests. All fine, so the cause is somewhere I can't "
                                   "see. Copy the doctor's note and share it where you ask for help.")
        self.assertEqual(sound[1], "Elsewhere: Space and Link need a look.")
        net = clinic.verdict(rep, "network")
        self.assertIn("Nothing is broken, but one thing bears watching.", net[0])
        self.assertIn("Keep an eye on Link.", net)
        self.assertEqual(net[-1], "Elsewhere: Space needs a look.")
        none = clinic.verdict(checkup(("storage.space", "pass")), "sound")
        self.assertIn("I have no tests for “No sound” on this system.", none[0])
        self.assertIn("in good health", none[1])
        self.assertIn("No tests for “No sound” in this area",
                      vm.empty_state(rep, False, "storage", "", frozenset(model.SEVERITIES), "sound"))
        self.assertIn("No sound", vm.overview_html(rep, symptom="sound"))

    def test_follow_up_says_what_changed_and_never_calls_unseen_better(self):
        import datetime
        now = datetime.datetime(2026, 11, 16, 20, 0, tzinfo=datetime.timezone.utc)
        before = checkup(("storage.space", "pass"), ("hardware.audio", "warn"), ("logs.boot", "warn"),
                         ("network.link", "unknown"), ("network.dns", "unknown"), ("updates.reboot", "info"),
                         at=(2026, 11, 12, 9, 0))
        after = checkup(("storage.space", "error"), ("hardware.audio", "pass"), ("logs.boot", "unknown"),
                        ("network.link", "warn"), ("network.dns", "pass"), ("updates.reboot", "pass"),
                        ("hardware.sensors", "error"))
        follow = clinic.follow_up(after, record.snapshot(before))
        self.assertEqual(follow["changes"], {"storage.space": ("worse", "pass"), "hardware.audio": ("better", "warn"),
                                             "logs.boot": ("unseen", "warn"), "network.link": ("worse", "unknown")})
        text = clinic.follow_up_text(after, follow, now)
        self.assertEqual(text[0], "Worse since your last checkup 4 days ago (12 Nov): Space (was Healthy, now Needs "
                                  "treatment); Link (was Couldn't examine, now Keep an eye on it).")
        self.assertEqual(text[1], "Better: Audio (was Keep an eye on it, now Healthy).")
        self.assertEqual(text[2], "I couldn't examine Boot this time.")
        self.assertEqual(clinic.follow_up_text(before, clinic.follow_up(before, record.snapshot(
            checkup(("storage.space", "pass"), at=(2026, 11, 15, 9, 0)))), now),
            ["Nothing has changed since your last checkup yesterday."])
        self.assertIsNone(clinic.follow_up(after, None))
        self.assertIsNone(clinic.follow_up(after, record.snapshot(after)), "a checkup is not compared with itself")
        self.assertEqual(clinic.follow_up_text(after, None), [])
        self.assertEqual(clinic.when_text("2026-11-16T01:00:00+00:00", now), "earlier today")
        self.assertEqual(clinic.when_text("2025-12-30T12:00:00+00:00", now), "321 days ago (30 Dec 2025)")
        self.assertEqual(clinic.when_text("not a time", now), "")

        html = vm.overview_html(after, follow=follow, now=now)
        self.assertIn("Since your last checkup", html)
        self.assertIn("Worse since your last checkup 4 days ago", html)
        self.assertNotIn("Since your last checkup", vm.overview_html(after))
        detail = vm.detail_html(after["findings"][0], change=follow["changes"]["storage.space"])
        self.assertIn("Worse since your last checkup: it was Healthy.", detail)
        self.assertNotIn("since your last checkup", vm.detail_html(after["findings"][2],
                                                                   change=follow["changes"]["logs.boot"]))

    def test_vital_signs_before_and_after_a_checkup(self):
        blank = clinic.vitals(None)
        self.assertEqual([v["label"] for v in blank], ["Temperature", "Memory", "Disk space", "Restart"])
        self.assertTrue(all(v["value"] == clinic.NO_VALUE and v["severity"] is None for v in blank))
        signs = {v["key"]: v for v in clinic.vitals(checkup(("hardware.sensors", "unknown"),
                                                             ("hardware.cpu_memory", "pass")))}
        self.assertEqual(signs["temperature"]["value"], "Couldn't read")
        self.assertEqual(signs["memory"]["value"], "hardware.cpu_memory summary", "an unknown wording is shown as it is")
        self.assertEqual(signs["memory"]["finding_id"], "hardware.cpu_memory")
        self.assertEqual((signs["disk"]["value"], signs["disk"]["severity"], signs["disk"]["finding_id"]),
                         ("Not examined", None, ""))

    def test_doctors_note_is_labelled_redacted_and_prescribes(self):
        rep = fixture_report()
        shared = report.redact_report(rep, "alice", "fe-royo")
        note = clinic.note(shared)
        for secret in SECRETS:
            self.assertNotIn(secret, note)
        self.assertIn("doctor's note", note.splitlines()[0])
        self.assertIn("REDACTED SHARING REPORT", note)
        self.assertIn("Patient:  Bazzite 44", note)
        self.assertIn("Verdict:  Needs treatment (1 needs treatment, 1 to keep an eye on", note)
        self.assertIn("NEEDS TREATMENT\n- Updates & Packages / Package database", note)
        self.assertIn("KEEP AN EYE ON\n- Logs / Current boot errors", note)
        self.assertIn("nothing on this PC was changed", note.splitlines()[-1])
        self.assertIn("FULL LOCAL REPORT", clinic.note(rep))
        self.assertIn("1 test for other kinds of Linux did not apply here and was not run.",
                      clinic.note(fixture_report(skipped=True)))
        full = clinic.note(checkup(("storage.space", "error", "free some room"), ("logs.boot", "info")))
        self.assertIn("    Treatment: free some room", full)
        self.assertIn("NOTES\n- Logs / Boot", full)

    def test_whats_wrong_can_be_typed_in_your_own_words(self):
        for typed, meant in (("my wifi keeps dropping", "network"), ("No sound from the speakers!", "sound"),
                             ("PC is really slow and laggy", "slow"), ("games stutter, low FPS", "graphics"),
                             ("can't install apps", "apps"), ("the disk is full", "space"),
                             ("is it safe? do I have a virus", "secure"), ("fan is so loud", "hot"),
                             ("Xbox controller not detected", "controller"), ("updates fail", "updates"),
                             ("it crashes on boot", "crash"), ("black screen", "graphics"),
                             ("black screen on boot", "crash"),
                             ("my printer is haunted", None), ("", None), ("???", None)):
            self.assertEqual(clinic.guess(typed), meant, typed)
        for symptom in clinic.SYMPTOMS[1:]:
            self.assertTrue(symptom.words and symptom.tests, symptom.key)
            self.assertEqual(clinic.guess(symptom.words[0]), symptom.key, "its own first word means it")
            self.assertTrue(all(word == word.lower() for word in symptom.words), symptom.key)

    def test_the_heart_monitor_trace_fits_its_strip(self):
        for width, height in ((240, 36), (60, 34), (900, 40), (35, 34)):
            points = clinic.pulse(width, height)
            xs, ys = [x for x, _ in points], [y for _, y in points]
            self.assertEqual((xs[0], xs[-1]), (0, width))
            self.assertEqual(xs, sorted(xs), "the trace never turns back")
            self.assertTrue(0 <= min(ys) and max(ys) <= height, (width, height, min(ys), max(ys)))
            self.assertEqual(ys[0], ys[-1], "it starts and ends on the baseline")
            self.assertLess(min(ys), height * 0.15, "the spike reaches near the top")
        self.assertEqual(clinic.pulse(0, 36), [])
        points = clinic.pulse(240, 36)
        self.assertEqual(clinic.pulse_y(points, 0), points[0][1])
        self.assertEqual(clinic.pulse_y(points, 9999), points[-1][1])
        (x0, y0), (x1, y1) = points[5], points[6]
        self.assertAlmostEqual(clinic.pulse_y(points, (x0 + x1) / 2), (y0 + y1) / 2)
        self.assertEqual(clinic.pulse_y([], 5), 0.0)

    def test_conditions_are_worded_for_every_severity_and_json_keeps_its_own(self):
        for words in (theme.STATUS_TEXT, theme.STATUS_COUNT, theme.STATUS_MEANING, theme.HEADLINE, clinic.CONCERN):
            self.assertEqual(set(words), set(model.SEVERITIES))
        self.assertEqual(theme.STATUS_TEXT["pass"], "Healthy")
        self.assertEqual(json.loads(report.to_json(fixture_report()))["findings"][1]["severity"], "error")
        self.assertIn("[ERROR  ]", report.format_text(fixture_report()), "--text keeps the report's severities")


class RecordTests(unittest.TestCase):
    def test_the_last_checkup_is_remembered_privately_and_only_when_finished(self):
        rep = checkup(("storage.space", "error"), ("hardware.audio", "pass"))
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state" / "doctor-nox" / record.FILE_NAME
            self.assertIsNone(record.load(path))
            self.assertTrue(record.save(path, rep))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual([p.name for p in path.parent.iterdir()], [record.FILE_NAME], "no temporary file is left")
            kept = json.loads(path.read_text())
            self.assertEqual(kept, {"version": 1, "generated_at": rep["generated_at"],
                                    "findings": {"storage.space": "error", "hardware.audio": "pass"}})
            self.assertEqual(record.load(path), kept)
            self.assertFalse(record.save(path, checkup(("storage.space", "pass"), complete=False)))
            self.assertFalse(record.save(path, report.redact_report(rep)))
            self.assertFalse(record.save(None, rep))
            self.assertEqual(record.load(path), kept, "an unfinished checkup never replaces a finished one")
            self.assertFalse(record.save(Path(tmp) / "state" / "doctor-nox" / record.FILE_NAME / "x", rep),
                             "a place that cannot be written is not an error")
            for junk in ("", "not json", "[]", '{"version": 2, "generated_at": "x", "findings": {}}',
                         '{"version": 1, "generated_at": 5, "findings": {}}', "x" * (record.MAX_BYTES + 1)):
                path.write_text(junk)
                self.assertIsNone(record.load(path), junk[:30])
            path.write_text('{"version": 1, "generated_at": "t", "findings": {"a.b": "pass", "c.d": "bogus"}}')
            self.assertEqual(record.load(path)["findings"], {"a.b": "pass"})

    def test_where_it_is_kept(self):
        self.assertEqual(record.default_path({"XDG_STATE_HOME": "/s", "HOME": "/h"}),
                         Path("/s/doctor-nox/last-checkup.json"))
        self.assertEqual(record.default_path({"XDG_STATE_HOME": "relative", "HOME": "/h"}),
                         Path("/h/.local/state/doctor-nox/last-checkup.json"))

    def test_only_the_window_a_person_opened_remembers(self):
        """Reports, self-tests and screenshots must leave no file behind (image builds run them)."""
        for module in ("cli.py", "engine.py", "report.py", "viewmodel.py", "clinic.py"):
            self.assertNotRegex((PKG / module).read_text(), r"import.*\brecord\b|\brecord\.(save|load)", module)
        tree = ast.parse((PKG / "gui.py").read_text())
        funcs = {n.name: ast.unparse(n) for n in tree.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("Window(record_path=record.default_path(), settings_path=settings_store.default_path())",
                      funcs["run"])
        for name in ("smoke_test", "screenshots", "self_test", "_self_test_steps", "_demo_report"):
            self.assertNotIn("default_path", funcs[name], name)
        writers = [p.name for p in PKG.rglob("*.py") if "os.replace(" in p.read_text()]
        self.assertEqual(sorted(writers), ["record.py", "report.py", "settings.py"],
                         "only reports you save, the record and your settings are written")
        self.assertTrue(record.forget(None))


class SettingsTests(unittest.TestCase):
    def test_defaults_and_anything_unusable_falls_back(self):
        defaults = settings.clean(None)
        self.assertEqual(defaults, {"theme": "system", "checkup_at_launch": True, "remember": True,
                                    "monitor_motion": True, "skip_areas": [], "deadline": 60})
        self.assertIsNot(settings.clean(None)["skip_areas"], settings.DEFAULTS["skip_areas"])
        for junk in ([], "x", 5, {"theme": "neon", "remember": "yes", "deadline": "soon", "skip_areas": "logs"},
                     {"deadline": True}, {"skip_areas": list(settings.AREAS)}, {"unknown": 1}):
            self.assertEqual(settings.clean(junk), defaults, junk)
        mine = settings.clean({"theme": "dark", "checkup_at_launch": False, "remember": False,
                               "monitor_motion": False, "skip_areas": ["logs", "nonsense", "hardware"],
                               "deadline": 9000})
        self.assertEqual(mine, {"theme": "dark", "checkup_at_launch": False, "remember": False,
                                "monitor_motion": False, "skip_areas": ["hardware", "logs"], "deadline": 300})
        self.assertEqual(settings.clean({"deadline": 1})["deadline"], 5)
        self.assertEqual(settings.clean({"deadline": 42.9})["deadline"], 42)
        self.assertEqual(set(settings.AREAS), set(model.CATEGORY_TITLE))

    def test_saved_privately_and_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config" / "doctor-nox" / settings.FILE_NAME
            self.assertEqual(settings.load(path), settings.clean(None))
            self.assertTrue(settings.save(path, {"theme": "light", "skip_areas": ["network"], "bogus": 1}))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual([p.name for p in path.parent.iterdir()], [settings.FILE_NAME])
            self.assertEqual(json.loads(path.read_text()), dict(settings.clean(None), theme="light",
                                                                skip_areas=["network"]))
            self.assertEqual(settings.load(path)["theme"], "light")
            for junk in ("", "{", "[1]", "x" * (settings.MAX_BYTES + 1)):
                path.write_text(junk)
                self.assertEqual(settings.load(path), settings.clean(None), junk[:20])
            self.assertFalse(settings.save(None, {}))
            self.assertFalse(settings.save(path / "under-a-file", {}))
        self.assertEqual(settings.default_path({"XDG_CONFIG_HOME": "/c", "HOME": "/h"}),
                         Path("/c/doctor-nox/settings.json"))
        self.assertEqual(settings.default_path({"HOME": "/h"}), Path("/h/.config/doctor-nox/settings.json"))

    def test_a_checkup_is_asked_only_for_what_differs_from_the_defaults(self):
        self.assertEqual(settings.scan_options(settings.clean(None)), {})
        options = settings.scan_options(settings.clean({"skip_areas": ["logs", "network"], "deadline": 20}))
        self.assertEqual(options, {"selected": set(settings.AREAS) - {"logs", "network"}, "deadline": 20.0})
        rep = fixture_report()
        self.assertIn("Left out by your settings", vm.overview_html(rep, skipped_areas=["logs", "nope"]))
        self.assertIn("Logs. Turn them back on", vm.overview_html(rep, skipped_areas=["logs", "nope"]))
        self.assertNotIn("Left out by your settings", vm.overview_html(rep))
        self.assertEqual({v["key"]: v for v in clinic.vitals(rep)}["disk"]["detail"],
                         "No test in this checkup measured it.")

    def test_only_the_window_uses_settings(self):
        for module in ("cli.py", "engine.py", "report.py", "viewmodel.py", "clinic.py", "record.py"):
            self.assertNotRegex((PKG / module).read_text(), r"\bsettings\b.*import|import.*\bsettings\b", module)


# ── theme (light/dark palettes) ─────────────────────────────────────────────
class ThemeTests(unittest.TestCase):
    def test_every_text_pair_meets_wcag_aa_in_both_themes(self):
        for t in theme.THEMES.values():
            backgrounds = (t.window, t.surface, t.surface_alt, t.hover, t.accent_soft)
            for bg in backgrounds:
                self.assertGreaterEqual(theme.contrast(t.text, bg), 7.0, f"{t.name} text on {bg}")
                self.assertGreaterEqual(theme.contrast(t.muted, bg), 4.5, f"{t.name} muted on {bg}")
            self.assertGreaterEqual(theme.contrast(t.on_accent, t.accent), 4.5, f"{t.name} primary button")
            self.assertGreaterEqual(theme.contrast(t.accent, t.surface), 3.0, f"{t.name} focus ring")
            self.assertEqual(set(t.status), set(model.SEVERITIES))
            for sev, s in t.status.items():
                for bg in (s.bg, t.surface, t.surface_alt, t.accent_soft):
                    self.assertGreaterEqual(theme.contrast(s.fg, bg), 4.5, f"{t.name} {sev} on {bg}")

    def test_themes_are_designed_not_inverted_and_accent_is_not_a_status(self):
        light, dark = theme.LIGHT_THEME, theme.DARK_THEME
        self.assertGreater(theme.luminance(light.window), 0.8)
        self.assertLess(theme.luminance(dark.window), 0.02)
        self.assertNotEqual(dark.window, "#000000", "no OLED black: the window is a deep night blue")
        for t in (light, dark):
            self.assertNotIn(t.accent, {s.fg for s in t.status.values()})
            self.assertEqual(len({s.fg for s in t.status.values()}), 5, "every status has its own colour")
            for value in vars(t).values():
                if isinstance(value, str):
                    self.assertNotIn(value.lower(), ("#00ff00", "#ff00ff"))

    def test_nox_mark_cross_sits_in_the_moons_hollow(self):
        """The cross never touches the crescent: every corner of both arms lies inside the cut-out circle."""
        for mark in (theme.NOX_MARK, theme.NOX_MARK_SMALL):
            (cx, cy, cr), (x, y, length, thick) = mark.cut, mark.cross
            for half_w, half_h in ((thick / 2, length / 2), (length / 2, thick / 2)):
                for dx in (-half_w, half_w):
                    for dy in (-half_h, half_h):
                        self.assertLess(((x + dx - cx) ** 2 + (y + dy - cy) ** 2) ** 0.5, cr, mark)
            mx, my, mr = mark.moon
            # Symmetric about its diagonal: the moon, the cut-out and the cross lie on one 45-degree line.
            for px, py in (mark.cut[:2], mark.cross[:2]):
                self.assertAlmostEqual(px - mx, -(py - my), msg=mark)
            # Optically centred: the outline sits just up-right of the grid's centre (the crescent's
            # weight sits down-left), and the mark keeps clear of the grid's edges.
            left, right = mx - mr, max(mx + mr, x + length / 2)
            top, bottom = min(my - mr, y - length / 2), my + mr
            centre_x, centre_y = (left + right) / 2, (top + bottom) / 2
            self.assertTrue(0 < centre_x - 50 <= 3 and 0 < 50 - centre_y <= 3, (mark, centre_x, centre_y))
            self.assertGreaterEqual(min(left, top, 100 - right, 100 - bottom), 8, mark)
            self.assertLessEqual(mark.scale, 1.0)
            # Rounded tips: each fillet touches the moon's edge from inside and the cut-out's from outside.
            tips = theme.crescent_tips(mark)
            self.assertEqual(len(tips), 2)
            for (fx, fy), quad in tips:
                self.assertAlmostEqual(((fx - mx) ** 2 + (fy - my) ** 2) ** 0.5, mr - mark.tip, places=6)
                self.assertAlmostEqual(((fx - cx) ** 2 + (fy - cy) ** 2) ** 0.5, cr + mark.tip, places=6)
                self.assertEqual(len(quad), 4)
        self.assertIs(theme.mark_for(16), theme.NOX_MARK_SMALL)
        self.assertIs(theme.mark_for(25), theme.NOX_MARK)
        for tile in theme.ICON_TILE:  # WCAG 1.4.11: graphics at least 3:1 against what is behind them
            self.assertGreaterEqual(theme.contrast(theme.DARK_THEME.accent, tile), 3.0)
            self.assertGreaterEqual(theme.contrast(theme.DARK_THEME.text, tile), 3.0)

    def test_mode_selection(self):
        self.assertEqual(theme.requested_mode({}), "system")
        self.assertEqual(theme.requested_mode({"DOCTOR_NOX_THEME": " Light "}), "light")
        self.assertEqual(theme.requested_mode({"DOCTOR_NOX_THEME": "neon"}), "system")
        self.assertIs(theme.resolve("light", True), theme.LIGHT_THEME)
        self.assertIs(theme.resolve("dark", False), theme.DARK_THEME)
        self.assertIs(theme.resolve("system", True), theme.DARK_THEME)
        self.assertIs(theme.resolve("system", False), theme.LIGHT_THEME)
        self.assertEqual(set(theme.STATUS_TEXT), set(model.SEVERITIES))
        self.assertEqual(set(theme.HEADLINE), set(model.SEVERITIES))


# ── safety audit of the source tree ─────────────────────────────────────────
class SourceSafetyTests(unittest.TestCase):
    def test_only_runner_starts_processes_and_nothing_elevates_or_writes_system_state(self):
        banned_calls = {"os.system", "os.popen", "os.exec", "os.spawn", "pty.spawn", "os.chmod", "os.chown",
                        "shutil.rmtree", "os.remove", "os.rename"}
        for path in [*PKG.rglob("*.py"), APP]:
            source = path.read_text()
            tree = ast.parse(source)
            rel = path.relative_to(ROOT)
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + ([node.module] if isinstance(node, ast.ImportFrom) else [])
                    if "subprocess" in names and path.name not in ("runner.py",):
                        self.fail(f"{rel}: only runner.py may import subprocess")
                if isinstance(node, ast.Call):
                    name = ast.unparse(node.func)
                    self.assertFalse(any(name.startswith(b) for b in banned_calls), f"{rel}: {name}")
                    for kw in node.keywords:
                        if kw.arg == "shell":
                            self.assertEqual(ast.unparse(kw.value), "False", f"{rel}: shell=")
            for word in ("sudo ", "pkexec", "polkit", "dnf ", "rpm-ostree upgrade", "bootc upgrade"):
                for line in source.splitlines():
                    stripped = line.strip()
                    if word in stripped and not stripped.startswith("#") and "`" not in stripped \
                            and "never" not in stripped.lower() and "Doctor" not in stripped:
                        self.fail(f"{rel}: suspicious '{word.strip()}' in: {stripped}")

    def test_network_probe_never_reads_ssids(self):
        source = (PKG / "probes/network.py").read_text()
        for word in ("SSID", "iw ", "iwgetid", "wpa_cli", "nmcli dev wifi"):
            self.assertNotIn(word, source.replace("Wi-Fi network names are never read", ""))


# ── packaging ───────────────────────────────────────────────────────────────
class PackagingTests(unittest.TestCase):
    def test_files_modes_and_desktop_entry(self):
        self.assertTrue(os.access(APP, os.X_OK))
        self.assertTrue(APP.read_text().startswith("#!/usr/bin/python3 -I"))
        self.assertFalse((ROOT / "doctor_core.py").exists(),
                         "v0.1 module replaced by the doctor_nox package")
        desktop = DESKTOP.read_text()
        for needle in ("Exec=doctor-nox", "Icon=io.github.royoshi.doctornox", "Terminal=false",
                       "Type=Application"):
            self.assertIn(needle, desktop)
        json.loads(SCHEMA.read_text())

    def test_doctor_icon_is_the_nox_mark(self):
        import struct
        hicolor = ROOT / "data/icons/hicolor"
        name = "io.github.royoshi.doctornox"
        # Raster PNGs at every size the packages install, drawn by the icon tool (no SVG to install).
        self.assertFalse((hicolor / f"scalable/apps/{name}.svg").exists())
        for size in (16, 22, 24, 32, 48, 64, 128, 256, 512):
            png = (hicolor / f"{size}x{size}/apps/{name}.png").read_bytes()
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            width, height, _depth, colour_type = struct.unpack(">IIBB", png[16:26])
            self.assertEqual((width, height), (size, size))
            self.assertEqual(colour_type, 6, "RGBA so the icon keeps its transparent background")
        # The icon and the window draw the same mark from theme.py's geometry.
        tool = (ROOT / "tools/build_doctor_icon.py").read_text()
        self.assertTrue(os.access(ROOT / "tools/build_doctor_icon.py", os.X_OK))
        for shared in ("mark_for", "ICON_TILE", "ICON_MARGIN", "ICON_RADIUS"):
            self.assertIn(shared, tool)
            self.assertIn(f"themes.{shared}", (PKG / "gui.py").read_text())
        # Independent identity: no distribution logo anywhere in Doctor's icon chain.
        gui = (PKG / "gui.py").read_text()
        self.assertIn("ICON_NAMES = (APP_ID,)", gui)
        self.assertNotIn("FE-XicoHat", gui)


class GuiThreadRuleTests(unittest.TestCase):
    """Regression for the v1.0 image-build crash.

    PySide6 runs a *lambda* receiver on the emitting thread. ScanThread signals are emitted
    from the scan thread, so a lambda receiver ran widget code off the GUI thread (native crash,
    no traceback). Every ScanThread signal must go to a @Slot method with a queued connection.
    """

    def test_scan_thread_signals_use_queued_bound_slots(self):
        tree = ast.parse((PKG / "gui.py").read_text())
        signals = {"progressed", "completed", "failed", "done", "finished", "started"}
        seen = set()
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "connect"
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr in signals
                    and ast.unparse(node.func.value.value) in ("worker", "self.worker")):
                seen.add(node.func.value.attr)
                self.assertNotIsInstance(node.args[0], ast.Lambda, ast.unparse(node))
                self.assertTrue(ast.unparse(node.args[0]).startswith("self._"), ast.unparse(node))
                self.assertEqual(len(node.args), 2, f"explicit connection type required: {ast.unparse(node)}")
        self.assertEqual(seen, {"progressed", "completed", "failed", "done"})
        slots = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in ("_progress", "_completed", "_failed", "_scan_thread_done"):
            decorators = [ast.unparse(d) for d in slots[name].decorator_list]
            self.assertTrue(any(d.startswith("Slot(") for d in decorators), name)
            self.assertIn("_on_gui_thread", ast.unparse(slots[name]), f"{name} must assert GUI-thread affinity")

    def test_self_test_is_diagnostic_and_non_modal(self):
        source = (PKG / "gui.py").read_text()
        for needle in ("faulthandler.enable", "dump_traceback_later", "qInstallMessageHandler", "flush=True",
                       "interactive=False", "every scan thread joined", "engine probe threads exited",
                       "UI updates ran only on the GUI thread", "theme applied to stylesheet and palette",
                       "custom painting raised no errors", "every stylesheet parsed without Qt warnings"):
            self.assertIn(needle, source)

    def test_gui_is_themed_from_theme_py_only(self):
        source = (PKG / "gui.py").read_text()
        for neon in ("#00ff00", "#ff00ff", "#000000\"", "vm.COLORS", "SEVERITY_COLOR"):
            self.assertNotIn(neon, source.replace('(role.Shadow, "#000000")', ""))
        tree = ast.parse(source)
        delegates = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name.endswith("Delegate")]
        self.assertEqual({d.name for d in delegates}, {"_PillDelegate", "StatusDelegate", "NavDelegate"})
        for d in delegates:
            for fn in (n for n in d.body if isinstance(n, ast.FunctionDef) and n.name == "paint"):
                self.assertTrue(any(isinstance(n, ast.Try) for n in ast.walk(fn)),
                                f"{d.name}.paint must record errors instead of crashing")

    def test_screenshot_mode_is_hidden_and_never_scans_the_host(self):
        source = CLI.read_text()
        self.assertIn('mode.add_argument("--gui-screenshot", metavar="DIR", help=argparse.SUPPRESS)', source)
        tree = ast.parse((PKG / "gui.py").read_text())
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in ("screenshots", "_demo_report"):
            body = ast.unparse(funcs[name])
            for banned in ("engine.scan", "Context(", "start_scan", "runner.", "load_all", "Probe("):
                self.assertNotIn(banned, body, f"{name} must use synthetic data only")


# ── Qt GUI (runs where PySide6 is installed, e.g. the CI containers) ──
def _have_pyside6():
    return importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(_have_pyside6(), "PySide6 not installed here; CI runs these")
class GuiTests(unittest.TestCase):
    def run_app(self, flag):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        return subprocess.run([sys.executable, str(APP), flag], capture_output=True, text=True, timeout=120, env=env)

    def test_offscreen_smoke(self):
        res = self.run_app("--gui-smoke-test")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def test_offscreen_self_test_filter_select_rescan_cancel_close(self):
        res = self.run_app("--gui-self-test")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("self-test: OK", res.stdout)


if __name__ == "__main__":
    unittest.main()
