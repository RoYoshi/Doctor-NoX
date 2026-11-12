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
        return self._answer((name, *args))

    def run_host_doctor(self, script, args, timeout, resolve=None):
        """The Flatpak build's host run, answered from the key ("host-doctor", *args)."""
        runner_mod.validate_host_doctor(script, args)
        self.scripts = getattr(self, "scripts", []) + [script]
        return self._answer(("host-doctor", *args))

    def _answer(self, key):
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


# ── distributions ────────────────────────────────────────────────────────────
OS_RELEASE = {
    "fedora": 'NAME="Fedora Linux"\nVERSION_ID=44\nID=fedora\nVARIANT_ID=kde\n'
              'PRETTY_NAME="Fedora Linux 44 (KDE Plasma Desktop Edition)"\n',
    "silverblue": 'NAME="Fedora Linux"\nVERSION_ID=44\nID=fedora\nVARIANT_ID=silverblue\n'
                  'PRETTY_NAME="Fedora Linux 44.20261030.0 (Silverblue)"\n',
    "bazzite": 'NAME="Bazzite"\nVERSION_ID=44\nID=bazzite\nID_LIKE="fedora"\nPRETTY_NAME="Bazzite 44 (FROM Fedora Kinoite)"\n',
    "fe-xicoos": 'NAME="FE-Xico"\nVERSION_ID="44"\nID=fe-xicoos\nID_LIKE="fedora"\nPRETTY_NAME="FE-XicoOS 44"\n',
    "almalinux": 'NAME="AlmaLinux"\nVERSION_ID="10.0"\nID="almalinux"\nID_LIKE="rhel centos fedora"\n'
                 'PRETTY_NAME="AlmaLinux 10.0 (Purple Lion)"\n',
    "ubuntu": 'NAME="Ubuntu"\nVERSION_ID="24.04"\nID=ubuntu\nID_LIKE=debian\nPRETTY_NAME="Ubuntu 24.04.3 LTS"\n',
    "debian": 'NAME="Debian GNU/Linux"\nVERSION_ID="13"\nID=debian\nPRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n',
    "mint": 'NAME="Linux Mint"\nVERSION_ID="22.2"\nID=linuxmint\nID_LIKE="ubuntu debian"\nPRETTY_NAME="Linux Mint 22.2"\n',
    "pop": 'NAME="Pop!_OS"\nVERSION_ID="24.04"\nID=pop\nID_LIKE="ubuntu debian"\nPRETTY_NAME="Pop!_OS 24.04 LTS"\n',
    "arch": 'NAME="Arch Linux"\nID=arch\nBUILD_ID=rolling\nPRETTY_NAME="Arch Linux"\n',
    "cachyos": 'NAME="CachyOS Linux"\nID=cachyos\nID_LIKE=arch\nBUILD_ID=rolling\nPRETTY_NAME="CachyOS"\n',
    "steamos": 'NAME="SteamOS"\nVERSION_ID=3.7.13\nID=steamos\nID_LIKE=arch\nPRETTY_NAME="SteamOS"\n',
    "tumbleweed": 'NAME="openSUSE Tumbleweed"\nVERSION_ID="20261101"\nID="opensuse-tumbleweed"\n'
                  'ID_LIKE="opensuse suse"\nPRETTY_NAME="openSUSE Tumbleweed"\n',
    "leap": 'NAME="openSUSE Leap"\nVERSION_ID="16.0"\nID="opensuse-leap"\nID_LIKE="suse opensuse"\n'
            'PRETTY_NAME="openSUSE Leap 16.0"\n',
    "aeon": 'NAME="openSUSE Aeon"\nVERSION_ID="20261101"\nID="opensuse-aeon"\n'
            'ID_LIKE="suse opensuse opensuse-tumbleweed microos"\nPRETTY_NAME="openSUSE Aeon"\n',
    "nixos": 'NAME=NixOS\nVERSION_ID="25.05"\nID=nixos\nPRETTY_NAME="NixOS 25.05 (Warbler)"\n',
    "alpine": 'NAME="Alpine Linux"\nVERSION_ID=3.22.2\nID=alpine\nPRETTY_NAME="Alpine Linux v3.22"\n',
}
#: name: (os-release key, systemd, OSTree, package tools, kernel)
PRESETS = {
    "fedora": ("fedora", True, False, ("dnf", "rpm", "flatpak"), KERNEL),
    "silverblue": ("silverblue", True, True, ("rpm", "rpm-ostree", "bootc", "flatpak"), KERNEL),
    "bazzite": ("bazzite", True, True, ("rpm", "rpm-ostree", "bootc", "flatpak"), KERNEL),
    "fe-xicoos": ("fe-xicoos", True, True, ("rpm", "rpm-ostree", "bootc", "flatpak"), KERNEL),
    "almalinux": ("almalinux", True, False, ("dnf", "rpm"), "6.12.0-55.9.1.el10_0.x86_64"),
    "ubuntu": ("ubuntu", True, False, ("apt", "dpkg", "snap"), "6.8.0-49-generic"),
    "debian": ("debian", True, False, ("apt", "dpkg"), "6.12.48+deb13-amd64"),
    "mint": ("mint", True, False, ("apt", "dpkg", "flatpak"), "6.8.0-49-generic"),
    "pop": ("pop", True, False, ("apt", "dpkg", "flatpak"), "6.16.3-76061603-generic"),
    "arch": ("arch", True, False, ("pacman",), "6.17.4-arch1-1"),
    "cachyos": ("cachyos", True, False, ("pacman", "flatpak"), "6.17.4-2-cachyos"),
    "steamos": ("steamos", True, False, ("pacman", "flatpak"), "6.11.11-valve19-1-neptune-611"),
    "tumbleweed": ("tumbleweed", True, False, ("zypper", "rpm", "flatpak"), "6.17.5-1-default"),
    "leap": ("leap", True, False, ("zypper", "rpm"), "6.12.0-160000.5-default"),
    "aeon": ("aeon", True, False, ("zypper", "rpm", "flatpak"), "6.17.5-1-default"),
    "nixos": ("nixos", True, False, ("nix",), "6.12.55"),
    "alpine": ("alpine", False, False, ("apk",), "6.12.57-0-lts"),
}


