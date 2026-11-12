# From FE-Xico Doctor to Doctor NoX

Doctor NoX started as FE-Xico Doctor 1.0.0 under a new name, imported from
[RoYoshi/Doctor](https://github.com/RoYoshi/Doctor) at commit `5336ac4` with a
fresh history. These names and paths changed:

| What | FE-Xico Doctor | Doctor NoX |
|---|---|---|
| Name shown to people | FE-Xico Doctor | Doctor NoX |
| Repository | `RoYoshi/Doctor` | `RoYoshi/Doctor-NoX` |
| Command | `fe-xico-doctor` | `doctor-nox` |
| RPM package | `fe-xico-doctor` | `doctor-nox` (obsoletes `fe-xico-doctor` < 1.0.1) |
| Application ID, desktop entry and icon name | `io.github.royoshi.fexicodoctor` | `io.github.royoshi.doctornox` |
| Python package | `fexico_doctor` in `/usr/lib/fe-xicoos/` | `doctor_nox` in `/usr/share/doctor-nox/` |
| Report schema file | `/usr/share/fe-xicoos/fe-xico-doctor-report.schema.json` | `/usr/share/doctor-nox/doctor-nox-report.schema.json` |
| Report schema ID (`schema`) | `fe-xico-doctor/report` v2 | `doctor-nox/report` v3 (see below) |
| Theme and font variables | `FE_XICO_DOCTOR_THEME`, `FE_XICO_DOCTOR_FONT` | `DOCTOR_NOX_THEME`, `DOCTOR_NOX_FONT` |
| Public signing key file | `packaging/RPM-GPG-KEY-fe-xico-doctor` | `packaging/RPM-GPG-KEY-doctor-nox` (same key) |

The app icon is still the magenta medical badge, under its new name, until the
new Doctor NoX icon is ready.

## From FE-XicoOS-only to every Linux

Before its first release Doctor NoX was made to work on every Linux. The
checks that only made sense on FE-XicoOS were removed, and the rest work on
the Fedora, Debian/Ubuntu, Arch and openSUSE families, on image-based OSTree
systems, and in general form anywhere else.

| FE-Xico Doctor check | In Doctor NoX |
|---|---|
| `system.bootc.*` | `system.image.*`: read from bootc, or from `rpm-ostree status` when bootc needs root, on any OSTree system; adds `system.image.local_changes` |
| `system.identity` (FE-XicoOS edition, WARN on other systems) | INFO on every distribution: family, image type, package tools, init, security module |
| `updates.timers`, `updates.active_hours`, `updates.results`, `updates.updater`, `updates.notifier` | removed (FE-Xico Updater); `updates.automatic` reports automatic updates for every family, FE-XicoOS's timer included |
| | new: `updates.reboot`, `updates.packages`, `updates.config_files`, `updates.last_update` |
| `security.selinux` (disabled = ERROR) | `security.mac`: SELinux or AppArmor, judged by what the family turns on by default |
| `security.state_permissions` | removed (FE-Xico Updater state) |
| | new: `security.firewall` |
| `hardware.graphics` (NVIDIA Open image policy) | Nouveau is WARN only when the NVIDIA driver is installed for the running kernel |
| `software.flatpaks`, `software.core_apps` | removed (FE-XicoOS defaults); `software.flatpak` and `software.snap` |
| `logs.update_units` | removed (FE-XicoOS services) |
| `storage.*` (bootc layout) | what is normal follows the kind of system; `/boot` free space added |

The report schema is v3: `host` describes the distribution (`family`,
`image`, `package_managers`, …) instead of `edition` and `image_variant`, and
`not_applicable` lists the checks that do not apply to the system.

## What FE-XicoOS changes when it switches

FE-XicoOS installs a pinned, signed release (`build_files/doctor/` in FE-Xico).
To switch, replace that pin with a Doctor NoX release and update every check
that names the old command, application ID, icon, package or paths above, and
anything that reads the old check IDs or the v2 report fields.
