# Doctor NoX · v1.0

Doctor NoX is FE-XicoOS's native, **read-only** system-health
application. It is developed, tested and released in this repository and
installed in FE-XicoOS as the signed `doctor-nox` RPM (see
[RELEASING.md](RELEASING.md)). It is separate from FE-Xico Updater: it never invokes the
updater's services, never touches its transaction lock, and never changes
bootc deployments, timers, packages, drivers or files outside a report you
explicitly save.

## Launch

| How | Command |
|---|---|
| Plasma menu | **Doctor NoX** (System) |
| Foot, window | `doctor-nox` |
| Full text report | `doctor-nox --text` |
| Redacted sharing report | `doctor-nox --text --redact` (or `--json --redact`) |
| Problems only | `doctor-nox --summary` |
| JSON (schema v2) | `doctor-nox --json` |
| One area / one check | `doctor-nox --only hardware --only system.bootc` |
| List check IDs | `doctor-nox --list-checks` |

CLI exit status: **0** no ERROR finding · **1** at least one ERROR · **2** usage
error · **3** Doctor itself failed. WARN and UNKNOWN never change the status.
`--deadline SECONDS` (5–300, default 60) bounds the whole scan.

## The window

- **Header:** the Doctor icon, the OS version · edition · kernel, and the
  overall state as a coloured pill with a plain-language headline:
  - **Problems found**: at least one Error;
  - **Needs attention**: at least one Warning;
  - **Partly verified**: at least one Unknown;
  - **Healthy**: otherwise.

  The time and duration of the last scan sit under the pill.
- **Check groups (left):** Overview, System & bootc, Updates, Security &
  Services, Hardware & Gaming, Storage, Apps & Flatpaks, Network, Logs.
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
  every group's worst status and a legend of status levels.
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
- **Fonts:** your desktop's UI font, so Doctor matches KDE (or
  `DOCTOR_NOX_FONT` if that family is installed). Check IDs and evidence use
  the desktop's fixed-width font.
- **Native dialogs:** a matching `QPalette` is applied, so native dialogs and
  menus follow the theme.
- **Icon:** `io.github.royoshi.doctornox` is RoYoshi's magenta medical badge
  (white cross with a smile). It is Doctor's own identity and deliberately does
  **not** use the FE-XicoOS top hat.
  - It ships as raster PNGs at 16–512 px in `hicolor`, downscaled from the
    pinned original `tools/doctor-nox-icon-source.png` by
    `python3 tools/build_doctor_icon.py`.
  - The art is never redrawn, and there is no SVG.
  - If the icon is missing, the window draws a deterministic "FX" glyph. There
    is never a generic or Tux fallback.

## Severity — what each level promises

| Level | Meaning |
|---|---|
| **PASS** | Verified healthy. |
| **INFO** | Verified and worth knowing, but not a fault. Examples: manual-update mode, Nouveau/NVK on the standard image, offline, first-boot Flatpaks pending. |
| **WARN** | Verified condition that may need attention. |
| **ERROR** | Verified fault likely to break functionality or safety. Examples: legacy auto-reboot timer enabled, read-only `/var`, no trusted updater config, < 2 GiB free. |
| **UNKNOWN** | Could not be verified (missing tool, permission, unsupported format). It is **never** counted as PASS. The overall state is UNKNOWN if any check is UNKNOWN and none is WARN/ERROR. |

There is deliberately no percentage "health score".

## Checks

