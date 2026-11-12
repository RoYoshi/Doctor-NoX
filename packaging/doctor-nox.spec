# Doctor NoX: read-only system-health diagnostics (Python 3 + PySide6).
# Built from a source tarball of this repository (packaging/build-rpm.sh).
# Version comes from the VERSION file; packaging/build-rpm.sh checks they agree.

Name:           doctor-nox
Version:        1.0.0
Release:        1%{?dist}
Summary:        Read-only system health diagnostics for FE-XicoOS

License:        Apache-2.0
URL:            https://github.com/RoYoshi/Doctor-NoX
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

BuildRequires:  python3-devel
BuildRequires:  desktop-file-utils

# Runtime: the Python standard library and Fedora's PySide6 (Qt Widgets GUI).
# Every diagnostic command (bootc, systemctl, journalctl, rpm, flatpak, lspci,
# nvidia-smi, ...) is optional: a missing tool is reported as Unknown.
Requires:       python3
Requires:       python3-pyside6
Requires:       hicolor-icon-theme
# The shipped bytecode is for this Fedora release's Python.
Requires:       python(abi) = %{python3_version}

# Doctor NoX was called FE-Xico Doctor (package fe-xico-doctor) up to 1.0.0.
Obsoletes:      fe-xico-doctor < 1.0.1

%description
Doctor NoX checks bootc deployments, updates, services, SELinux, graphics
drivers, controllers, storage, Flatpaks, network and logs, and explains what
it finds. It is strictly read-only: it never needs root, never changes the
system and never makes network requests. It has a desktop window and a
command-line interface with text, JSON (schema v2) and redacted reports.

%prep
%autosetup -n %{name}-%{version}

%build
# Pure Python: nothing to compile here (bytecode is generated in %%install).

%install
install -Dpm 0755 bin/doctor-nox %{buildroot}%{_bindir}/doctor-nox
# A private Python package: Doctor NoX owns /usr/share/doctor-nox.
install -d %{buildroot}%{_datadir}/%{name}
cp -pr doctor_nox %{buildroot}%{_datadir}/%{name}/
find %{buildroot}%{_datadir}/%{name}/doctor_nox -type d -exec chmod 0755 {} +
find %{buildroot}%{_datadir}/%{name}/doctor_nox -type f -exec chmod 0644 {} +
# /usr is read-only on FE-XicoOS; ship the bytecode.
%py_byte_compile %{python3} %{buildroot}%{_datadir}/%{name}/doctor_nox
install -Dpm 0644 data/io.github.royoshi.doctornox.desktop \
    %{buildroot}%{_datadir}/applications/io.github.royoshi.doctornox.desktop
install -Dpm 0644 data/doctor-nox-report.schema.json \
    %{buildroot}%{_datadir}/%{name}/doctor-nox-report.schema.json
for size in 16 22 24 32 48 64 128 256 512; do
    install -Dpm 0644 data/icons/hicolor/${size}x${size}/apps/io.github.royoshi.doctornox.png \
        %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/io.github.royoshi.doctornox.png
done

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.royoshi.doctornox.desktop
%{python3} -c 'import json, sys; json.load(open(sys.argv[1]))' \
    %{buildroot}%{_datadir}/%{name}/doctor-nox-report.schema.json
# Source-tree tests (probe fixtures, runner, report, view model, safety audit).
# GUI tests skip here when PySide6 is absent from the build root; CI runs them
# against the installed package.
PYTHONDONTWRITEBYTECODE=1 %{python3} -m unittest discover -s tests

%files
%license LICENSE
%doc README.md docs/DOCTOR_NOX.md
%{_bindir}/doctor-nox
%dir %{_datadir}/%{name}
%{_datadir}/%{name}/doctor_nox/
%{_datadir}/%{name}/doctor-nox-report.schema.json
%{_datadir}/applications/io.github.royoshi.doctornox.desktop
%{_datadir}/icons/hicolor/*/apps/io.github.royoshi.doctornox.png

%changelog
* Thu Nov 12 2026 RoYoshi <99851152+RoYoshi@users.noreply.github.com> - 1.0.0-1
- First Doctor NoX release: FE-Xico Doctor 1.0.0 under its new name, from
  RoYoshi/Doctor commit 5336ac4. Command doctor-nox, application ID
  io.github.royoshi.doctornox, files in /usr/share/doctor-nox, report schema
  doctor-nox/report v2. Checks and results are unchanged.
