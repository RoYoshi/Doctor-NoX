"""Doctor NoX core: runner safety, engine, reports/redaction, CLI, view model, GUI, packaging.

Nothing here runs Doctor's host probes or any mutating command. Runner tests
execute only a temporary Python helper script through a test-only allowlist entry.
"""
import ast
import contextlib
import importlib.machinery
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

from doctor_nox import engine, model, probes, report, runner, theme, viewmodel as vm  # noqa: E402
from doctor_nox.model import Finding  # noqa: E402
from doctor_nox.probes import Probe  # noqa: E402

PKG = ROOT / "doctor_nox"
APP = ROOT / "bin/doctor-nox"
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


def load_cli():
    loader = importlib.machinery.SourceFileLoader("doctor_nox_cli", str(APP))
    spec = importlib.util.spec_from_loader("doctor_nox_cli", loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def fixture_report(**kw):
    findings = [
        Finding("system.bootc.booted", "system", "Booted deployment", "pass", "Booted from ghcr.io/royoshi/fe-xicoos:latest",
                evidence=["digest sha256:" + "a" * 64]),
        Finding("updates.legacy_timer", "updates", "Legacy auto-reboot timer", "error", "Legacy timer ENABLED"),
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
    host = {"name": "FE-Xico", "edition": "NVIDIA", "version": "44.20261030.0", "kernel": "6.17.4-300.fc44.x86_64",
            "image_variant": "nvidia-open"}
    return model.build_report(findings, host, started=now, finished=now, complete=kw.get("complete", True))


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
                           ("systemctl", ("show", "fe-xicoos-upgrade.service", "--property=ExecStart"))):
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
        self.assertIn("ghcr.io/royoshi/fe-xicoos:latest", report.to_json(shared))
        self.assertIn("alice", report.to_json(rep), "the full local report is not modified")

    def test_redaction_keeps_ordinary_text(self):
        terms = report.identity_terms("alice", "fe-xico")
        for text in ("09:30:15 on 01:00.0", "fe-xicoos-auto-update.timer", "/usr/bin/doctor-nox",
                     "nameserver 127.0.0.53", "No token found", "kernel 6.17.4-300.fc44.x86_64"):
            self.assertEqual(report.redact_text(text, terms), text)
        self.assertEqual(report.identity_terms("root", "localhost"), [])

    def test_full_report_labelled_and_summary_lists_problems_only(self):
        rep = fixture_report(complete=False)
        text = report.format_text(rep)
        self.assertIn("FULL LOCAL REPORT", text)
        self.assertIn("INCOMPLETE", text)
        self.assertIn("No changes were made", text)
        summary = report.format_summary(rep)
        self.assertIn("Legacy auto-reboot timer", summary)
        self.assertNotIn("Booted deployment", summary)

    def test_export_permissions_and_atomic_write(self):
        rep = fixture_report()
        with tempfile.TemporaryDirectory() as tmp:
            full = Path(tmp) / "full.json"
            report.export(full, rep, redacted=False, fmt="json")
            self.assertEqual(full.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads(full.read_text())["findings"][0]["id"], "system.bootc.booted")
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
        self.cli = load_cli()

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
        self.assertIn("system.bootc", out)

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
                         ["updates.legacy_timer", "logs.boot"])
        self.assertEqual(len(vm.visible(rep, query="enp5s0 connected")), 1)
        self.assertEqual(vm.visible(rep, "network", severities=frozenset({"error"})), [])
        self.assertIn("No checks match", vm.empty_state(rep, False, "network", "", frozenset({"error"})))
        self.assertIn("Press Scan", vm.empty_state(None, False, "overview", "", frozenset(model.SEVERITIES)))

    def test_detail_html_escapes_and_explains(self):
        f = Finding("logs.boot", "logs", "Boot <b>", "unknown", "x <script>alert(1)</script>",
                    evidence=["<img src=x onerror=alert(1)>"], reason="denied", guidance=["run `x`"]).to_dict()
        html = vm.detail_html(f)
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img", html)
        self.assertIn("never runs these steps", html)
        self.assertIn("Unknown", html)  # status is always spelled out, not colour alone
        self.assertIn("not a score", vm.overview_html(fixture_report()))

    def test_sidebar_badges_count_problems_and_mark_healthy_groups(self):
        zero = {sev: 0 for sev in model.SEVERITIES}
        self.assertEqual(vm.category_badge(zero, 0), ("", None))
        self.assertEqual(vm.category_badge(dict(zero, error=1, warn=2, unknown=1, info=1, pass_=0), 5), ("4", "error"))
        self.assertEqual(vm.category_badge(dict(zero, warn=1, unknown=2), 3), ("3", "warn"))
        self.assertEqual(vm.category_badge(dict(zero, unknown=1, info=1), 2), ("1", "unknown"))
        self.assertEqual(vm.category_badge(dict(zero, info=2, **{"pass": 3}), 5), ("OK", "pass"))
        self.assertEqual(vm.category_accessible("Logs", dict(zero, warn=2), 4), "Logs: 4 checks, 2 warnings")
        self.assertEqual(vm.category_accessible("Logs", dict(zero, **{"pass": 4}), 4), "Logs: 4 checks, all verified")

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
        self.assertIn("Scan incomplete", vm.overview_html(fixture_report(complete=False)))
        self.assertNotIn("<script>", vm.error_html("boom <script>"))
        self.assertIn("font-family:'Hack'", vm.detail_html(finding, theme.DARK_THEME, "Hack"))


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
        self.assertNotEqual(dark.window, "#000000", "no OLED black: the window is a neutral dark grey")
        for t in (light, dark):
            self.assertNotIn(t.accent, {s.fg for s in t.status.values()})
            self.assertEqual(len({s.fg for s in t.status.values()}), 5, "every status has its own colour")
            for value in vars(t).values():
                if isinstance(value, str):
                    self.assertNotIn(value.lower(), ("#00ff00", "#ff00ff"))

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

    def test_doctor_icon_is_its_own_raster_badge(self):
        import hashlib
        import re
        import struct
        hicolor = ROOT / "data/icons/hicolor"
        name = "io.github.royoshi.doctornox"
        # Raster only: RoYoshi's badge is never redrawn or wrapped in an SVG.
        self.assertFalse((hicolor / f"scalable/apps/{name}.svg").exists())
        for size in (16, 22, 24, 32, 48, 64, 128, 256, 512):
            png = (hicolor / f"{size}x{size}/apps/{name}.png").read_bytes()
            self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
            width, height, _depth, colour_type = struct.unpack(">IIBB", png[16:26])
            self.assertEqual((width, height), (size, size))
            self.assertEqual(colour_type, 6, "RGBA so the icon keeps its transparent background")
        source = ROOT / "tools/doctor-nox-icon-source.png"
        tool = (ROOT / "tools/build_doctor_icon.py").read_text()
        pinned = re.search(r'^SOURCE_SHA256 = "([0-9a-f]{64})"$', tool, re.M).group(1)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), pinned, "source art changed")
        self.assertTrue(os.access(ROOT / "tools/build_doctor_icon.py", os.X_OK))
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
        cli = APP.read_text()
        self.assertIn('mode.add_argument("--gui-screenshot", metavar="DIR", help=argparse.SUPPRESS)', cli)
        tree = ast.parse((PKG / "gui.py").read_text())
        funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        for name in ("screenshots", "_demo_report"):
            body = ast.unparse(funcs[name])
            for banned in ("engine.scan", "Context(", "start_scan", "runner.", "load_all", "Probe("):
                self.assertNotIn(banned, body, f"{name} must use synthetic data only")


# ── Qt GUI (runs where Fedora's python3-pyside6 is installed, e.g. the image build) ──
def _have_pyside6():
    return importlib.util.find_spec("PySide6") is not None


@unittest.skipUnless(_have_pyside6(), "PySide6 not installed here; the image build runs --gui-self-test instead")
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
