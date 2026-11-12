"""Shared fixtures for Doctor NoX tests (not a test module itself).

FakeRunner validates every call against the real allowlist before answering,
so probe tests also prove that probes only ask for permitted commands. No
test executes a host command through Doctor's probes.
"""
from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT  # the doctor_nox package lives at the repository root
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

from doctor_nox import runner as runner_mod  # noqa: E402
from doctor_nox.context import Context  # noqa: E402
from doctor_nox.runner import CmdResult  # noqa: E402

KERNEL = "6.17.4-300.fc44.x86_64"
NOW = datetime.datetime(2026, 10, 30, 20, 0, tzinfo=datetime.timezone.utc)


def ok(stdout="", stderr=""):
    return ("ok", 0, stdout, stderr)


def exit_(code=1, stdout="", stderr=""):
    return ("exit", code, stdout, stderr)


MISSING = ("missing", None, "", "")
TIMEOUT = ("timeout", None, "", "")


class FakeRunner:
    """Answers allowlisted commands from a table; unknown-but-allowed calls are 'missing'."""

    def __init__(self, answers=None, default=MISSING):
        self.answers = dict(answers or {})
        self.default = default
        self.calls = []
        self.cancel = threading.Event()

    def run(self, name, *args, timeout=None):
        runner_mod.validate(name, args)  # raises CommandNotAllowed exactly like the real runner
        key = (name, *args)
        self.calls.append(key)
        answer = self.answers.get(key)
        if answer is None:
            for k, v in self.answers.items():  # prefix match: ("systemctl", "is-enabled") etc.
                if key[:len(k)] == k:
                    answer = v
                    break
        status, code, out, err = answer if answer is not None else self.default
        return CmdResult(key, status, code, out, err)


class Tree:
    """A throwaway filesystem root for one test."""

    def __init__(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="doctor-fixture-")
        self.root = Path(self._tmp.name)

    def cleanup(self):
        self._tmp.cleanup()

    def p(self, absolute):
        return self.root / absolute.lstrip("/")

    def write(self, absolute, text="", mode=None):
        path = self.p(absolute)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if mode is not None:
            path.chmod(mode)
        return path

    def json(self, absolute, data, mode=0o644):
        return self.write(absolute, json.dumps(data), mode)

    def mkdir(self, absolute, mode=None):
        path = self.p(absolute)
        path.mkdir(parents=True, exist_ok=True)
        if mode is not None:
            path.chmod(mode)
        return path

    def link(self, absolute, target):
        path = self.p(absolute)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(target, path)
        return path

    def context(self, runner=None, env=None, **kw):
        kw.setdefault("kernel", KERNEL)
        kw.setdefault("user", "alice")
        kw.setdefault("home", "/home/alice")
        kw.setdefault("hostname", "fe-royo")
        kw.setdefault("now", NOW)
        kw.setdefault("trusted_uid", os.getuid())  # fixture files stand in for root-owned files
        return Context(root=self.root, env=env or {}, runner=runner or FakeRunner(), **kw)


def fe_xico_os_release(tree, variant="NVIDIA", version="44.20261030.0"):
    tree.write("/usr/lib/os-release",
               f'NAME="FE-Xico"\nVERSION_ID="44"\nFE_XICO_VERSION="{version}"\n'
               f'FE_XICO_VARIANT="{variant}"\nVARIANT="{variant}"\n')


# ── bootc v1 fixtures (shape of `bootc status --json --format-version=1`) ──────
IMAGE = "ghcr.io/royoshi/fe-xicoos:latest"


def bootc_entry(version, digest_char="a", *, ref=IMAGE, pinned=False, download_only=None, incompatible=False):
    entry = {
        "image": {"image": {"image": ref, "transport": "registry"},
                  "version": version, "timestamp": "2026-10-28T04:00:00Z",
                  "imageDigest": "sha256:" + digest_char * 64},
        "cachedUpdate": None, "incompatible": incompatible, "pinned": pinned,
        "store": "ostreeContainer",
        "ostree": {"checksum": digest_char * 64, "deploySerial": 0, "stateroot": "default"},
    }
    if download_only is not None:
        entry["downloadOnly"] = download_only
    return entry


def bootc_host(*, staged=None, rollback=True, queued=False, spec_ref=IMAGE, others=None, booted=None):
    return {
        "apiVersion": "org.containers.bootc/v1", "kind": "BootcHost", "metadata": {"name": "host"},
        "spec": {"image": {"image": spec_ref, "transport": "registry"},
                 "bootOrder": "rollback" if queued else "default"},
        "status": {"staged": staged, "booted": booted or bootc_entry("44.20261030.0", "b"),
                   "rollback": bootc_entry("44.20261023.0", "c") if rollback else None,
                   "rollbackQueued": queued, "type": "bootcHost",
                   "otherDeployments": others or []},
    }
