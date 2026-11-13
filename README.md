<p align="center">
  <img src="https://raw.githubusercontent.com/RoYoshi/Doctor-NoX/main/data/icons/hicolor/256x256/apps/io.github.royoshi.doctornox.png" width="128" height="128" alt="Doctor NoX icon: a crescent moon cradling a medical cross, on an indigo night tile">
</p>

<h1 align="center">Doctor NoX</h1>

<p align="center">
  <strong>A read-only health check for your Linux desktop.</strong><br>
  It finds what is wrong with updates, security, drivers, storage, the network and logs,<br>
  then tells you what each finding means and what you can do about it.
</p>

<p align="center">
  <a href="https://github.com/RoYoshi/Doctor-NoX/actions/workflows/ci.yml"><img alt="CI" src="https://github.com/RoYoshi/Doctor-NoX/actions/workflows/ci.yml/badge.svg?branch=main"></a>
  <a href="https://github.com/RoYoshi/Doctor-NoX/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/RoYoshi/Doctor-NoX?label=release&color=4a3cb5"></a>
  <a href="https://github.com/RoYoshi/Doctor-NoX/blob/main/LICENSE"><img alt="License: Apache-2.0" src="https://img.shields.io/badge/license-Apache--2.0-4a3cb5"></a>
</p>

<p align="center">
  <a href="#install">Install</a>&emsp;<a href="#using-it">Using it</a>&emsp;<a href="#how-it-stays-safe">How it stays safe</a>&emsp;<a href="https://github.com/RoYoshi/Doctor-NoX/blob/main/docs/DOCTOR_NOX.md">Documentation</a>
</p>

---

> **Doctor only looks.** It never needs root, never changes anything, never
> makes a network request, and never uploads anything.

## Which Linux it knows

It knows these families, and runs its general checks on any other Linux:

| Family | Including | Specific checks |
|---|---|---|
| **Fedora** | Fedora Workstation and spins, RHEL, AlmaLinux, Rocky; image-based Fedora Atomic (Silverblue, Kinoite…), bootc, Universal Blue (Bazzite, Bluefin, Aurora), FE-XicoOS | RPM database, newer kernel installed, `.rpmnew` files, dnf-automatic; on image-based systems the booted, staged, rollback and pinned deployments, layered packages and automatic image updates |
| **Debian** | Debian, Ubuntu, Linux Mint, Pop!_OS, elementary… | `/run/reboot-required`, half-installed packages (`dpkg --audit`), `.dpkg-dist` files, unattended-upgrades, Snap |
| **Arch** | Arch, EndeavourOS, CachyOS, Manjaro, SteamOS… | modules removed by a kernel update, stale `db.lck`, orphaned packages, `.pacnew` files, the last `pacman -Syu` |
| **openSUSE** | Tumbleweed, Leap, Slowroll; Aeon, Kalpa and MicroOS | `/run/reboot-needed`, RPM database, a running zypper, `.rpmnew` files, transactional-update |

Checks that don't apply to a system (bootc on Arch, Snap where it isn't
installed) don't run. They are listed as **not applicable** instead of
showing up as Unknown.

## What it checks

| Group | Examples |
|---|---|
| System | distribution, family and kernel; a stale `/etc/os-release`; on OSTree systems the booted, staged, rollback and pinned deployments, the update source and layered packages |
| Updates & Packages | a pending restart, broken or locked package databases, config files left by updates, when the system was last updated, automatic updates |
| Security & Services | SELinux or AppArmor, the firewall (firewalld, ufw), failed services |
| Hardware & Gaming | GPU driver (NVIDIA Open or proprietary, Nouveau/NVK, AMD, Intel), NVIDIA kernel/user-space mismatch, PipeWire or PulseAudio, Xbox-protocol controllers, temperatures |
| Storage | read-only filesystems (what is normal depends on the kind of system), where your home lives, free space including `/boot` |
| Apps | Flatpak remotes and apps, Snap, whether installed apps can appear in your menu |
| Network | links, routes, DNS, NetworkManager (local state only, no packets sent) |
| Logs | critical and error entries in this boot's journal |

