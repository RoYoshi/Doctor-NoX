"""The Flatpak build: running Doctor's checks on the host through flatpak-spawn.

Nothing here starts flatpak-spawn: the host run is answered by FakeRunner,
which enforces the real host-command allowlist, or captured before execution.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from doctor_testlib import MISSING, ROOT, TIMEOUT, FakeRunner, exit_, ok  # noqa: E402

from doctor_nox import cli, engine, flatpak, hostrun, model, runner  # noqa: E402
from doctor_nox.context import Context  # noqa: E402
from doctor_nox.model import Finding  # noqa: E402
from doctor_nox.probes import Probe  # noqa: E402

APP = "/var/lib/flatpak/app/io.github.royoshi.doctornox/x86_64/master/0a1b2c/files"
PACKAGE = Path("/app/share/doctor-nox/doctor_nox")
SCRIPT = APP + "/share/doctor-nox/doctor_nox/hostrun.py"
INFO = ("[Application]\nname=io.github.royoshi.doctornox\nruntime=runtime/org.kde.Platform/x86_64/6.10\n\n"
        f"[Instance]\ninstance-id=1234567\napp-path={APP}\nbranch=master\nflatpak-version=1.16.1\n")


def host_report():
    import datetime
    now = datetime.datetime(2026, 11, 12, 22, 0, tzinfo=datetime.timezone.utc)
    host = {"name": "Fedora Linux 44 (KDE Plasma Desktop Edition)", "id": "fedora", "version": "44",
            "family": "fedora", "kernel": "6.17.4-300.fc44.x86_64", "init": "systemd", "security": "selinux",
            "package_managers": ["dnf", "rpm", "flatpak"]}
    findings = [Finding("system.identity", "system", "Operating system", "info", "Fedora Linux 44"),
                Finding("updates.reboot", "updates", "Pending restart", "pass", "No restart needed")]
    return model.build_report(findings, host, started=now, finished=now, complete=True)


class AppPathTests(unittest.TestCase):
    def info(self, text):
        tmp = tempfile.NamedTemporaryFile("w", delete=False, suffix=".flatpak-info")
        tmp.write(text)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        return tmp.name

    def test_only_doctors_own_flatpak(self):
        self.assertEqual(flatpak.sandbox_app_path(self.info(INFO), PACKAGE), APP)
        self.assertEqual(flatpak.sandbox_app_path(self.info(INFO), Path("/usr/share/doctor-nox/doctor_nox")), "",
                         "a native install is never treated as the Flatpak")
        self.assertEqual(flatpak.sandbox_app_path("/nonexistent/.flatpak-info", PACKAGE), "")
        for bad in (INFO.replace("io.github.royoshi.doctornox", "com.example.Other"),
                    INFO.replace(f"app-path={APP}", "app-path=relative/files"),
                    INFO.replace(f"app-path={APP}", "app-path=/var/../etc"),
                    INFO.replace(f"app-path={APP}", "app-path=/var/lib/\x07flatpak"),
                    "[Application\nbroken", ""):
            with self.subTest(info=bad[:40]):
                self.assertEqual(flatpak.sandbox_app_path(self.info(bad), PACKAGE), "")

    def test_this_checkout_is_not_the_flatpak(self):
        self.assertEqual(flatpak.sandbox_app_path(), "")

    def test_host_script_and_arguments(self):
        self.assertEqual(flatpak.host_script(APP, PACKAGE), SCRIPT)
        self.assertEqual(flatpak.host_script(APP, Path("/home/x/doctor_nox")), SCRIPT, "the manifest's layout")
        self.assertEqual(flatpak.host_args(60, None), ["--json", "--deadline", "60"])
        args = flatpak.host_args(42.5, {"system.image", "hardware"})
        self.assertEqual(args, ["--json", "--deadline", "42.5", "--only", "hardware", "--only", "system.image"])
        runner.validate_host_doctor(SCRIPT, args)
        runner.validate_host_doctor(SCRIPT, ["--list-checks"])


class HostCommandTests(unittest.TestCase):
    def test_only_read_only_report_modes_are_allowed(self):
        for script, args in ((SCRIPT, ["--text"]), (SCRIPT, ["--json"]), (SCRIPT, ["--gui-self-test"]),
                             (SCRIPT, ["--json", "--deadline", "60", "--redact"]),
                             (SCRIPT, ["--json", "--deadline", "60", "--only"]),
                             (SCRIPT, ["--json", "--deadline", "60", "--only", "x; rm -rf /"]),
                             (SCRIPT, ["--json", "--deadline", "1e9"]), (SCRIPT, ["--list-checks", "--json"]),
                             ("/tmp/evil.py", ["--list-checks"]), (SCRIPT.replace("hostrun", "other"), ["--list-checks"]),
                             (APP + "/../doctor_nox/hostrun.py", ["--list-checks"]), ("doctor_nox/hostrun.py", ["--list-checks"]),
                             (SCRIPT + "\n", ["--list-checks"])):
            with self.subTest(script=script[-30:], args=args):
                with self.assertRaises(runner.CommandNotAllowed):
                    runner.validate_host_doctor(script, args)

    def test_exact_command_line_and_no_changing_verbs(self):
        r = runner.Runner()
        captured = []
        with mock.patch.object(r, "_execute", lambda argv, display, spec, timeout, exe: captured.append(
                (argv, exe, timeout)) or runner.CmdResult(display, "ok", 0, "", "")):
            r.run_host_doctor(SCRIPT, ["--list-checks"], timeout=60,
                              resolve=lambda paths: ("/usr/bin/flatpak-spawn", "ok"))
        (argv, exe, timeout), = captured
        self.assertEqual(argv, ("/usr/bin/flatpak-spawn", "--host", "--watch-bus", "/usr/bin/python3", "-I", "-B",
                                SCRIPT, "--list-checks"))
        self.assertEqual((exe, timeout), ("/usr/bin/flatpak-spawn", 60))
        self.assertFalse(set(argv) & runner.FORBIDDEN_VERBS)

    def test_untrusted_or_missing_flatpak_spawn_is_never_run(self):
        with tempfile.NamedTemporaryFile(dir="/tmp", delete=False) as tmp:
            os.chmod(tmp.name, 0o755)
        self.addCleanup(os.unlink, tmp.name)
        self.assertEqual(runner.sandbox_trusted_executable((tmp.name,)), (None, "untrusted"))
        self.assertEqual(runner.sandbox_trusted_executable(("/usr/bin/definitely-not-flatpak-spawn",)),
                         (None, "missing"))
        with mock.patch("subprocess.Popen") as popen:
            res = runner.Runner().run_host_doctor(SCRIPT, ["--list-checks"], timeout=60,
                                                  resolve=lambda paths: (None, "untrusted"))
        popen.assert_not_called()
        self.assertEqual(res.status, "untrusted")


class ScanOnHostTests(unittest.TestCase):
    def ctx(self, answer):
        return Context(root=Path("/"), runner=FakeRunner({("host-doctor",): answer}), user="alice", home="/home/alice",
                       hostname="ws", kernel="6.17.4-300.fc44.x86_64")

    def test_the_hosts_report_is_shown(self):
        seen = []
        ctx = self.ctx(ok(json.dumps(host_report())))
        rep = flatpak.scan_on_host(ctx, APP, progress=lambda d, t, title: seen.append((d, t)), deadline=60)
        self.assertEqual(rep["host"]["family"], "fedora")
        self.assertEqual([f["id"] for f in rep["findings"]], ["system.identity", "updates.reboot"])
        self.assertEqual(seen, [(0, 1), (1, 1)])
        self.assertTrue(ctx.cancel.is_set())
        self.assertEqual(ctx.runner.calls, [("host-doctor", "--json", "--deadline", "60")])
        self.assertEqual(ctx.runner.scripts, [SCRIPT])
        errors = host_report()
        errors["findings"][1]["severity"] = "error"
        rep = flatpak.scan_on_host(self.ctx(exit_(1, json.dumps(errors))), APP)
        self.assertEqual(rep["findings"][1]["severity"], "error", "exit 1 with a report means Error findings")

    def test_every_failure_is_one_explained_unknown(self):
        cases = (
            (MISSING, "cannot reach this computer", True),
            (TIMEOUT, "timed out", False),
            (("cancelled", None, "", ""), "scan cancelled", False),
            (exit_(64, stderr="Doctor NoX needs Python 3.10 or later on this computer; /usr/bin/python3 is 3.9"),
             "Python 3.10", True),
            (exit_(1, stderr="Failed to start command: Failed to execute child process “/usr/bin/python3” "
                             "(No such file or directory)"), "Python 3.10", True),
            (exit_(1, stderr="Portal call failed: org.freedesktop.DBus.Error.ServiceUnknown: The name "
                             "org.freedesktop.Flatpak was not provided by any .service files"), "outside the sandbox is off",
             True),
            (ok("not json"), "failed", True),
            (ok(json.dumps({"schema": "doctor-nox/report", "schema_version": 2})), "failed", True),
            (exit_(3, stderr="Doctor NoX failed: RuntimeError: boom"), "failed", True),
        )
        for answer, words, complete in cases:
            with self.subTest(answer=answer[0], words=words):
                rep = flatpak.scan_on_host(self.ctx(answer), APP)
                self.assertEqual(model.validate_report(rep), [])
                (f,) = rep["findings"]
                self.assertEqual((f["id"], f["severity"]), ("system.host_access", "unknown"))
                self.assertIn(words, f["summary"])
                self.assertEqual(rep["complete"], complete)
        permission = flatpak.scan_on_host(self.ctx(cases[5][0]), APP)["findings"][0]
        self.assertTrue(any("flatpak override --user --talk-name=org.freedesktop.Flatpak" in g
                            for g in permission["guidance"]))

    def test_list_checks_from_the_host(self):
        code, out, err = flatpak.list_checks_on_host(self.ctx(ok("# Fedora Linux 44 · Fedora / RHEL family\nx\n")), APP)
        self.assertEqual((code, err), (0, ""))
        self.assertTrue(out.startswith("# Fedora"))
        code, out, err = flatpak.list_checks_on_host(self.ctx(exit_(1, stderr="org.freedesktop.Flatpak ServiceUnknown")),
                                                     APP)
        self.assertEqual((code, out), (3, ""))
        self.assertIn("outside the sandbox is off", err)


class DelegationTests(unittest.TestCase):
    def test_engine_runs_on_the_host_only_inside_the_flatpak(self):
        ctx = Context(root=Path("/"), runner=FakeRunner({("host-doctor",): ok(json.dumps(host_report()))}),
                      user="alice", home="/home/alice", hostname="ws")
        with mock.patch.object(flatpak, "sandbox_app_path", return_value=APP):
            rep = engine.scan(ctx, deadline=30)
        self.assertEqual(rep["host"]["family"], "fedora")
        self.assertEqual(ctx.runner.calls, [("host-doctor", "--json", "--deadline", "30")])
        probe = Probe("system.x", "system", "X", lambda c: Finding("system.x", "system", "X", "pass", "ok"))
        with mock.patch.object(flatpak, "sandbox_app_path", return_value=APP), \
                mock.patch.object(flatpak, "scan_on_host") as host:
            engine.scan(Context(root=Path("/"), runner=FakeRunner()), probes=[probe])  # the GUI self-test's probes
            with tempfile.TemporaryDirectory() as tmp:
                engine.scan(Context(root=Path(tmp), runner=FakeRunner()), deadline=10)  # fixture roots
        host.assert_not_called()

    def test_list_checks_runs_on_the_host_inside_the_flatpak(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(flatpak, "sandbox_app_path", return_value=APP), \
                mock.patch.object(flatpak, "list_checks_on_host", return_value=(0, "# Arch Linux · Arch family\n", "")), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(["--list-checks"])
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), "# Arch Linux · Arch family\n")


class HostRunTests(unittest.TestCase):
    def test_old_python_gets_a_message_not_a_traceback(self):
        err = io.StringIO()
        with mock.patch.object(sys, "version_info", (3, 9, 18, "final", 0)), contextlib.redirect_stderr(err):
            self.assertEqual(hostrun.main(), flatpak.TOO_OLD)
        self.assertIn("Python 3.10 or later", err.getvalue())
        self.assertEqual(hostrun.TOO_OLD, flatpak.TOO_OLD)

    def test_runs_the_command_line_from_its_own_folder(self):
        res = subprocess.run([sys.executable, "-I", "-B", str(ROOT / "doctor_nox/hostrun.py"), "--version"],
                             capture_output=True, text=True, timeout=60, cwd="/")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(res.stdout.strip(), f"Doctor NoX {model.VERSION}")

    def test_stays_parseable_by_old_pythons(self):
        import ast
        ast.parse((ROOT / "doctor_nox/hostrun.py").read_text(), feature_version=(3, 6))
        self.assertNotIn("from __future__", (ROOT / "doctor_nox/hostrun.py").read_text())


if __name__ == "__main__":
    unittest.main()