| ID | What it reads | Notes |
|---|---|---|
| `system.identity` | `/usr/lib/os-release` | FE-XicoOS name, version, edition, kernel. |
| `system.os_release` | `/etc/os-release` vs `/usr/lib/os-release` | A stale regular-file copy kept by the OSTree `/etc` merge → WARN (this was seen on FE-ROYO). |
| `system.bootc` → `system.bootc.{booted,source,pending,rollback,pinned}` | `bootc status --json --format-version=1` | See [bootc sources](#bootc-sources) below. |
| `updates.timers` → `updates.{auto_timer,legacy_timer,migration}` | `systemctl is-enabled` / `show` | Safe hourly timer on = PASS; off = INFO (manual mode). Legacy `bootc-fetch-apply-updates.timer`: masked or absent = PASS, merely disabled = WARN, enabled = ERROR (it reboots unattended). |
| `updates.active_hours` | `/var/lib/fe-xicoos-auto-updates/settings.json` | Absent → worker default 09–23. Invalid or untrusted → WARN. |
| `updates.results` → `updates.{auto_result,updater_result}` | `event.json`, `/run/fe-xicoos-updater/{status,check,upgrade,rollback}.json` | Root-owned, non-writable files only. A failed operation this boot → WARN. |
| `updates.updater` | build marker, executable, `config.ini` | Mirrors the updater's own lookup: `/etc` → `/usr/local/etc` → `/usr/etc` → XDG dirs; the first root-owned, not group/world-writable file wins. See the note below. |
| `updates.notifier` | `systemctl --user is-enabled` | UNKNOWN when there is no user session bus. |
| `security.selinux` | `/sys/fs/selinux/enforce`, `getenforce` | Enforcing PASS, permissive WARN, disabled (as reported by getenforce) ERROR. No answer at all = UNKNOWN, not "disabled". |
| `security.failed_units` | `systemctl list-units --state=failed` (system + user) | WARN with unit names and inspection commands. Doctor never restarts or resets units. |
| `security.state_permissions` | update state directories | A non-root-writable state directory → WARN. |
| `hardware.cpu_memory` | `/proc/cpuinfo`, `/proc/meminfo` | < 5 % available → WARN (point in time). |
| `hardware.graphics` | `/sys/class/drm`, `lspci -nnk` | See [graphics](#graphics) below. |
| `hardware.nvidia` | `/sys/module/nvidia/version`, `nvidia-smi` | `nvidia-smi` only runs when the nvidia module is loaded. A kernel/user-space version mismatch → WARN. |
| `hardware.session` | `XDG_SESSION_TYPE` | Wayland PASS, X11 INFO. |
| `hardware.audio` | `/proc/asound/cards`, PipeWire socket | Cards but no PipeWire → WARN. No cards → INFO. |
| `hardware.controllers` | `/proc/bus/input/devices`, `/sys/bus/usb/devices`, `modules.dep`, `rpm -q kernel-modules-extra-$(uname -r)` | See [controllers](#controllers) below. |
| `hardware.sensors` | `/sys/class/hwmon` | WARN only at or above a sensor's own `crit` value. |
| `storage.layout` / `storage.home` | `/proc/self/mountinfo` | See [storage](#storage) below. |
| `storage.space` | `statvfs` | See [storage](#storage) below. |
| `software.flatpaks` | `flatpak list --system/--user`, provisioning marker, service state | See [Flatpaks](#flatpaks) below. |
| `software.core_apps` | file presence, `rpm -q konsole-part` | Foot, Dolphin + F4 terminal (konsole-part), Steam, Doctor. Standalone Konsole present → INFO. |
| `network.link` / `.route` / `.dns` / `.manager` | `/sys/class/net`, `/proc/net/*`, `/etc/resolv.conf`, `nmcli -t general` | Local state only. Offline = INFO. No packets are sent and no connectivity check is triggered. Wi-Fi names are never read. DNS search domains are counted, not shown. |
| `logs.boot` | `journalctl --boot=0 --priority=0..3 --output=json --lines=400` | See [logs](#logs) below. |
| `logs.update_units` | `journalctl --boot=0 --unit=<FE-XicoOS unit>` | Errors from FE-XicoOS update and Flatpak services. |

### bootc sources

Doctor tries these in order and says which one it used:
1. A live `bootc status` query.
2. If non-root users are refused, FE-Xico Updater's **root-written** status
   snapshot from this boot (`/run/fe-xicoos-updater/*.json`; only trusted files).
3. Otherwise UNKNOWN, with OSTree runtime markers as evidence and a pointer to
   Updater → Status.

What it reports:
- A **download-only** staged deployment is reported as "will NOT be applied at
  reboot". Doctor never claims a reboot applies it.
- A rollback queued via `rollbackQueued` / `bootOrder` is reported as such.
- Pinned deployments are counted across booted, staged, rollback and
  `otherDeployments`.
- If the spec source differs from the booted image → WARN, or INFO when the
  staged deployment already follows the new source.

### Updater config permissions

A `0777` `/etc/fe-xico-updater/config.ini` is **ignored by the updater**, which
then falls back to the `/usr/etc` image default. Doctor shows this as WARN with
the `chown`/`chmod` you could run. It never changes permissions, never
executes, and never echoes the configured command values.

### Graphics

- NVIDIA Open is told apart from the proprietary module via
  `/proc/driver/nvidia/version` or the module licence.
- Nouveau (+ NVK) is INFO on the standard image and WARN only on the NVIDIA
  Open image (detected by its `80-fe-xico-nvidia-open.conf` policy).
- AMD amdgpu and Intel i915/xe = PASS. Virtual GPUs = INFO. No bound driver = WARN.

### Controllers

- xpad loaded, built in, or present for the **running** kernel → PASS.
- xpad missing and no Xbox-protocol device → INFO (not a fault).
- An Xbox-protocol USB interface (class ff/5d/01, ff/47/d0) with no bound
  driver → WARN. The reason names `kernel-modules-extra-<running kernel>` when
  that package is missing.
- Hyperkin (2e24) devices are listed but **not assumed** to need xpad; HID-mode
  pads work without it.

### Storage

- `storage.layout` / `storage.home`:
  - Read-only `/usr`, `/` and `/sysroot` are normal on bootc.
  - A read-only `/var` or `/etc` → ERROR.
  - A writable `/usr` → INFO (`bootc usr-overlay`).
  - Home must resolve onto persistent writable storage (normally `/var/home`).
- `storage.space`: the same filesystem is reported once.
  - ERROR below 2 GiB.
  - WARN below 5 % or 5 GiB, or below 10 GiB on the filesystem that holds deployments.

### Flatpaks

- Present system- or user-wide → PASS.
- Missing before `default-flatpaks-v4.done` exists → INFO (first boot, or
  installing now).
- Provisioning service failed → WARN.
- Missing after provisioning completed → INFO: probably removed on purpose,
  and FE-XicoOS does not reinstall it.

### Logs

- Critical entries → WARN. Error-level only → INFO, since these are common and
  often harmless.
- No journal files → UNKNOWN.
- Without journal group access, Doctor says that only your own logs are visible
  and reports UNKNOWN rather than a false PASS.

## Safety boundaries (enforced in code and by tests)

- **Read-only.** No sudo/pkexec/Polkit, no bootc upgrade/switch/rollback, no
  rpm-ostree, no package installs, no module loads, no `systemctl
  start/stop/enable/mask/daemon-reload/reset-failed`, no reboot, no chmod,
  no cleanup.
- **Command allowlist with argument patterns.** `doctor_nox/runner.py`
  declares every executable *and* every permitted argument list. Unit names,
  rpm packages, kernel modules and modinfo fields come from fixed sets.
  Anything else raises before a process is created. A test fails if any
  pattern contains a mutating verb.
- **Bounded processes:**
  - no shell and no stdin;
  - minimal environment (no `LD_PRELOAD`, `PYTHONPATH` or tokens);
  - per-command timeout and stdout/stderr byte caps;
  - the whole process group is killed on timeout, overflow or cancel;
  - at most 4 concurrent commands;
  - a 60-second scan deadline.
- **Trusted inputs.** Executables must be root-owned, non-writable and under
  `/usr`. Root-produced state files must be regular, non-symlink, root-owned
  and not group/world-writable before they are believed.
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
- image references such as `ghcr.io/royoshi/fe-xicoos:latest`;
- kernel version, package names and versions, unit names, interface names;
- PCI/USB vendor:product IDs.

The names `root`, `admin`, `user`, `localhost`, `fedora` and the default
hostname `fe-xico` are not replaced, because they would mangle ordinary text.

**Limits:** redaction is pattern-based. A secret in an unusual format, or a
private name inside a log message, can slip through. That is why the sharing
header asks you to review before posting.

## Architecture

```
/usr/bin/doctor-nox            CLI + GUI entry (python3 -I)
/usr/share/doctor-nox/doctor_nox/
  model.py      Finding, severities, categories, report schema v2, validator
  runner.py     allowlist (command + argument patterns), bounded execution
  context.py    filesystem root, env, kernel, identity, trusted-file reads
  probes/       system, updates, security, hardware, storage, software, network, logs
  engine.py     4 workers, isolation, progress, cancellation, deadline
  report.py     text / JSON / redaction / atomic export
  viewmodel.py  Qt-free filtering, grouping, badges, detail HTML (unit-tested)
  theme.py      Qt-free light/dark palettes, status colours, WCAG contrast helper
  gui.py        PySide6 Qt Widgets window, stylesheet, status-pill delegates,
                worker thread, self-test, synthetic-data screenshots
/usr/share/doctor-nox/doctor-nox-report.schema.json
/usr/share/applications/io.github.royoshi.doctornox.desktop
/usr/share/icons/hicolor/<N>x<N>/apps/io.github.royoshi.doctornox.png
```

In this repository the same files live at `bin/`, `doctor_nox/`, `data/`
and `data/icons/`; `packaging/doctor-nox.spec` installs them to the paths
above.

**Why PySide6 Qt Widgets (kept from v0.1), not Kirigami/QML:**
- It is already integrated and uses Fedora's `python3-pyside6` RPM (no pip).
- It gives native KDE theming hooks.
- The logic is testable without a display through `viewmodel.py`.
- A Kirigami rewrite would add QML modules and a second language without a
  matching test benefit.

**Adding a check:**
1. Write `@probe("area.name", "<category>", "Title")` in the right
   `probes/*.py`, returning `Finding`(s).
2. Add any new command to `runner.ALLOWED` with exact argument patterns.
3. Add fixture tests to `tests/test_doctor_nox_probes.py`.

## Tests

```bash
python3 -m unittest discover -s tests                  # all repository tests
python3 -m unittest discover -s tests -p 'test_doctor_nox_probes.py'   # probe fixtures
QT_QPA_PLATFORM=offscreen bin/doctor-nox --gui-self-test   # needs PySide6
```

What the tests cover:
- **Probes:** run on synthetic filesystem roots through a fake runner that
  enforces the real allowlist. Nothing executes bootc/systemctl/journalctl/flatpak.
- **Runner:** tested against a temporary helper script (output cap, timeout,
  cancel killing grandchildren, Unicode, minimal environment).
- **GUI self-test:** uses synthetic probes to load, filter, select, export,
  rescan, ignore a rapid refresh, cancel, and close while scanning.
  - It also switches to Light, Dark and Match system and renders the whole
    window in each.
  - It fails if a delegate raised a paint error or Qt could not parse a
    stylesheet.
- **Themes:** WCAG contrast for every text/background pair in both palettes;
  no neon or OLED-black values; status colours distinct from each other and
  from the accent.
- **Screenshots:** `doctor-nox --gui-screenshot DIR` (hidden QA flag)
  renders the real window with **synthetic** results and runs no host probes.
  - It saves `welcome`, `overview`, `details` and `narrow` PNGs, in both themes.
  - FE-XicoOS's VM workflow runs it offscreen inside the built image and
    uploads the PNGs as the `doctor-nox-screenshots` artifact.
  - That step is informational and can never block validation or publishing.
- **Package (`.github/workflows/ci.yml`, Fedora 45 and 44):** builds the RPM,
  inspects its files and dependencies, installs it with Fedora's signature
  enforcement on, and runs `--list-checks`, the CLI report modes,
  `--gui-smoke-test` and `--gui-self-test` from the installed package as an
  unprivileged user. It also compares the installed package's reports with
  the source tree's.
- **FE-XicoOS image build:** installs the pinned, signed RPM and runs
  `--list-checks`, `--gui-smoke-test` and `--gui-self-test` offscreen. None of
  these touch host state.

### GUI checks and threading rule

FE-XicoOS's `build_files/run-doctor-gui-check.sh` runs `--gui-smoke-test` and
`--gui-self-test` in the image build with:
- `python3 -I -u -X faulthandler`, so output is unbuffered and native crashes
  print every thread's stack;
- an outer `timeout --kill-after`;
- an explanation of the exit status (124 timeout, 134 abort, 137 kill, 139 segfault);
- the last output lines re-emitted as `::error::` annotations;
- disk and memory figures at the moment of failure.

The self-test prints a flushed `[doctor self-test]` checkpoint for every step,
installs a Qt message handler, and dumps all thread stacks if it hangs for
60 s. It never opens modal dialogs.

**Threading rule:**
- `ScanThread` signals are emitted from the scan thread and must be connected
  with `Qt.QueuedConnection` to `@Slot` methods of `Window`. **Never connect
  them to a lambda.** PySide6 runs lambda receivers on the emitting thread, so
  widget code would run off the GUI thread.
- That is what crashed the v1.0 image build.
- `tests/test_doctor_nox.py::GuiThreadRuleTests` enforces this, and each
  slot records any off-thread call in `Window.thread_violations`.

## Runtime verification on FE-ROYO (after a signed image is published)

1. `rpm-ostree status` or `sudo bootc status`: confirm you booted the new image.
2. Launch **Doctor NoX** from the Plasma menu.
   - The window icon should be Doctor's own magenta medical badge.
   - No password prompt should appear.
3. Run `doctor-nox --summary` in Foot, then `echo $?`. Expect 0 unless an
   ERROR is shown.
4. Check **System & bootc**:
   - It matches `sudo bootc status`: booted image, rollback, staged and pinned.
   - If it says "snapshot", open FE-Xico Updater → Status and rescan.
5. Check **Hardware & Gaming**:
   - NVIDIA Open shows "NVIDIA Open kernel driver" and `nvidia-smi` responds.
   - Plug in the Hyperkin controller and confirm the controller finding lists
     it with the right interface/driver.
6. Check **Updates**. The timer states should match
   `systemctl is-enabled fe-xicoos-auto-update.timer bootc-fetch-apply-updates.timer`.
7. Press Scan several times quickly, then close the window during a scan. It
   must close cleanly, without a crash or hang.
8. Export a redacted .txt. Confirm your username, hostname and IPs are absent.
9. Confirm Foot opens, Dolphin F4 opens the embedded terminal, and `konsole`
   is not installed.
