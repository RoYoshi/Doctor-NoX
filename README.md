# Doctor NoX

A read-only system-health checker for **FE-XicoOS**, a Fedora bootc desktop
with KDE Plasma 6. It looks at the parts of the system that tend to go wrong:
updates and bootc deployments, services, graphics drivers, controllers,
storage, Flatpaks, network and logs. For each finding it says what it means
and what you can do about it.

Doctor only looks. It never needs root, never changes anything, never makes a
network request, and never uploads anything.

## What it checks

| Group | Examples |
|---|---|
| System & bootc | booted, staged, rollback and pinned deployments; image source; a stale `/etc/os-release` |
| Updates | the automatic update timer, the legacy timer that reboots unattended, last update results, FE-Xico Updater's configuration |
| Security & Services | SELinux mode, failed units, permissions on update state |
| Hardware & Gaming | GPU driver (NVIDIA Open, Nouveau/NVK, AMD, Intel), NVIDIA kernel/userspace mismatch, audio, Xbox-protocol controllers, temperatures |
| Storage | read-only `/var` or `/etc`, where your home lives, free space |
| Apps & Flatpaks | core apps present, Flatpak provisioning |
| Network | links, routes, DNS, NetworkManager (local state only, no packets sent) |
| Logs | critical boot-log entries, errors from FE-XicoOS update services |

`doctor-nox --list-checks` lists every check ID. The full list, with what
each one reads, is in [docs/DOCTOR_NOX.md](docs/DOCTOR_NOX.md#checks).

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

Open **Doctor NoX** from the Plasma menu, or run `doctor-nox`. The
window shows the overall state, the check groups, results sorted most urgent
first, and the details of the selected finding: evidence and what you can do.
Doctor never runs those steps itself. It has light and dark themes and
follows your desktop by default. The main actions have shortcuts: F5 scans,
Ctrl+F filters, Ctrl+S saves a redacted report.

From a terminal:

| Command | Output |
|---|---|
| `doctor-nox --summary` | the overall state and anything that is not a pass |
| `doctor-nox --text` | the full report |
| `doctor-nox --json` | the report as JSON ([schema](data/doctor-nox-report.schema.json) `doctor-nox/report` v2) |
| `doctor-nox --only hardware --only system.bootc` | only these groups or checks |
| `doctor-nox --text --redact` | a version for sharing (see below) |

Exit status: **0** no error, **1** at least one error, **2** usage error,
**3** Doctor itself failed. Warnings and unknowns don't change it. A scan
stops after 60 seconds (`--deadline`, 5–300).

### Sharing a report

`--redact`, **Copy summary** and **Export → Redacted report** replace your
username, hostname, home paths, IP and MAC addresses, e-mail addresses,
tokens, passwords, keys and machine IDs with placeholders. Redaction is
best-effort, so read the report before you post it. Full reports are saved
only where you choose, readable only by you (mode 0600).

## How it stays safe

- **Read-only.** No sudo or Polkit, no bootc or rpm-ostree changes, no package
  installs, no `systemctl start/stop/enable`, no reboots, no file changes
  outside a report you save.
- **Allowlisted commands.** Every program Doctor may run, and every argument
  list it may use, is declared in [`doctor_nox/runner.py`](doctor_nox/runner.py).
  Anything else is refused before a process starts, and a test fails if an
  allowed pattern contains a changing verb.
- **Bounded.** No shell, a minimal environment, time and output limits on
  every command, at most four at a time.
- **Trusted inputs only.** It believes state files only when they are
  root-owned and not writable by others. It never reads tokens, keyrings,
  SSH keys, shell history or private app data.

## Install

**FE-XicoOS** includes Doctor: every image installs a pinned, signed release.

On **Fedora 45 or 44**, download the RPM for your release and
`RPM-GPG-KEY-doctor-nox` from the
[latest release](https://github.com/RoYoshi/Doctor-NoX/releases/latest). Check the
key, then install:

```bash
gpg --show-keys RPM-GPG-KEY-doctor-nox
#   the fingerprint must be D0FA314C5A9DAE72B16F0AAEEB49C4761F24DE55
sudo rpmkeys --import RPM-GPG-KEY-doctor-nox
rpmkeys --checksig doctor-nox-1.0.0-1.fc45.noarch.rpm   # expect "signatures OK"
sudo dnf install ./doctor-nox-1.0.0-1.fc45.noarch.rpm
```

It needs `python3-pyside6`, which dnf installs for you. Diagnostic tools
(bootc, flatpak, lspci, nvidia-smi…) are optional. Checks for things your
system doesn't have report **Unknown** instead of a false pass.

## Development

Python 3 with PySide6 (Qt Widgets); the logic is Qt-free and tested without a
display.

```bash
python3 -m unittest discover -s tests                         # GUI tests need python3-pyside6
bin/doctor-nox --summary                                  # run from the checkout
QT_QPA_PLATFORM=offscreen bin/doctor-nox --gui-self-test  # drive the real window headless
packaging/build-rpm.sh                                        # RPM + SRPM in dist/ (needs rpm-build python3-devel desktop-file-utils)
```

| Path | Installed as |
|---|---|
| `bin/doctor-nox` | `/usr/bin/doctor-nox` |
| `doctor_nox/` | `/usr/share/doctor-nox/doctor_nox/` |
| `data/io.github.royoshi.doctornox.desktop` | `/usr/share/applications/` |
| `data/doctor-nox-report.schema.json` | `/usr/share/doctor-nox/` |
| `data/icons/hicolor/` | `/usr/share/icons/hicolor/` |
| `packaging/` | RPM spec, build script, CI checks |

CI builds the RPM on Fedora 45 and 44 for every pull request. It checks the
file list and dependencies and runs rpmlint. It installs the package with
signature enforcement on, signed with a throwaway key. Then it runs the CLI
and GUI tests from the installed package as an unprivileged user.

Fedora 45 refuses unsigned packages, so a local build installs there only
once you sign it with your own key and import that key.

## Releases

Releases are signed, made by hand through a protected workflow, and
published only after review. [docs/RELEASING.md](docs/RELEASING.md) covers
versioning, signing, verification and how FE-XicoOS pins a release.

Signing key: `D0FA314C5A9DAE72B16F0AAEEB49C4761F24DE55`
([`packaging/RPM-GPG-KEY-doctor-nox`](packaging/RPM-GPG-KEY-doctor-nox)).

## History

Doctor NoX was called **FE-Xico Doctor** until November 2026. It shipped inside
the FE-XicoOS repository, moved to [RoYoshi/Doctor](https://github.com/RoYoshi/Doctor)
for its 1.0.0 release, and was renamed and moved here with a fresh history.
The checks are the same; [docs/NAMES.md](docs/NAMES.md) lists every name and
path that changed. The earlier C++/Kirigami prototype (v0.1) is still on the old
repository's
[`archive/cpp-prototype`](https://github.com/RoYoshi/Doctor/tree/archive/cpp-prototype)
branch.

## License

Apache-2.0. See [LICENSE](LICENSE).
