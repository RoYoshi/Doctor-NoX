#!/usr/bin/env bash
# Checks an installed doctor-nox package as an unprivileged user, and
# compares its reports with the source tree's. Used by ci.yml and release.yml
# inside throwaway containers. Reads the system; changes nothing.
#
#   packaging/ci/check-installed.sh SOURCE_TREE [rpm|deb|arch]
#
# KIND defaults to rpm (the Fedora and openSUSE package).
set -uo pipefail
src="${1:?usage: check-installed.sh SOURCE_TREE [rpm|deb|arch]}"
kind="${2:-rpm}"
user=doctorcheck
status=0
note()  { printf '::notice title=%s::%s\n' "$1" "$2"; }
fail()  { printf '::error title=%s::%s\n' "$1" "$2"; status=1; }
as_user() { runuser -u "$user" -- env -i HOME="/home/$user" PATH=/usr/bin:/usr/sbin LANG=C.UTF-8 QT_QPA_PLATFORM=offscreen "$@"; }

id "$user" >/dev/null 2>&1 || useradd -m "$user"
work="$(mktemp -d)"; chmod 0755 "$work"

# Package integrity.
case "$kind" in
    rpm)  verify=(rpm -V doctor-nox) ;;
    deb)  verify=(dpkg --verify doctor-nox) ;;
    arch) verify=(pacman -Qkk doctor-nox) ;;
    *)    echo "check-installed: unknown package kind $kind" >&2; exit 2 ;;
esac
if out="$("${verify[@]}" 2>&1)"; then note "${verify[*]}" "installed files match the package"
else fail "${verify[*]}" "$(head -c 600 <<<"$out")"; fi

# Where the Python package lives: the RPM keeps it private in /usr/share/doctor-nox;
# the Debian and Arch packages install it like any Python module.
if [[ -d /usr/share/doctor-nox/doctor_nox ]]; then
    pkgdir=/usr/share/doctor-nox/doctor_nox
    pypath=/usr/share/doctor-nox
else
    pkgdir="$(python3 -I -c 'import doctor_nox, os; print(os.path.dirname(doctor_nox.__file__))' 2>/dev/null)"
    pypath=""
fi
[[ -n "$pkgdir" && -f "$pkgdir/cli.py" ]] && note "Package location" "$pkgdir" || fail "Package location" "doctor_nox not found"
for path in /usr/bin/doctor-nox "$pkgdir/gui.py" "$pkgdir/distro.py" \
            /usr/share/applications/io.github.royoshi.doctornox.desktop \
            /usr/share/metainfo/io.github.royoshi.doctornox.metainfo.xml \
            /usr/share/doctor-nox/doctor-nox-report.schema.json \
            /usr/share/icons/hicolor/48x48/apps/io.github.royoshi.doctornox.png \
            /usr/share/icons/hicolor/512x512/apps/io.github.royoshi.doctornox.png; do
    [[ -e "$path" ]] || fail "Installed files" "missing $path"
done
pyc="$(find "$pkgdir" -name "*.cpython-$(python3 -c 'import sys; print(f"{sys.version_info[0]}{sys.version_info[1]}")')*.pyc" | wc -l)"
[[ "$pyc" -gt 0 ]] && note "Bytecode" "$pyc .pyc files for this Python" || fail "Bytecode" "no bytecode for this Python"
desktop-file-validate /usr/share/applications/io.github.royoshi.doctornox.desktop || fail "Desktop entry" "desktop-file-validate failed"

# CLI as an unprivileged user.
version="$(as_user doctor-nox --version 2>&1)"; [[ "$version" == "Doctor NoX "* ]] && note "--version" "$version" || fail "--version" "$version"
as_user doctor-nox --list-checks >"$work/installed-checks.txt" 2>"$work/err" || fail "--list-checks" "$(head -c 400 "$work/err")"
for mode in --json --text --summary "--text --redact" "--json --redact"; do
    out="$work/report$(tr ' -' '__' <<<"$mode")"
    # shellcheck disable=SC2086
    as_user doctor-nox $mode --deadline 60 >"$out" 2>"$out.err"; rc=$?
    if [[ $rc -eq 0 || $rc -eq 1 ]]; then note "doctor-nox $mode" "exit $rc as an unprivileged user ($(wc -c <"$out") bytes)"
    else fail "doctor-nox $mode" "exit $rc: $(head -c 400 "$out.err")"; fi
