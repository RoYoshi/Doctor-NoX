#!/usr/bin/env bash
# Runs the installed Doctor NoX Flatpak (ci.yml, inside dbus-run-session):
# --version, the GUI tests offscreen, a scan of the host (here: this CI
# container) through flatpak-spawn --host, --list-checks from the host, and
# the report shown when the org.freedesktop.Flatpak permission is turned off.
#
#   packaging/ci/check-flatpak.sh SCHEMA.json
set -uo pipefail
schema="${1:?usage: check-flatpak.sh SCHEMA.json}"
app=io.github.royoshi.doctornox
status=0
note() { printf '::notice title=%s::%s\n' "$1" "$2"; }
fail() { printf '::error title=%s::%s\n' "$1" "$2"; status=1; }
work="$(mktemp -d)"

if version="$(flatpak run "$app" --version 2>"$work/err")" && [[ "$version" == "Doctor NoX "* ]]; then
    note "Flatpak --version" "$version"
else
    fail "Flatpak --version" "$version $(head -c 400 "$work/err")"
fi

for mode in --gui-smoke-test --gui-self-test; do
    timeout --kill-after=10 180 flatpak run --env=QT_QPA_PLATFORM=offscreen "$app" "$mode" >"$work/gui.log" 2>&1; rc=$?
    if [[ $rc -eq 0 ]]; then note "Flatpak GUI $mode" "passed ($(grep -c . "$work/gui.log") log lines)"
    else fail "Flatpak GUI $mode" "exit $rc: $(tail -n 8 "$work/gui.log" | tr '\n' ' ' | head -c 700)"; fi
done

# A scan through flatpak-spawn --host: the report must describe the host, not the sandbox.
timeout --kill-after=10 150 flatpak run "$app" --json --deadline 60 >"$work/host.json" 2>"$work/host.err"; rc=$?
python3 -I - "$work/host.json" "$schema" "$rc" "$work/host.err" <<'PY' || status=1
import json, sys
import jsonschema
title = "Flatpak host scan"
try:
    report = json.load(open(sys.argv[1]))
except ValueError as exc:
    print(f"::error title={title}::no JSON ({exc}): {open(sys.argv[4]).read()[:500]}")
    sys.exit(1)
jsonschema.validate(report, json.load(open(sys.argv[2])))
rc, ids = int(sys.argv[3]), [f["id"] for f in report["findings"]]
first = report["findings"][0] if report["findings"] else {}
if rc not in (0, 1) or "system.host_access" in ids or report["host"]["family"] != "fedora" or len(ids) < 10:
    print(f"::error title={title}::exit {rc}, host {report['host'].get('name')}, findings {ids[:6]}: "
          f"{first.get('summary', '')} / {first.get('reason', '')} / {first.get('evidence', [])[:3]}")
    sys.exit(1)
print(f"::notice title={title}::{len(ids)} findings about {report['host']['name']} ({report['host']['family']}) "
      f"through flatpak-spawn --host, {len(report['not_applicable'])} not applicable; the report validates "
      f"against schema v{report['schema_version']}")
PY

flatpak run "$app" --list-checks >"$work/checks.txt" 2>"$work/checks.err"; rc=$?
if [[ $rc -eq 0 ]] && head -n 1 "$work/checks.txt" | grep -q "Fedora"; then
    note "Flatpak --list-checks" "from the host: $(head -n 1 "$work/checks.txt")"
else
    fail "Flatpak --list-checks" "exit $rc: $(head -c 300 "$work/checks.txt") $(head -c 300 "$work/checks.err")"
fi

# The permission turned off: one Unknown finding that says so, never the sandbox's state.
flatpak run --no-talk-name=org.freedesktop.Flatpak "$app" --json >"$work/denied.json" 2>"$work/denied.err"; rc=$?
python3 -I - "$work/denied.json" "$rc" <<'PY' || status=1
import json, sys
title = "Flatpak without the permission"
report = json.load(open(sys.argv[1]))
findings = report["findings"]
if len(findings) != 1 or findings[0]["id"] != "system.host_access" or findings[0]["severity"] != "unknown":
    print(f"::error title={title}::expected one Unknown system.host_access finding, got "
          f"{[(f['id'], f['severity']) for f in findings][:6]}")
    sys.exit(1)
print(f"::notice title={title}::exit {sys.argv[2]}: {findings[0]['summary']} "
      f"(evidence: {' | '.join(findings[0]['evidence'][:2])[:300]})")
PY

rm -rf "$work"
exit "$status"
