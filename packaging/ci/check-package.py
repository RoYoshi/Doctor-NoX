#!/usr/bin/python3
"""Inspect a built doctor-nox RPM: exact file set, modes and dependencies.

    check-package.py PACKAGE.rpm

Prints GitHub annotations; exit 1 on any difference.
"""
import subprocess
import sys

ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
MODULES = ("__init__", "context", "engine", "gui", "model", "report", "runner", "theme", "viewmodel")
PROBES = ("__init__", "hardware", "logs", "network", "security", "software", "storage", "system", "updates")
EXPECTED = {
    "/usr/bin/doctor-nox",
    "/usr/share/applications/io.github.royoshi.doctornox.desktop",
    "/usr/share/doctor-nox/doctor-nox-report.schema.json",
    *(f"/usr/share/doctor-nox/doctor_nox/{m}.py" for m in MODULES),
    *(f"/usr/share/doctor-nox/doctor_nox/probes/{m}.py" for m in PROBES),
    *(f"/usr/share/icons/hicolor/{n}x{n}/apps/io.github.royoshi.doctornox.png" for n in ICON_SIZES),
}
REQUIRED_DEPS = {"python3", "python3-pyside6", "hicolor-icon-theme"}


def query(rpm, *args):
    return subprocess.run(["rpm", "-qp", *args, rpm], check=True, capture_output=True, text=True).stdout


def main():
    rpm = sys.argv[1]
    problems = []
    listing = {}
    for line in query(rpm, "--qf", "[%{FILEMODES:perms} %{FILENAMES}\\n]").splitlines():
        mode, name = line.split(" ", 1)
        listing[name] = mode
    files = {n for n, m in listing.items() if not m.startswith("d")}
    plain = {n for n in files if "/__pycache__/" not in n and not n.startswith("/usr/share/licenses/")
             and not n.startswith("/usr/share/doc/")}
    if plain != EXPECTED:
        problems.append(f"unexpected files {sorted(plain - EXPECTED)[:5]}, missing {sorted(EXPECTED - plain)[:5]}")
    if listing.get("/usr/bin/doctor-nox") != "-rwxr-xr-x":
        problems.append(f"/usr/bin/doctor-nox mode {listing.get('/usr/bin/doctor-nox')}")
    writable = [n for n, m in listing.items() if m[8] == "w" or m[5] == "w"]
    if writable:
        problems.append(f"group/world-writable: {writable[:5]}")
    setid = [n for n, m in listing.items() if "s" in m[1:] or "S" in m[1:]]
    if setid:
        problems.append(f"setuid/setgid: {setid[:5]}")
    if not any("/__pycache__/" in n for n in files):
        problems.append("no bytecode shipped")
    if not any(n.startswith("/usr/share/licenses/") and n.endswith("/LICENSE") for n in files):
        problems.append("LICENSE not shipped")
    requires = {line.split()[0] for line in query(rpm, "--requires").splitlines() if line.strip()}
    missing = REQUIRED_DEPS - requires
    if missing:
        problems.append(f"missing Requires: {sorted(missing)}")
    if not any(r == "python(abi)" for r in requires):
        problems.append("missing Requires: python(abi)")
    arch = query(rpm, "--qf", "%{ARCH}")
    if arch != "noarch":
        problems.append(f"arch {arch}, expected noarch")
    scripts = query(rpm, "--scripts").strip()
    if scripts:
        problems.append(f"package has scriptlets: {scripts[:200]}")
    if problems:
        for p in problems:
            print(f"::error title=Package contents::{p}")
        return 1
    print(f"::notice title=Package contents::{len(plain)} application files as expected, bytecode and LICENSE shipped, "
          f"noarch, no scriptlets, nothing writable or set-id; Requires include {', '.join(sorted(REQUIRED_DEPS))}, python(abi)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