done
as_user doctor-nox --deadline 2 --json >/dev/null 2>&1; rc=$?
[[ $rc -eq 2 ]] && note "Usage errors" "--deadline 2 exits 2" || fail "Usage errors" "--deadline 2 exited $rc"

# JSON report against the installed schema and Doctor's own validator.
python3 -I - "$work/report__json" "$pypath" <<'PY' || status=1
import json, sys
if sys.argv[2]:
    sys.path.insert(0, sys.argv[2])
from doctor_nox import model
report = json.load(open(sys.argv[1]))
schema = json.load(open("/usr/share/doctor-nox/doctor-nox-report.schema.json"))
problems = model.validate_report(report) if hasattr(model, "validate_report") else []
title = "Report schema"
if problems:
    print(f"::error title={title}::{problems[:5]}")
    sys.exit(1)
print(f"::notice title={title}::{report.get('schema')} with {len(report.get('findings', []))} findings validates (schema file {schema.get('$id', schema.get('title', '?'))})")
PY
redacted="$work/report__text__redact"
if grep -Fq "$(cat /proc/sys/kernel/hostname)" "$redacted" 2>/dev/null; then fail "Redaction" "the redacted text report contains the host name"; else note "Redaction" "host name absent from the redacted text report"; fi

# GUI checks offscreen as an unprivileged user (synthetic probes only).
if python3 -I -c 'import PySide6.QtWidgets' 2>/dev/null; then
    for mode in --gui-smoke-test --gui-self-test; do
        as_user timeout --kill-after=10 180 python3 -I -u -X faulthandler /usr/bin/doctor-nox "$mode" >"$work/gui$mode.log" 2>&1; rc=$?
        if [[ $rc -eq 0 ]]; then note "GUI $mode" "passed as an unprivileged user ($(grep -c . "$work/gui$mode.log") log lines)"
        else fail "GUI $mode" "exit $rc: $(tail -n 5 "$work/gui$mode.log" | tr '\n' ' ' | head -c 600)"; fi
    done
else
    as_user /usr/bin/doctor-nox >"$work/gui-missing.log" 2>&1; rc=$?
    if [[ $rc -eq 3 ]] && grep -q "PySide6" "$work/gui-missing.log"; then
        note "GUI" "PySide6 is not installed here; the window explains what to install and exits 3"
    else fail "GUI" "without PySide6: exit $rc: $(head -c 300 "$work/gui-missing.log")"; fi
fi

# Same results from the source tree and the installed package, run as the same user.
# The source run imports the package from the source tree first, then runs
# the source launcher (which would otherwise prefer /usr/share/doctor-nox).
cp -r "$src" "$work/source"; chown -R "$user" "$work/source"
cat >"$work/run-source.py" <<'PY'
import runpy, sys
source = sys.argv[1]
sys.path.insert(0, source)
import doctor_nox
assert doctor_nox.__file__.startswith(source), doctor_nox.__file__
sys.argv = ["doctor-nox", *sys.argv[2:]]
runpy.run_path(source + "/bin/doctor-nox", run_name="__main__")
PY
chmod 0644 "$work/run-source.py"
as_user python3 -I "$work/run-source.py" "$work/source" --list-checks >"$work/source-checks.txt" 2>&1
if cmp -s "$work/source-checks.txt" "$work/installed-checks.txt"; then
    note "Equivalence: checks" "installed package and source tree list the same $(wc -l <"$work/installed-checks.txt") checks"
else
    fail "Equivalence: checks" "$(diff "$work/source-checks.txt" "$work/installed-checks.txt" | head -c 600)"
fi
as_user python3 -I "$work/run-source.py" "$work/source" --json --deadline 60 >"$work/source.json" 2>/dev/null
python3 -I - "$work/source.json" "$work/report__json" <<'PY' || status=1
import json, sys
def key(path):
    report = json.load(open(path))
    return sorted((f["id"], f["category"], f["severity"]) for f in report["findings"])
a, b = key(sys.argv[1]), key(sys.argv[2])
if a == b:
    print(f"::notice title=Equivalence: findings::{len(a)} findings with the same IDs, categories and severities from the source tree and the installed package")
else:
    diff = sorted(set(a) ^ set(b))
    print(f"::error title=Equivalence: findings::differ: {diff[:10]}")
    sys.exit(1)
PY
rm -rf "$work"
exit "$status"
