# Doctor NoX · v1.0

Doctor NoX is a **read-only** health checker for Linux. It never changes
packages, deployments, services, drivers or files outside a report you
explicitly save, never needs root and never makes network requests.

## Launch

| How | Command |
|---|---|
| App menu | **Doctor NoX** (System) |
| Window | `doctor-nox` |
| Full text report | `doctor-nox --text` |
| Redacted sharing report | `doctor-nox --text --redact` (or `--json --redact`) |
| Problems only | `doctor-nox --summary` |
| JSON (schema v3) | `doctor-nox --json` |
| One area / one check | `doctor-nox --only hardware --only system.image` |
| List checks, and which apply here | `doctor-nox --list-checks` |
| From a source checkout | `python3 -m doctor_nox …` or `bin/doctor-nox …` |

CLI exit status: **0** no ERROR finding · **1** at least one ERROR · **2** usage
error · **3** Doctor itself failed. WARN and UNKNOWN never change the status.
`--deadline SECONDS` (5–300, default 60) bounds the whole scan.

## Which Linux this is

`doctor_nox/distro.py` works out, from `os-release` and the filesystem only
(it runs no command):

| Property | Values | From |
|---|---|---|
| family | `fedora`, `debian`, `arch`, `suse`, `other` | `ID`, then each word of `ID_LIKE` |
| image | `ostree`, `steamos`, `transactional`, or none | `/run/ostree-booted`; `ID=steamos`; MicroOS-style IDs |
| package tools | dnf, rpm, rpm-ostree, bootc, apt, dpkg, pacman, zypper, flatpak, snap, nix, apk, xbps, emerge | the program's file |
| init | `systemd` or `other` | `/run/systemd/system` |
| security module | `selinux`, `apparmor` or none | `/sys/kernel/security/lsm`, selinuxfs, AppArmor's module switch |

Every check declares where it applies (a `Scope`). A check that does not apply
is not run; the report lists it under `not_applicable`, with the kind of
system it is for, and the window shows these under **Not applicable here**.

| Scope | Checks |
|---|---|
| every Linux | identity, os-release, MAC, hardware, storage, network |
| package-based and transactional-update systems | pending restart |
| package-based Fedora, Debian, Arch and openSUSE | package database, last update |
| Fedora, Debian, Arch and openSUSE families | config files to review |
| those families and OSTree systems, not SteamOS | automatic updates |
| image-based OSTree systems | image deployments |
| systemd | firewall, failed services, boot journal |
| Flatpak or Snap installed | Flatpak, Snap |

## The window

- **Header:** the Doctor icon, the distribution · image type · kernel, and the
  overall state as a coloured pill with a plain-language headline:
  - **Problems found**: at least one Error;
  - **Needs attention**: at least one Warning;
  - **Partly verified**: at least one Unknown;
  - **Healthy**: otherwise.

  The time and duration of the last scan sit under the pill.
- **Check groups (left):** Overview, System, Updates & Packages, Security &
  Services, Hardware & Gaming, Storage, Apps, Network, Logs.
  - A group with problems shows a pill with how many checks are Error, Warning
    or Unknown, coloured by the worst one.
  - A group where everything was verified shows a green **OK** pill.
- **Results (centre):** sorted most-urgent first, with a status pill per row and
  alternating row shading. The status is always spelled out in text, never
  conveyed by colour alone.
- **Details (right, or below on narrow windows):**
  - status, summary and what that status means;
  - **Why** (for Unknown/Error);
  - **Evidence**, in a monospace block;
  - **What you can do**. Doctor never runs these steps itself.

  With no row selected, the pane shows the overview: the headline, counts,
  every group's worst status, the checks that do not apply here, and a legend
  of status levels.
- **Filter:** free text across title, summary, evidence and guidance.
  - Also `is:problem`, `is:warn`, `is:error`, `is:unknown`, `is:pass`.
  - The status chips (Error, Warning, Unknown, Info, Pass, each with its count
    and a coloured dot) show or hide each level.
- **Scan / Cancel:** scans run on a worker thread, so the window never blocks.
  The primary button reads **Scan again** after the first scan.
  - Refreshing while a scan is running is ignored.
  - Cancel stops in-flight commands. Unfinished checks become Unknown and the
    report is marked incomplete.
  - Closing the window mid-scan cancels the scan and closes once the worker
    has stopped. A running thread is never destroyed.
