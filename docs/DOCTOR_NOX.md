# Doctor NoX · v1.0

*Doctor NoX is in the house!* It is a doctor for your Linux PC: it examines,
diagnoses and prescribes, and never operates. It is **read-only** towards the system. It
never changes packages, deployments, services, drivers or settings, never needs
root and never makes network requests. It writes only a report you explicitly
save and two small files of its own: the conditions found at your last checkup
([Follow-ups](#follow-ups-the-last-checkup)) and your
[settings](#settings).

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
| The Flatpak | `flatpak run io.github.royoshi.doctornox …` (the checks run on the host; see [The Flatpak](#the-flatpak)) |

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

Doctor NoX speaks as a doctor would. A **checkup** runs every **test** that
applies to this PC. Tests are grouped into **areas**. Each result has a
**condition**, and may have a **diagnosis**, **what I found** and a
**treatment**. Doctor NoX examines and prescribes; it never operates, so every
treatment is yours to carry out.

The window's words for the report's severities (`--text`, `--summary` and JSON
keep the severities, see [Severity](#severity--what-each-level-promises)):

| Condition | Severity | Means |
|---|---|---|
| **Needs treatment** | ERROR | a problem that is likely to break something or put the system at risk |
| **Keep an eye on it** | WARN | not urgent, but it may need care |
| **Couldn't examine** | UNKNOWN | a missing tool, permission or unsupported format; never a clean bill of health |
| **Note** | INFO | worth knowing, not a problem |
| **Healthy** | PASS | examined and healthy |

- **Header:** the Doctor NoX icon, the distribution · image type · kernel, and
  the overall condition as a coloured pill with a plain-language headline:
  - **Needs treatment**: at least one test needs treatment;
  - **Needs attention**: at least one to keep an eye on;
  - **Couldn't examine everything**: at least one test couldn't be examined;
  - **In good health**: otherwise.

  The time and duration of the last checkup sit under the pill.
- **Heart monitor:** between the name and the pill, an ECG trace in the overall
  condition's colour. While a checkup runs, a bright head sweeps along it as on
  a bedside monitor. It is decoration: the condition is always written beside
  it, and the sweep can be turned off in Settings.
- **What's wrong?** Tell the doctor the complaint, in your own words or from
  the list.
  - **Type it** ("no sound", "my wifi keeps dropping", "games stutter") and
    press Enter. Doctor NoX matches your words against the complaints it knows,
    on this PC; nothing is sent anywhere. With no match it shows the whole
    checkup and suggests sharing the doctor's note where you ask for help.
  - **Or pick one:** *It's slow or freezes*, *No sound*, *No internet*,
    *Updates won't install*, *Graphics or games act up*, *A controller isn't
    working*, *It runs hot or loud*, *Running out of space*, *An app won't
    install or open*, *Is my PC secure?*, *Something crashes or won't start*,
    or *Just a checkup*.
  - The findings list then shows the tests that bear on the complaint, and the
    verdict speaks to it: what those tests found, and what needs a look
    elsewhere.
  - Every checkup still runs every test; the complaint only chooses what is
    shown. *Just a checkup* shows them all.
  - When the tests for a complaint all come back fine, the verdict says the
    cause is somewhere Doctor NoX can't see, rather than that nothing is wrong,
    and suggests sharing the doctor's note.
- **Start checkup / Cancel:** a checkup runs on a worker thread, so the window
  never blocks. The primary button reads **New checkup** after the first one.
  - Starting one while another is running is ignored.
  - Cancel stops in-flight commands. Unfinished tests become **Couldn't
    examine** and the checkup is marked as stopped early.
  - Closing the window mid-checkup cancels it and closes once the worker has
    stopped. A running thread is never destroyed.
- **Copy doctor's note:** copies a **redacted** note: the verdict, what changed
  since the last checkup, and every finding that isn't simply healthy, each
  with its diagnosis and treatment.
- **Export:**
  - redacted report for sharing (.txt / .json);
  - full local report (.json / .txt), saved with mode 0600;
  - copy the full report to the clipboard.
- **Menu:** **Settings…**, **Appearance** (Match system / Light / Dark) and
  **About**.
- **Vital signs:** four readings from the last checkup, in the fixed-width
  font of a monitor, each edged in its condition's colour. A click opens the
  test behind a reading.

  | Vital sign | Shows | From |
  |---|---|---|
  | Temperature | the hottest sensor | `hardware.sensors` |
  | Memory | memory free of the total | `hardware.cpu_memory` |
  | Disk space | the filesystem that is worst off | `storage.space` |
  | Restart | whether a restart is waiting | `updates.reboot`, or `system.image.pending` on image-based systems |

  A sign no test in the checkup measured reads **Not examined**; one that
  couldn't be read reads **Couldn't read**.
- **Find and conditions:** free text across title, finding, what was found and
  treatment.
  - Also `is:problem`, and a condition by either name: `is:error` or
    `is:treat`, `is:warn` or `is:watch`, `is:unknown`, `is:info` or `is:note`,
    `is:pass` or `is:healthy`.
  - The condition chips (each with its count and a coloured dot) show or hide
    the findings in that condition.
- **Areas examined (left):** Diagnosis, System, Updates & Packages, Security &
  Services, Hardware & Gaming, Storage, Apps, Network, Logs.
  - An area with problems shows a pill with how many of its tests need
    treatment, an eye kept on them, or couldn't be examined, coloured by the
    worst one.
  - An area where every test came back fine shows a green **OK** pill.
- **Findings (centre):** sorted most-urgent first, with a condition pill per
  row and alternating row shading. The condition is always spelled out in
  text, never conveyed by colour alone. After a follow-up, a **Since last
  checkup** column marks what got **Worse** or **Better**.
- **Diagnosis and treatment (right, or below on narrow windows):** for the
  selected test:
  - its condition, the finding and what that condition means;
  - whether it got better or worse since the last checkup;
  - **Diagnosis** (why, when there is a reason to give);
  - **What I found**, in a monospace block;
  - **Treatment**, or "None needed" for a healthy test. Doctor NoX never
    carries these steps out itself.

  With no test selected, the pane shows the diagnosis for the whole checkup:
  - the headline and the **verdict**, a few sentences saying what needs
    treatment, what to keep an eye on, where to start, and what couldn't be
    examined;
  - **Since your last checkup**, when there is one to compare with;
  - every area's worst condition, the tests that don't apply here, and what
    the conditions mean.
- **Status bar:** progress and results messages on the left. The right side
  always shows "Examines only · no root · no network requests · nothing
  uploaded or changed".
- **Keyboard:**

  | Key | Action |
  |---|---|
  | Ctrl+L | type what's wrong |
  | Ctrl+F | find in the findings |
  | F5 or Ctrl+R | start a checkup |
  | Esc | cancel, or clear the search |
  | Ctrl+Shift+C | copy the doctor's note |
  | Ctrl+S | save redacted report |
  | Ctrl+, | settings |
  | F1 | about |
  | Ctrl+Q | quit |

  Arrow keys move through areas and findings. Every interactive widget has an
  accessible name, and keyboard focus is shown as an accent-coloured outline.
  The vital signs are a pointer shortcut; the tests behind them are in the
  findings list.

### Settings

**Menu → Settings…** (Ctrl+,). Nothing changes until OK, and **Restore
Defaults** puts everything back.

| Setting | Default | What it does |
|---|---|---|
| Appearance | Match system | Match system follows the desktop's light or dark setting and switches when it does; Light and Dark keep one theme |
| Animate the heart monitor | on | whether the trace sweeps during a checkup |
| Start a checkup when Doctor NoX opens | on | off: the window opens and waits for **Start checkup** |
| Remember my last checkup | on | follow-ups; turning it off deletes the remembered checkup |
| Stop a checkup after | 60 seconds | 5 to 300, the same limits as `--deadline` |
| Areas to examine | all | an area you turn off is left out of every checkup; the diagnosis lists what was left out |

They are kept in `$XDG_CONFIG_HOME/doctor-nox/settings.json`
(`~/.config/doctor-nox/settings.json` unless you set it otherwise), readable
only by you. Only the window reads or writes them. A missing or damaged file is
never an error: every value that can't be used falls back to its default.

### Follow-ups: the last checkup

To say what changed, the window remembers the last checkup that finished:

- **Where:** `$XDG_STATE_HOME/doctor-nox/last-checkup.json`, which is
  `~/.local/state/doctor-nox/last-checkup.json` unless you set it otherwise.
  The file is readable only by you (mode 0600).
- **What:** the time of the checkup and one condition per test ID. No
  evidence, names, addresses or log lines.
- **When:** only the window writes it, after a checkup that finished. A
  checkup that was stopped early is neither remembered nor compared. The
  command line, the self-tests and screenshot mode never write it.
- **How it is used:** the next checkup is compared with it. A test that got
  more or less urgent is **Worse** or **Better**. One that couldn't be
  examined this time is reported as that, never as better.
- **Turning it off:** Settings → *Remember my last checkup*. Off deletes the
  file and nothing is remembered.
- Deleting the file yourself is safe too: the next checkup has nothing to
  compare with and starts a new record.

### Appearance

Doctor has a light and a dark theme. They are designed separately, not
inverted, and both live in `doctor_nox/theme.py`, which has no Qt
dependency.

NoX is Latin for night, so the dark theme is the night shift in a clinic:
deep night-blue surfaces, moonlight text, and status colours that glow softly
like monitor lights. The light theme is first light: cool paper and indigo ink.

- **Choosing:** by default Doctor NoX follows the desktop's colour scheme
  (Qt's colour-scheme hint, else the desktop palette) and switches live when it
  changes. Choose a theme under **Menu → Settings** or **Menu → Appearance**;
  the choice is remembered. `DOCTOR_NOX_THEME=light|dark|system`, when set,
  decides instead.
- **Accent:** moonlit lavender (`#b5a8ff`) in the dark theme and indigo
  (`#4a3cb5`) in the light one, used **only** for interaction: the primary
  button, focus, selection and progress. No status colour is close to it.
- **Status colours:** used only for results, so a selected row can never look
  like a result. Each status has a text colour, a soft tint and a border:

  | Status | Light text | Dark text |
  |---|---|---|
  | Error | `#b3261e` | `#ff8e8b` |
  | Warning | `#875200` | `#f1c35b` |
  | Unknown | `#54587a` | `#b1b4cc` |
  | Info | `#1f5fa8` | `#7db4ff` |
  | Pass | `#18723f` | `#6cd4a1` |

- **Contrast:** tests check every text/background pair against WCAG 2.x:
  - body text at least 7:1;
  - secondary and status text at least 4.5:1, including on tints and the
    selection colour;
  - the focus accent at least 3:1.
- **Shape:** night-blue (dark) or cool paper (light) surfaces with 1 px
  borders, 6–8 px rounded corners, rounded status pills, and no animations.
- **The NoX mark:** a crescent moon cradling a medical cross, drawn from
  geometry by `gui.nox_mark()` in the theme's accent and text colours. It
  appears on the empty results panel before the first scan.
- **Wording:** sentence-case section labels, counts written as words
  ("1 error, 2 warnings … and 6 passed"), and commas rather than dotted
  "A · B · C" strings in the window's own header and labels. Check results
  keep each check's own wording.
- **Fonts:** your desktop's UI font (or `DOCTOR_NOX_FONT` if that family is
  installed). Check IDs and evidence use the desktop's fixed-width font.
- **Native dialogs:** a matching `QPalette` is applied, so native dialogs and
  menus follow the theme.
- **Icon:** `io.github.royoshi.doctornox` is the NoX mark on an indigo night
  tile.
  - It ships as raster PNGs at 16–512 px in `hicolor`, drawn by
    `python3 tools/build_doctor_icon.py` (needs Pillow) from the mark's
    geometry in `theme.py`, the same geometry the window uses. Sizes up to
    24 px use a larger, bolder mark so the cross survives.
  - The mark is optically centred: the tile's centre sits midway between the
    mark's outline centre and its centre of mass (1.4% of the tile each way,
    along its diagonal). It fills 63% of the tile, and the crescent's tips are
    rounded to match the cross's corners.
  - The tile's margin and the cross's arms land on whole pixels at every size,
    with arms of equal parity so the cross stays symmetric. From 48 px up a
    faint moonglow and a faint light along the top edge add depth; smaller
    sizes stay flat.
  - The earlier icon, RoYoshi's magenta medical badge, is in the repository
    history: `git show bb6ae57:tools/doctor-nox-icon-source.png > badge.png`.
  - If the icon is missing, the window draws the same icon itself, without
    the glow and edge light. There is never a generic or Tux fallback.

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
- **Three things are ever written,** all in your own files and all atomically:
  a report where you choose to save it (`report.py`), the window's record of
  the last checkup (`record.py`) and your settings (`settings.py`). A test
  checks that no other module writes.
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
- **The Flatpak's one host command.** From its sandbox the Flatpak starts
  only `flatpak-spawn --host --watch-bus /usr/bin/python3 -I -B
  <app folder>/share/doctor-nox/doctor_nox/hostrun.py`, followed by either
  `--list-checks` or `--json --deadline N` plus `--only ID` pairs: Doctor's
  own read-only modes (`runner.validate_host_doctor`). Anything else raises
  before a process is created. Inside the sandbox root's files show up as
  owned by the overflow user, so ownership proves nothing there:
  `flatpak-spawn` is trusted when it is under `/usr` on a read-only mount and
  not group/world-writable. On the host the scan is the ordinary one, with
  the same allowlist.
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
  clinic.py     Qt-free doctor's side: verdict, vital signs, complaints,
                follow-ups, the doctor's note (unit-tested)
  record.py     the last checkup's conditions, for follow-ups
  settings.py   your settings for the window (unit-tested)
  theme.py      Qt-free light/dark palettes, status colours, WCAG contrast helper
  gui.py        PySide6 Qt Widgets window, stylesheet, status-pill delegates,
                worker thread, self-test, synthetic-data screenshots
  cli.py        the command line; also the `doctor-nox` entry point
  flatpak.py    the Flatpak build: recognise it, run the scan on the host,
                explain in one finding when that is not possible
  hostrun.py    what the host's Python starts in the Flatpak build: checks
                for Python 3.10, then runs cli.py
```

How it is installed:

| Format | Python package | Command |
|---|---|---|
| RPM (Fedora, openSUSE) | `/usr/share/doctor-nox/doctor_nox/`, with bytecode | `bin/doctor-nox` → `/usr/bin/doctor-nox` |
| .deb, Arch, pip, pipx | the Python module directory (from the wheel) | the `doctor-nox` entry point |
| Flatpak | `/app/share/doctor-nox/doctor_nox/`, with bytecode | `bin/doctor-nox` → `/app/bin/doctor-nox` |

Every format installs `/usr/share/applications/io.github.royoshi.doctornox.desktop`,
`/usr/share/metainfo/io.github.royoshi.doctornox.metainfo.xml`,
`/usr/share/doctor-nox/doctor-nox-report.schema.json` and the hicolor icons
(pip and pipx under their own prefix, the Flatpak under `/app`).

### The Flatpak

Inside its sandbox Doctor would see the runtime's `/usr` and `/etc`, its own
private `/var` and `/run`, and only its own processes and mounts: nothing of
the computer it is meant to check. So the Flatpak
(`packaging/flatpak/io.github.royoshi.doctornox.yml`) runs the scan on the
host:

1. `flatpak.sandbox_app_path()` recognises Doctor's own Flatpak: its code is
   under `/app`, and `/.flatpak-info`, which Flatpak itself writes, names
   `io.github.royoshi.doctornox`. That file also gives the app's folder as
   the host sees it.
2. `engine.scan()` and `--list-checks` hand over to `flatpak.scan_on_host()`
   and `list_checks_on_host()`. These start the host command described under
   [Safety boundaries](#safety-boundaries-enforced-in-code-and-by-tests),
   which needs the `org.freedesktop.Flatpak` permission.
3. `hostrun.py` runs on the host's `/usr/bin/python3`. It stops with exit
   status 64 on anything older than 3.10, and is written so that older
   Pythons can still read it and say why. Then it runs Doctor's ordinary
   command line from the app's folder: the same checks, allowlist and
   read-only rules as a native install, without root.
4. The window and the command line show the host's report. `--watch-bus`
   ends the host process when the Flatpak's side is stopped (timeout or
   cancel).

Apart from that permission the Flatpak asks only for the display (Wayland,
or X11 as a fallback) and IPC: no network and no file access. But the
permission itself lets an app run anything on the host as you, so the
sandbox is not what keeps Doctor read-only. Doctor's own code is: the one
host command above, and the ordinary allowlist on the host.

When no report comes back, the scan shows one Unknown finding,
`system.host_access` ("Access to this computer"), instead of the sandbox's
state:

| Cause | Summary shown |
|---|---|
| the permission is off (Flatseal, the desktop's app permissions, `flatpak override`) | Doctor cannot check this computer: running commands outside the sandbox is off. The guidance gives the command that turns it back on. |
| no `/usr/bin/python3`, or one older than 3.10 | Doctor needs Python 3.10 or later on this computer |
| `flatpak-spawn` missing or untrusted | Doctor cannot reach this computer from the Flatpak |
| the host command could not start | The check on the host could not start |
| timeout or cancel | Not completed (the report is marked incomplete) |
| anything else | The check on the host failed, with the end of its error output as evidence |

The About dialog says that the Flatpak's checks run on the host.

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
- **Flatpak (`tests/test_doctor_nox_flatpak.py`):**
  - only Doctor's own Flatpak is recognised;
  - the exact host command line, and refusal of any other;
  - an untrusted `flatpak-spawn` is never run;
  - each failure becomes the one Unknown finding;
  - the engine and the command line hand over to the host;
  - `hostrun.py` refuses old Pythons, and is checked against Python 3.6's
    grammar.
- **The doctor's side:** the verdict's sentences, complaints (every test a
  complaint or vital sign names must exist), follow-ups (a test that can't be
  examined is never "better"), the doctor's note, and the record (private,
  atomic, finished checkups only, written by the window alone). Vital signs
  are read from the real probes' results, so a reworded probe fails a test.
- **GUI self-test:** uses synthetic probes to load, filter, select, export,
  rescan, ignore a rapid refresh, cancel, and close while scanning.
  - It checks the doctor's wording, the verdict, the vital signs, a complaint
    (picked and typed), the heart monitor at rest and sweeping, and a
    follow-up against a remembered checkup in a temporary directory.
  - It changes every setting, checks each takes effect and is saved, runs a
    checkup with an area turned off, and restores the defaults.
  - It checks that every control fits in the window.
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
- **Flatpak (`flatpak` job):** builds the Flatpak with `flatpak-builder` in a
  Fedora 45 container and installs it. It then runs:
  - `--version`, and the GUI tests offscreen;
  - a scan through `flatpak-spawn --host`, which must validate and describe
    the Fedora container, not the sandbox;
  - `--list-checks` from the host;
  - a run with the permission removed, which must be the one Unknown
    finding.
- **Oldest Python (`oldest-python` job):** the unit tests on Python 3.10, the
  oldest that Doctor supports.

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