`doctor-nox --list-checks` lists every check and which apply to your system.
The full list, with what each one reads, is in
[docs/DOCTOR_NOX.md](docs/DOCTOR_NOX.md#checks).

Every finding has one of five levels:

| Level | Meaning |
|---|---|
| **Pass** | verified healthy |
| **Info** | verified, worth knowing, not a fault |
| **Warning** | verified, may need attention |
| **Error** | verified fault likely to break something |
| **Unknown** | could not be verified (missing tool, permission); never counted as a pass |

There is deliberately no percentage "health score".

## Using it

Open **Doctor NoX** from your app menu, or run `doctor-nox`. The window shows
the overall state, the check groups, results sorted most urgent first, and the
details of the selected finding: evidence and what you can do. Doctor never
runs those steps itself. It has light and dark themes and follows your desktop
by default. The main actions have shortcuts: F5 scans, Ctrl+F filters, Ctrl+S
saves a redacted report.

From a terminal:

| Command | Output |
|---|---|
| `doctor-nox --summary` | the overall state and anything that is not a pass |
| `doctor-nox --text` | the full report |
| `doctor-nox --json` | the report as JSON ([schema](data/doctor-nox-report.schema.json) `doctor-nox/report` v3) |
| `doctor-nox --only hardware --only system.image` | only these groups or checks |
| `doctor-nox --text --redact` | a version for sharing (see below) |
| `doctor-nox --list-checks` | every check, and which ones apply here |

Exit status: **0** no error, **1** at least one error, **2** usage error,
**3** Doctor itself failed. Warnings and unknowns don't change it. A scan
stops after 60 seconds (`--deadline`, 5–300).

The window needs PySide6 (Qt for Python). The command line needs only Python
3.10 or later.

### Sharing a report

`--redact`, **Copy summary** and **Export → Redacted report** replace your
username, hostname, home paths, IP and MAC addresses, e-mail addresses,
tokens, passwords, keys and machine IDs with placeholders. Redaction is
best-effort, so read the report before you post it. Full reports are saved
only where you choose, readable only by you (mode 0600).

## How it stays safe

- **Read-only.** No sudo or Polkit, no package installs or removals, no
  bootc or rpm-ostree changes, no `systemctl start/stop/enable`, no reboots, no
  file changes outside a report you save.
- **Allowlisted commands.** Every program Doctor may run, and every argument
  list it may use, is declared in [`doctor_nox/runner.py`](doctor_nox/runner.py):
  queries such as `rpm -q`, `dpkg --audit`, `pacman -Qdtq`, `rpm-ostree status`
  and `flatpak list`. Anything else is refused before a process starts, and a
  test fails if an allowed pattern contains a changing verb.
- **Bounded.** No shell, a minimal environment, time and output limits on
  every command, at most four at a time. Programs must be root-owned files
  under `/usr`.
- **Private.** It never reads tokens, keyrings, SSH keys, shell history or
  private app data, and never reads Wi-Fi network names.

## Install

Every [release](https://github.com/RoYoshi/Doctor-NoX/releases/latest) has a
package for each supported system, all listed in a signed `SHA256SUMS`:

| System | File |
|---|---|
| Fedora 44 and 45 | `doctor-nox-…fc45.noarch.rpm` / `…fc44.noarch.rpm` (signed) |
| openSUSE Tumbleweed | `doctor-nox-…opensuse.noarch.rpm` (signed) |
| Debian 13, Ubuntu 24.04 and later | `doctor-nox_…_all.deb` |
| Arch Linux | `doctor-nox-…-any.pkg.tar.zst`, or build `packaging/arch/PKGBUILD` (the AUR recipe) |
| Any Linux with Python 3.10+ | the wheel: `pipx install doctor_nox-…whl` (`…whl[gui]` for the window) |
| FE-XicoOS | included: every image installs a pinned, signed release |

A Flatpak (below) is built and tested on every commit too. PyPI, AUR and
Flathub listings are not set up yet ([docs/RELEASING.md](docs/RELEASING.md)).

**Fedora:** download the RPM for your release and `RPM-GPG-KEY-doctor-nox`.
Check the key, then install:

```bash
gpg --show-keys RPM-GPG-KEY-doctor-nox
#   the fingerprint must be 15E1018F7CDEF196E2B0C3F0AA19D45E300CE63D
sudo rpmkeys --import RPM-GPG-KEY-doctor-nox
rpmkeys --checksig doctor-nox-1.0.0-1.fc45.noarch.rpm   # expect "signatures OK"
sudo dnf install ./doctor-nox-1.0.0-1.fc45.noarch.rpm
```

**Build a package yourself** from a clone of this repository:

```bash
packaging/build-rpm.sh     # Fedora, openSUSE: needs rpm-build and python3-devel (Fedora) or python-rpm-macros (openSUSE)
packaging/build-deb.sh     # Debian, Ubuntu: needs build-essential debhelper dh-python pybuild-plugin-pyproject python3-hatchling
packaging/build-arch.sh    # Arch, as a normal user: needs base-devel python-build python-installer python-hatchling
```

Each writes the package to `dist/`. The window's dependency is
`python3-pyside6` (Fedora), `python3-pyside6.qtwidgets` (Debian 13; Ubuntu
25.04 and later), `pyside6` (Arch) or `python3NN-pyside6` (openSUSE). Ubuntu
24.04 does not package PySide6: there the command line works, and pipx with
the `gui` extra brings the window. Diagnostic tools (lspci,
nvidia-smi, flatpak…) are optional: a check whose tool is missing reports
**Unknown** instead of a false pass.

**From the source tree**, without installing: `python3 -m doctor_nox --summary`.

### The Flatpak

Inside a Flatpak sandbox Doctor would see only the Flatpak runtime and its own
private folders, not your computer. So the Flatpak runs Doctor's checks
**on the host**: it starts its own command line there with
`flatpak-spawn --host`, using your system's `/usr/bin/python3` (3.10 or later)
and the code in the app's folder, and shows that report. The host run is the
same read-only scan as a native install's; nothing in it needs root.

This needs the `org.freedesktop.Flatpak` permission, which lets an app run
programs outside its sandbox, so what keeps the Flatpak read-only is Doctor's
own code, not the sandbox. If you turn the permission off (Flatseal, your
desktop's app permissions), or the host has no Python 3.10, Doctor says so in
one finding instead of reporting on the sandbox.

```bash
flatpak-builder --user --install --force-clean flatpak-build packaging/flatpak/io.github.royoshi.doctornox.yml
flatpak run io.github.royoshi.doctornox
```

## Development

Python 3 with PySide6 (Qt Widgets); the logic is Qt-free and tested without a
display.

```bash
python3 -m unittest discover -s tests                    # GUI tests need PySide6
bin/doctor-nox --summary                                 # run from the checkout
QT_QPA_PLATFORM=offscreen bin/doctor-nox --gui-self-test # drive the real window headless
```

Checks are tested against synthetic systems: fixtures describe Fedora,
Silverblue, Bazzite, AlmaLinux, Debian, Ubuntu, Mint, Pop!_OS, Arch, CachyOS,
SteamOS, openSUSE Tumbleweed, Leap and Aeon, NixOS and Alpine, and every
command answer is faked through the real allowlist.

| Path | What |
|---|---|
| `doctor_nox/` | the application; `distro.py` says which Linux this is and where each check applies |
| `bin/doctor-nox` | launcher for the RPM layout and source checkouts |
| `data/` | desktop entry, AppStream metadata, icons, report schema |
| `pyproject.toml` | Python package (pip, pipx; the Debian and Arch packages build from it) |
| `packaging/` | RPM spec, `debian/`, Arch `PKGBUILD`, Flatpak manifest, build scripts, CI checks |

CI (`.github/workflows/ci.yml`) builds and installs the package on Fedora 45
and 44, openSUSE Tumbleweed, Debian 13, Ubuntu 26.04 and 24.04, and Arch, plus
the wheel with pipx and the Flatpak. Each installed package then runs the
command line and the GUI tests as an unprivileged user and is compared with the
source tree; the Flatpak also scans its host through `flatpak-spawn --host`.
The tests also run on Python 3.10, the oldest supported.

## Releases

Releases are made by hand through a protected workflow and published only after
review: signed RPMs for Fedora and openSUSE, the .deb, the Arch package and the
Python wheel and sdist, all in a signed `SHA256SUMS`.
[docs/RELEASING.md](docs/RELEASING.md) covers versioning, signing, verification,
and what PyPI, the AUR and Flathub still need.

Signing key: `15E1018F7CDEF196E2B0C3F0AA19D45E300CE63D`
([`packaging/RPM-GPG-KEY-doctor-nox`](packaging/RPM-GPG-KEY-doctor-nox)).
FE-Xico Doctor's releases were signed with an earlier key, `D0FA314C5A9DAE72B16F0AAEEB49C4761F24DE55`.

## History

Doctor NoX grew out of **FE-Xico Doctor**, the health checker of FE-XicoOS. It
was renamed and moved here with a fresh history in November 2026, then made
to work on every Linux: the FE-XicoOS-only checks were removed and the others
generalized. [docs/NAMES.md](docs/NAMES.md) lists what changed. The earlier
C++/Kirigami prototype (v0.1) is on the old repository's
[`archive/cpp-prototype`](https://github.com/RoYoshi/Doctor/tree/archive/cpp-prototype)
branch.

## License

Apache-2.0. See [LICENSE](LICENSE).