- **Copy summary:** copies a **redacted** summary.
- **Export:**
  - redacted report for sharing (.txt / .json);
  - full local report (.json / .txt), saved with mode 0600;
  - copy the full report to the clipboard.
- **View:** **Appearance** (Match system / Light / Dark) and **About**.
- **Status bar:** progress and results messages on the left. The right side
  always shows "Read-only · No root · No network requests · Nothing is
  uploaded".
- **Keyboard:**

  | Key | Action |
  |---|---|
  | Ctrl+F | filter |
  | F5 or Ctrl+R | scan |
  | Esc | cancel, or clear the filter |
  | Ctrl+Shift+C | copy summary |
  | Ctrl+S | save redacted report |
  | F1 | about |
  | Ctrl+Q | quit |

  Arrow keys move through groups and results. Every interactive widget has an
  accessible name, and keyboard focus is shown as an accent-coloured outline.

### Appearance

Doctor has a light and a dark theme. They are designed separately, not
inverted, and both live in `doctor_nox/theme.py`, which has no Qt
dependency.

- **Choosing:** by default Doctor follows the desktop's colour scheme (Qt's
  colour-scheme hint, else the desktop palette) and switches live when it
  changes. Choose a theme for the session under **View → Appearance**, or set
  `DOCTOR_NOX_THEME=light|dark|system`. Doctor writes no settings file.
- **Accent:** a calm diagnostic cyan, used **only** for interaction: the
  primary button, focus, selection and progress.
- **Status colours:** used only for results, so a selected row can never look
  like a result. Each status has a text colour, a soft tint and a border:

  | Status | Light text | Dark text |
  |---|---|---|
  | Error | `#b3261e` | `#f47c74` |
  | Warning | `#875200` | `#e8b84a` |
  | Unknown | `#56616d` | `#a9b2be` |
  | Info | `#1f5fa8` | `#78b6f0` |
  | Pass | `#1b7a46` | `#5fca91` |

- **Contrast:** tests check every text/background pair against WCAG 2.x:
  - body text at least 7:1;
  - secondary and status text at least 4.5:1, including on tints and the
    selection colour;
  - the focus accent at least 3:1.
- **Shape:** neutral grey surfaces with 1 px borders, 6–8 px rounded corners,
  rounded status pills, and no animations.
- **Fonts:** your desktop's UI font (or `DOCTOR_NOX_FONT` if that family is
  installed). Check IDs and evidence use the desktop's fixed-width font.
- **Native dialogs:** a matching `QPalette` is applied, so native dialogs and
  menus follow the theme.
- **Icon:** `io.github.royoshi.doctornox` is RoYoshi's magenta medical badge
  (white cross with a smile), until the new Doctor NoX icon is ready.
  - It ships as raster PNGs at 16–512 px in `hicolor`, downscaled from the
    pinned original `tools/doctor-nox-icon-source.png` by
    `python3 tools/build_doctor_icon.py`.
  - The art is never redrawn, and there is no SVG.
  - If the icon is missing, the window draws a deterministic glyph. There is
    never a generic or Tux fallback.

## Severity — what each level promises

| Level | Meaning |
|---|---|
| **PASS** | Verified healthy. |
| **INFO** | Verified and worth knowing, but not a fault. Examples: automatic updates off, a restart pending for less than a week, Nouveau/NVK in use, offline, no MAC on Arch. |
| **WARN** | Verified condition that may need attention. Examples: kernel modules removed by an update, SELinux permissive, interrupted dpkg run, no update for 30 days. |
| **ERROR** | Verified fault likely to break functionality or safety. Examples: unreadable package database, read-only `/var` or root filesystem, < 2 GiB free, < 80 MiB free on `/boot`. |
| **UNKNOWN** | Could not be verified (missing tool, permission, unsupported format). It is **never** counted as PASS. The overall state is UNKNOWN if any check is UNKNOWN and none is WARN/ERROR. |

There is deliberately no percentage "health score".

## Checks

