# From FE-Xico Doctor to Doctor NoX

Doctor NoX 1.0.0 is FE-Xico Doctor 1.0.0 under a new name, imported from
[RoYoshi/Doctor](https://github.com/RoYoshi/Doctor) at commit `5336ac4` with a
fresh history. Its checks, results, exit codes and report contents are
unchanged. Only these names and paths changed:

| What | FE-Xico Doctor | Doctor NoX |
|---|---|---|
| Name shown to people | FE-Xico Doctor | Doctor NoX |
| Repository | `RoYoshi/Doctor` | `RoYoshi/Doctor-NoX` |
| Command | `fe-xico-doctor` | `doctor-nox` |
| RPM package | `fe-xico-doctor` | `doctor-nox` (obsoletes `fe-xico-doctor` < 1.0.1) |
| Application ID, desktop entry and icon name | `io.github.royoshi.fexicodoctor` | `io.github.royoshi.doctornox` |
| Python package | `fexico_doctor` in `/usr/lib/fe-xicoos/` | `doctor_nox` in `/usr/share/doctor-nox/` |
| Report schema file | `/usr/share/fe-xicoos/fe-xico-doctor-report.schema.json` | `/usr/share/doctor-nox/doctor-nox-report.schema.json` |
| Report schema ID (`schema`) | `fe-xico-doctor/report` v2 | `doctor-nox/report` v2 (same fields) |
| Theme and font variables | `FE_XICO_DOCTOR_THEME`, `FE_XICO_DOCTOR_FONT` | `DOCTOR_NOX_THEME`, `DOCTOR_NOX_FONT` |
| Public signing key file | `packaging/RPM-GPG-KEY-fe-xico-doctor` | `packaging/RPM-GPG-KEY-doctor-nox` (same key) |

The app icon is still the magenta medical badge, under its new name, until the
new Doctor NoX icon is ready.

## What FE-XicoOS changes when it switches

FE-XicoOS installs a pinned, signed release (`build_files/doctor/` in FE-Xico).
To switch, replace that pin with a Doctor NoX release and update every check
that names the old command, application ID, icon, package or paths above.