def install_distro(tree, name):
    """Write the os-release, init marker and package tools that make `tree` look like `name`.

    Returns the kernel release that distribution would run.
    """
    from doctor_nox import distro
    key, systemd, ostree, tools, kernel = PRESETS[name]
    tree.write("/usr/lib/os-release", OS_RELEASE[key])
    if systemd:
        tree.mkdir("/run/systemd/system")
    if ostree:
        tree.write("/run/ostree-booted", "")
    paths = dict(distro._TOOLS)
    for tool in tools:
        first = paths[tool][0]
        if tool == "nix":
            tree.mkdir(first)
        else:
            tree.write(first, "", 0o755)
    return kernel


# ── bootc v1 fixtures (shape of `bootc status --json --format-version=1`) ──────
IMAGE = "ghcr.io/ublue-os/bazzite:stable"


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


# ── rpm-ostree fixtures (shape of `rpm-ostree status --json`) ─────────────────
OSTREE_REF = "ostree-image-signed:docker://ghcr.io/ublue-os/bazzite:stable"


def ostree_entry(version, checksum="a", *, booted=False, staged=False, pinned=False, ref=OSTREE_REF,
                 layered=(), unlocked="none", timestamp=1793404800):
    return {"id": f"default-{checksum * 8}.0", "osname": "default", "serial": 0, "checksum": checksum * 64,
            "version": version, "timestamp": timestamp, "booted": booted, "staged": staged, "pinned": pinned,
            "unlocked": unlocked, "container-image-reference": ref,
            "container-image-reference-digest": "sha256:" + checksum * 64,
            "requested-packages": list(layered), "requested-local-packages": [],
            "requested-base-removals": [], "requested-base-local-replacements": []}


def ostree_status(*entries):
    return {"deployments": list(entries), "transaction": None, "cached-update": None, "update-driver": None}
