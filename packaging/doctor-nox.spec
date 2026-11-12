# Doctor NoX: read-only system health checks for Linux (Python 3 + PySide6).
# Built from a source tarball of this repository (packaging/build-rpm.sh).
# Version comes from the VERSION file; packaging/build-rpm.sh checks they agree.
# Builds on Fedora (and RHEL-family) and on openSUSE (%%{suse_version}).

Name:           doctor-nox
Version:        1.0.0
Release:        1%{?dist}
Summary:        Read-only system health checks for Linux

License:        Apache-2.0
URL:            https://github.com/RoYoshi/Doctor-NoX
Source0:        %{name}-%{version}.tar.gz

BuildArch:      noarch

%global doctor_pyver %(%{__python3} -c 'import sys; print("%%d.%%d" %% sys.version_info[:2])')

%if 0%{?suse_version}
BuildRequires:  python-rpm-macros
BuildRequires:  python3
%else
BuildRequires:  python3-devel
%endif
BuildRequires:  desktop-file-utils

# Runtime: the Python standard library, and PySide6 for the desktop window.
# Every diagnostic command (systemctl, journalctl, rpm, rpm-ostree, bootc,
# flatpak, lspci, nvidia-smi, ...) is optional: a missing tool is reported as
# Unknown, and checks for other kinds of system do not run.
Requires:       python3
%if 0%{?suse_version}
# openSUSE names Python modules by interpreter flavour (python313-pyside6).
Requires:       %{?primary_python}%{!?primary_python:python3}-pyside6
%else
Requires:       python3-pyside6
%endif
Requires:       hicolor-icon-theme
# The shipped bytecode is for this distribution's Python. Worked out here
# because Fedora and openSUSE define different Python macros.
Requires:       python(abi) = %{doctor_pyver}

# Doctor NoX was called FE-Xico Doctor (package fe-xico-doctor) up to 1.0.0.
Obsoletes:      fe-xico-doctor < 1.0.1

%description
Doctor NoX looks at the parts of a Linux system that tend to go wrong and
explains what it finds: pending restarts, the package database, leftover
config files, automatic updates, image deployments on Fedora Atomic, bootc
and other OSTree systems, SELinux or AppArmor, the firewall, failed services,
graphics drivers, game controllers, disk space, Flatpak and Snap, the network
and boot errors.

It is strictly read-only: it never needs root, never changes the system and
never makes network requests. It has a desktop window and a command line with
text, JSON (report schema v3) and redacted reports. It knows the Fedora,
Debian/Ubuntu, Arch and openSUSE families and runs its general checks on any
other Linux.

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
# Ship the bytecode: /usr is read-only on image-based systems, so it cannot be
# written later.
%if 0%{?suse_version}
%{__python3} -m compileall -q -o 0 -o 1 -s %{buildroot} -p / %{buildroot}%{_datadir}/%{name}/doctor_nox
%else
%py_byte_compile %{__python3} %{buildroot}%{_datadir}/%{name}/doctor_nox
%endif
install -Dpm 0644 data/io.github.royoshi.doctornox.desktop \
    %{buildroot}%{_datadir}/applications/io.github.royoshi.doctornox.desktop
install -Dpm 0644 data/io.github.royoshi.doctornox.metainfo.xml \
    %{buildroot}%{_datadir}/metainfo/io.github.royoshi.doctornox.metainfo.xml
install -Dpm 0644 data/doctor-nox-report.schema.json \
    %{buildroot}%{_datadir}/%{name}/doctor-nox-report.schema.json
for size in 16 22 24 32 48 64 128 256 512; do
    install -Dpm 0644 data/icons/hicolor/${size}x${size}/apps/io.github.royoshi.doctornox.png \
        %{buildroot}%{_datadir}/icons/hicolor/${size}x${size}/apps/io.github.royoshi.doctornox.png
done

%check
desktop-file-validate %{buildroot}%{_datadir}/applications/io.github.royoshi.doctornox.desktop
%{__python3} -c 'import json, sys; json.load(open(sys.argv[1]))' \
    %{buildroot}%{_datadir}/%{name}/doctor-nox-report.schema.json
# Source-tree tests (probe fixtures, runner, report, view model, safety audit).
# GUI tests skip here when PySide6 is absent from the build root; CI runs them
# against the installed package.
PYTHONDONTWRITEBYTECODE=1 %{__python3} -m unittest discover -s tests

%files
%license LICENSE
%doc README.md docs/DOCTOR_NOX.md
%{_bindir}/doctor-nox
%dir %{_datadir}/%{name}
%{_datadir}/%{name}/doctor_nox/
%{_datadir}/%{name}/doctor-nox-report.schema.json
%{_datadir}/applications/io.github.royoshi.doctornox.desktop
%{_datadir}/metainfo/io.github.royoshi.doctornox.metainfo.xml
%{_datadir}/icons/hicolor/*/apps/io.github.royoshi.doctornox.png

%changelog
* Thu Nov 12 2026 RoYoshi <99851152+RoYoshi@users.noreply.github.com> - 1.0.0-1
- First Doctor NoX release, for every Linux. Checks for the Fedora (including
  Atomic and bootc), Debian/Ubuntu, Arch and openSUSE families, general
  checks everywhere else, and a list of checks that do not apply. Report
  schema doctor-nox/report v3. Grew out of FE-Xico Doctor 1.0.0; the
  FE-XicoOS-only checks are gone.