| ID | Applies to | What it reads | Notes |
|---|---|---|---|
| `system.identity` | all | `os-release`, the platform above | INFO: distribution, family, image type, package tools, init, security module, kernel. |
| `system.os_release` | all | `/etc/os-release` vs `/usr/lib/os-release` | A stale regular-file copy (OSTree's `/etc` merge keeps old files) → WARN. |
| `system.image` → `system.image.{booted,source,pending,rollback,pinned,local_changes}` | OSTree | `bootc status --json --format-version=1`, else `rpm-ostree status --json` | See [image deployments](#image-deployments). |
| `updates.reboot` | package-based, transactional | `/run/reboot-required(.pkgs)`, `/run/reboot-needed`, kernel module trees | See [pending restart](#pending-restart). |
| `updates.packages` | package-based Fedora, Debian, Arch, openSUSE | `rpm -q rpm`, `dpkg --audit` + `/var/lib/dpkg/updates`, `pacman -Q pacman` + `db.lck` + `pacman -Qdtq`, `/run/zypp.pid` | See [package database](#package-database). |
| `updates.config_files` | the four families | a bounded walk of `/etc` | `.rpmnew`/`.rpmsave`, `.dpkg-dist`/`.dpkg-old`/`.ucf-*`, `.pacnew`/`.pacsave` → INFO with the family's merge tool. |
| `updates.last_update` | package-based, the four families | the end of `pacman.log`; `/var/lib/dpkg/status`; `rpm -qa --queryformat=%{INSTALLTIME}` | WARN after 30 days. On Arch only a full `pacman -Syu` counts. |
| `updates.automatic` | the four families and OSTree, not SteamOS | `systemctl is-enabled` of known timers, `rpm-ostreed.conf`, `dnf/automatic.conf`, `apt.conf.d` | Always INFO. See [automatic updates](#automatic-updates). |
| `security.mac` | all | `/sys/kernel/security/lsm`, selinuxfs, `getenforce`, AppArmor's `enabled`, `/proc/cmdline` | SELinux enforcing or AppArmor on → PASS; SELinux permissive → WARN; off where the family turns one on by default (Fedora, Debian, openSUSE) → WARN; off on Arch and others → INFO; nothing readable → UNKNOWN. |
| `security.firewall` | systemd | `systemctl show firewalld.service`, `/etc/ufw/ufw.conf` | firewalld running or ufw enabled → PASS; neither → INFO. Rules set directly with nftables need root to read. |
| `security.failed_units` | systemd | `systemctl list-units --state=failed` (system + user) | WARN with unit names and inspection commands. Doctor never restarts or resets units. |
| `hardware.cpu_memory` | all | `/proc/cpuinfo`, `/proc/meminfo` | < 5 % available → WARN (point in time). |
| `hardware.graphics` | all | `/sys/class/drm`, `lspci -nnk`, module trees | See [graphics](#graphics). |
| `hardware.nvidia` | all (INFO without an NVIDIA GPU) | `/sys/module/nvidia/version`, `nvidia-smi` | `nvidia-smi` only runs when the module is loaded. Kernel/user-space version mismatch → WARN. Missing `nvidia-smi` names the family's package. |
| `hardware.session` | all | `XDG_SESSION_TYPE` | Wayland PASS, X11 INFO. |
| `hardware.audio` | all | `/proc/asound/cards`, PipeWire and PulseAudio sockets | Cards but no sound server → WARN. No cards → INFO. |
| `hardware.controllers` | all | `/proc/bus/input/devices`, `/sys/bus/usb/devices`, `modules.dep`; on Fedora `rpm -q kernel-modules-extra-$(uname -r)` | See [controllers](#controllers). |
| `hardware.sensors` | all | `/sys/class/hwmon` | WARN only at or above a sensor's own `crit` value. |
| `storage.layout` / `storage.home` | all | `/proc/self/mountinfo` | See [storage](#storage). |
| `storage.space` | all | `statvfs` | See [storage](#storage). |
| `software.flatpak` | Flatpak installed | `flatpak remotes`, `flatpak list` (system + user), `XDG_DATA_DIRS` | Apps and remotes → PASS; no remotes and no apps → INFO; installed apps whose folder is missing from `XDG_DATA_DIRS` in a desktop session → WARN (they may not appear in menus). |
| `software.snap` | Snap installed | `snap list`, `XDG_DATA_DIRS` | snapd not answering → WARN; a broken snap → WARN; `/var/lib/snapd/desktop` missing from `XDG_DATA_DIRS` → WARN. |
| `network.link` / `.route` / `.dns` / `.manager` | all | `/sys/class/net`, `/proc/net/*`, `/etc/resolv.conf`, `nmcli -t general` | Local state only. Offline = INFO. No packets are sent and no connectivity check is triggered. Wi-Fi names are never read. DNS search domains are counted, not shown. |
| `logs.boot` | systemd | `journalctl --boot=0 --priority=0..3 --output=json --lines=400` | See [logs](#logs). |

### Image deployments

Doctor asks `bootc status` first. bootc refuses users who are not root, so on
most desktops the answer comes from `rpm-ostree status --json`, which any user
may read. Both are read-only, and the evidence says which one answered.

- **Booted:** the image reference and version, how old the image is; WARN if
  bootc marks it incompatible or it has no update source.
- **Update source:** bootc's configured image vs the booted one → WARN, or INFO
  when the staged deployment already follows the new source.
- **Next boot:** staged update → INFO ("applies at the next reboot"). A
  **download-only** deployment is "will NOT be applied at reboot": Doctor never
  claims a reboot applies it. A queued rollback (bootc's `rollbackQueued`, or
  rpm-ostree listing the previous deployment first) is reported as such.
- **Rollback and pinned:** a previous deployment → PASS; none → INFO. Pinned
  deployments are counted across every slot.
- **Local changes:** layered or removed packages, or an unlocked `/usr` → INFO,
  because each update has to reapply them.
- Neither tool answering → UNKNOWN, with `/run/ostree/staged-deployment` as
  evidence.

### Pending restart

- Debian/Ubuntu `/run/reboot-required` (with the packages that asked, from
  `.pkgs`) and openSUSE `/run/reboot-needed` → INFO, WARN after 7 days.
- The running kernel's modules removed by an update → WARN: new USB devices,
  filesystems or VPNs that need a module fail until a restart. This is the
  signal on Arch, which removes the old kernel's modules.
- A newer kernel of the **same flavour** installed → INFO. Flavours are kept
  apart (`-generic` vs `-lowlatency`, Fedora's `+debug`), and on Arch a
  different release is another kernel package (`linux`, `linux-lts`), not an
  update, so it never counts.
- Both `/usr/lib/modules` and `/lib/modules` are read (openSUSE Leap 15 has no
  merged `/usr`). No kernels found → UNKNOWN (normal in containers and WSL).

### Package database

- **Fedora:** `rpm -q rpm` fails → ERROR (the database cannot be read).
- **Debian/Ubuntu:** `dpkg --audit` output, or records left in
  `/var/lib/dpkg/updates` by an interrupted run → WARN with
  `dpkg --configure -a`.
- **Arch:** `db.lck` with no `pacman` process running → WARN (a stale lock blocks
  every package change); pacman running now → INFO; orphaned dependencies →
  INFO; `pacman -Q pacman` fails → ERROR.
- **openSUSE:** the RPM database as on Fedora; a zypper holding its lock right
  now → INFO. A lock file naming a dead process is not reported: libzypp takes
  such a lock over by itself.

### Automatic updates

Doctor reads these timers with `systemctl is-enabled` only:

| Family | Timers | Also read |
|---|---|---|
| OSTree | `rpm-ostreed-automatic.timer`, `bootc-fetch-apply-updates.timer`, `uupd.timer`, `ublue-update.timer`, `fe-xicoos-auto-update.timer` | `AutomaticUpdatePolicy` in `/etc/rpm-ostreed.conf` (`none` means the timer does nothing) |
| Fedora | `dnf-automatic.timer`, `dnf5-automatic.timer`, `dnf-automatic-install.timer` | `apply_updates` in `/etc/dnf/automatic.conf` |
| Debian | `apt-daily-upgrade.timer` | unattended-upgrades installed, `APT::Periodic::Unattended-Upgrade` in `apt.conf.d` |
| openSUSE | `transactional-update.timer` | |
| Arch | none: Arch updates when you run `pacman -Syu` | |

The result is always INFO: on or off is a choice. `bootc-fetch-apply-updates`
restarts the computer by itself, so the summary says so. Software centers
(GNOME Software, KDE Discover) can update too; Doctor does not read their
settings and says so.

### Graphics

- NVIDIA Open is told apart from the proprietary module via
  `/proc/driver/nvidia/version` or the module licence. Both → PASS.
- Nouveau (+ NVK) → INFO: a valid choice. It becomes WARN when the NVIDIA
  driver is installed for the running kernel but Nouveau claimed the GPU.
- AMD amdgpu/radeon and Intel i915/xe = PASS. Virtual GPUs = INFO. No bound
  driver = WARN.

### Controllers

- xpad loaded, built in, or present for the **running** kernel → PASS.
- xpad missing and no Xbox-protocol device → INFO (not a fault).
- An Xbox-protocol USB interface (class ff/5d/01, ff/47/d0) with no bound
  driver → WARN.
- Where xpad comes from is said in the distribution's terms:
  `kernel-modules-extra-<running kernel>` on Fedora (checked with `rpm -q`),
  `linux-modules-extra-$(uname -r)` on Ubuntu, the image itself on image-based
  systems.
- Hyperkin (2e24) devices are listed but **not assumed** to need xpad; HID-mode
  pads work without it.

### Storage

- `storage.layout`:
  - `/var` or `/etc` read-only → ERROR everywhere.
  - Package-based: a read-only root → ERROR (usually a remount after an
    error); `/usr` mounted read-only on its own → WARN.
  - OSTree: read-only `/` and `/usr` are normal; a writable `/usr` → INFO
    (`bootc usr-overlay`, `rpm-ostree usroverlay`).
  - SteamOS and transactional systems: a read-only system partition is
    normal; a writable one → INFO (changes are lost at the next update).
- `storage.home`: read-only → ERROR; in memory (tmpfs) → WARN; on OSTree,
  not under `/var` or a separate mount → WARN.
- `storage.space` checks `/` and `/var` (OSTree: `/sysroot` and `/var`; SteamOS:
  `/var`, since its system partition is full by design), your home, and a
  separate `/boot`. Each real filesystem is reported once: btrfs subvolumes
  and OSTree's bind mounts share a device. Image layers (composefs, erofs,
  squashfs) are skipped.
  - ERROR below 2 GiB; WARN below 5 % of the disk, but never below 5 GiB
    (10 GiB where OSTree deployments live) or above 20 GiB.
  - Filesystems under 8 GiB: WARN below 10 %, ERROR below 3 %.
  - `/boot`: WARN below 200 MiB, ERROR below 80 MiB (a kernel update needs
    room).
  - The guidance names the family's cleanup: `paccache`, `apt autoremove`,
    `dnf autoremove`, snapper snapshots.

### Logs

- Critical entries → WARN. Error-level only → INFO, since these are common and
  often harmless.
- No journal files → UNKNOWN.
- Without journal group access (`systemd-journal`, `adm` or `wheel`), Doctor
  says that only your own logs are visible and reports UNKNOWN rather than a
  false PASS.

## Safety boundaries (enforced in code and by tests)

- **Read-only.** No sudo/pkexec/Polkit, no package installs or removals, no
  bootc or rpm-ostree transactions, no module loads, no `systemctl
  start/stop/enable/mask/daemon-reload/reset-failed`, no reboot, no chmod,
  no cleanup.
- **Command allowlist with argument patterns.** `doctor_nox/runner.py`
  declares every executable *and* every permitted argument list. Unit names,
  rpm packages, kernel modules and modinfo fields come from fixed sets.
  Anything else raises before a process is created. A test fails if any
  pattern contains a mutating verb (`upgrade`, `-Syu`, `--configure`,
  `refresh`, `rebase`, …).
- **Bounded processes:**
  - no shell and no stdin;
  - minimal environment (no `LD_PRELOAD`, `PYTHONPATH` or tokens);
  - per-command timeout and stdout/stderr byte caps;
  - the whole process group is killed on timeout, overflow or cancel;
  - at most 4 concurrent commands;
  - a 60-second scan deadline.
- **Trusted inputs.** Executables must be root-owned, non-writable and under
  `/usr`; Doctor runs the verified file but keeps the name it was found under,
  so multi-call tools such as `modinfo` (a link to `kmod`) behave. Root-produced
  state files must be regular, non-symlink, root-owned and not
  group/world-writable before they are believed.
- **Privacy.**
  - No network requests, telemetry or automatic exports.
  - Doctor never reads tokens, keyrings, SSH keys, shell history or private app data.
  - Files are written only to the path you choose, atomically. Full reports are mode 0600.

## Redaction (sharing reports)

`--redact`, COPY, and the "Redacted sharing report" exports replace:

| Matches | Replaced with |
|---|---|
| your username and hostname (whole words, case-insensitive; also the short hostname) | `<user>` / `<host>` |
| `/home/<name>` and `/var/home/<name>` | `~` |
| IPv4/IPv6 addresses (loopback and unspecified addresses are kept) | `<ipv4>` / `<ipv6>` |
| MAC addresses | `<mac>` |
| e-mail addresses | `<email>` |
| URL credentials `scheme://user:pass@` | `<credentials>@` |
| query-string secrets (`token=`, `key=`, `password=`, `sig=`, …) | `<redacted>` |
| `key: value` / `key=value` secrets (`password`, `token`, `secret`, `api_key`, `Authorization`) and `Bearer …`/`Basic …` | `<redacted>` |
| GitHub/Slack/AWS-style tokens, JWTs, private-key blocks | `<token>` / `<private-key>` |
| hex identifiers of 32+ characters (machine IDs, boot IDs) | `<hex-id>` |
| image digests | shortened to `sha256:` + 12 characters |

**Not redacted, on purpose:**
- image references such as `ghcr.io/ublue-os/bazzite:stable`;
- kernel version, package names and versions, unit names, interface names;
- PCI/USB vendor:product IDs.

Names everyone shares are not replaced, because that would mangle ordinary
text: `root`, `admin`, `user`, `localhost`, and distribution default host and
user names such as `fedora`, `ubuntu`, `debian`, `archlinux`, `steamdeck` and
`deck`.

**Limits:** redaction is pattern-based. A secret in an unusual format, or a
private name inside a log message, can slip through. That is why the sharing
header asks you to review before posting.

## Report schema v3

`data/doctor-nox-report.schema.json` describes the JSON report
(`schema: doctor-nox/report`, `schema_version: 3`). Compared with v2 (FE-Xico
Doctor and the first Doctor NoX commit):

- `host` has `name`, `id`, `version`, `family`, `image`, `kernel`, `init`,
  `security` and `package_managers` (a list) instead of `edition` and
  `image_variant`;
- `not_applicable` lists each check left out, with `id`, `category`, `title`
  and `applies_to`;
- the bootc findings are now `system.image.*`, from bootc or rpm-ostree.

## Architecture

```
doctor_nox/
  distro.py     which Linux this is; Scope rules for where each check applies
  model.py      Finding, severities, categories, report schema v3, validator
  runner.py     allowlist (command + argument patterns), bounded execution
  context.py    filesystem root, env, kernel, identity, platform, trusted reads
  probes/       system, updates, security, hardware, storage, software, network, logs
  engine.py     applicability, 4 workers, isolation, progress, cancellation, deadline
  report.py     text / JSON / redaction / atomic export
  viewmodel.py  Qt-free filtering, grouping, badges, detail HTML (unit-tested)
  theme.py      Qt-free light/dark palettes, status colours, WCAG contrast helper
  gui.py        PySide6 Qt Widgets window, stylesheet, status-pill delegates,
                worker thread, self-test, synthetic-data screenshots
  cli.py        the command line; also the `doctor-nox` entry point
```

How it is installed:

| Format | Python package | Command |
|---|---|---|
| RPM (Fedora, openSUSE) | `/usr/share/doctor-nox/doctor_nox/`, with bytecode | `bin/doctor-nox` → `/usr/bin/doctor-nox` |
| .deb, Arch, pip, pipx | the Python module directory (from the wheel) | the `doctor-nox` entry point |

Every format installs `/usr/share/applications/io.github.royoshi.doctornox.desktop`,
`/usr/share/metainfo/io.github.royoshi.doctornox.metainfo.xml`,
`/usr/share/doctor-nox/doctor-nox-report.schema.json` and the hicolor icons
(pip and pipx under their own prefix).

**Why PySide6 Qt Widgets:**
- Every supported family packages PySide6, and pip provides it elsewhere.
- It gives native desktop theming hooks.
- The logic is testable without a display through `viewmodel.py`.

**Adding a check:**
1. Write `@probe("area.name", "<category>", "Title", applies=<Scope>)` in the
   right `probes/*.py`, returning `Finding`(s). Pick the narrowest Scope from
   `distro.py` (or add one with words that say where it applies).
2. Add any new command to `runner.ALLOWED` with exact argument patterns.
3. Add fixture tests to `tests/test_doctor_nox_probes.py`, using
   `install_distro()` for each family the check handles.

## Tests

```bash
python3 -m unittest discover -s tests                  # all repository tests
python3 -m unittest discover -s tests -p 'test_doctor_nox_probes.py'   # probe fixtures
QT_QPA_PLATFORM=offscreen bin/doctor-nox --gui-self-test   # needs PySide6
```

What the tests cover:
- **Platform detection:** os-release fixtures for Fedora, Silverblue, Bazzite,
  FE-XicoOS, AlmaLinux, Ubuntu, Debian, Mint, Pop!_OS, Arch, CachyOS, SteamOS,
  openSUSE Tumbleweed, Leap and Aeon, NixOS and Alpine.
- **Probes:** run on synthetic filesystem roots through a fake runner that
  enforces the real allowlist. Nothing executes rpm-ostree, systemctl,
  journalctl, dpkg, pacman, rpm, flatpak or snap. Every check is also run on
  every family with missing tools, timeouts and garbage output, and may never
  claim ERROR without evidence.
- **Runner:** tested against a temporary helper script (output cap, timeout,
  cancel killing grandchildren, Unicode, minimal environment, argv[0] for
  multi-call tools).
- **GUI self-test:** uses synthetic probes to load, filter, select, export,
  rescan, ignore a rapid refresh, cancel, and close while scanning.
  - It also switches to Light, Dark and Match system and renders the whole
    window in each.
  - It fails if a delegate raised a paint error or Qt could not parse a
    stylesheet.
- **Themes:** WCAG contrast for every text/background pair in both palettes;
  no neon or OLED-black values; status colours distinct from each other and
  from the accent.
- **Screenshots:** `doctor-nox --gui-screenshot DIR` (hidden QA flag) renders
  the real window with **synthetic** results and runs no host probes. It saves
  `welcome`, `overview`, `details` and `narrow` PNGs, in both themes.
- **Packages (`.github/workflows/ci.yml`):** builds the package on each
  supported family, inspects it, installs it, and runs `--list-checks`, the
  CLI report modes, `--gui-smoke-test` and `--gui-self-test` from the
  installed package as an unprivileged user, then compares the installed
  package's reports with the source tree's:
  - Fedora 45 and 44: the RPM, rpmlint, and an install with signature
    enforcement on (signed with a throwaway key);
  - openSUSE Tumbleweed: the same spec;
  - Debian 13, Ubuntu 26.04 and Ubuntu 24.04 (command line only): the .deb,
    and the AppStream metadata with `appstreamcli validate`;
  - Arch: `makepkg` with `packaging/arch/PKGBUILD`;
  - Python: the sdist and wheel (`twine check`, contents), the sdist building
    the same wheel, and pipx installs with and without the `gui` extra.

### GUI checks and threading rule

The self-test prints a flushed `[doctor self-test]` checkpoint for every step,
installs a Qt message handler, and dumps all thread stacks if it hangs for
60 s. It never opens modal dialogs. Image builds that ship Doctor (FE-XicoOS
does) run `--gui-smoke-test` and `--gui-self-test` with `python3 -I -u -X
faulthandler` under an outer `timeout`.

**Threading rule:**
- `ScanThread` signals are emitted from the scan thread and must be connected
  with `Qt.QueuedConnection` to `@Slot` methods of `Window`. **Never connect
  them to a lambda.** PySide6 runs lambda receivers on the emitting thread, so
  widget code would run off the GUI thread.
- That is what crashed an early image build.
- `tests/test_doctor_nox.py::GuiThreadRuleTests` enforces this, and each
  slot records any off-thread call in `Window.thread_violations`.
