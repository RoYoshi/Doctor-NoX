#!/usr/bin/python3
"""Inspect a built doctor-nox wheel: modules, entry point, data files, licence.

    check-wheel.py doctor_nox-VERSION-py3-none-any.whl

Prints GitHub annotations; exit 1 on any difference.
"""
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from doctor_nox import VERSION  # noqa: E402

ICON_SIZES = (16, 22, 24, 32, 48, 64, 128, 256, 512)
MODULES = ("__init__", "__main__", "cli", "context", "distro", "engine", "gui", "model", "report", "runner",
           "theme", "viewmodel")
PROBES = ("__init__", "hardware", "logs", "network", "security", "software", "storage", "system", "updates")
DATA = f"doctor_nox-{VERSION}.data/data/share"
EXPECTED = {
    *(f"doctor_nox/{m}.py" for m in MODULES),
    *(f"doctor_nox/probes/{m}.py" for m in PROBES),
    f"{DATA}/applications/io.github.royoshi.doctornox.desktop",
    f"{DATA}/metainfo/io.github.royoshi.doctornox.metainfo.xml",
    f"{DATA}/doctor-nox/doctor-nox-report.schema.json",
    *(f"{DATA}/icons/hicolor/{n}x{n}/apps/io.github.royoshi.doctornox.png" for n in ICON_SIZES),
}


def main():
    wheel = Path(sys.argv[1])
    problems = []
    if not wheel.name.startswith(f"doctor_nox-{VERSION}-") or not wheel.name.endswith("-py3-none-any.whl"):
        problems.append(f"wheel name {wheel.name}: expected doctor_nox-{VERSION}-py3-none-any.whl")
    with zipfile.ZipFile(wheel) as z:
        names = set(z.namelist())
        info = f"doctor_nox-{VERSION}.dist-info"
        content = {n for n in names if not n.startswith(info + "/")}
        if content != EXPECTED:
            problems.append(f"unexpected {sorted(content - EXPECTED)[:5]}, missing {sorted(EXPECTED - content)[:5]}")
        entry = z.read(f"{info}/entry_points.txt").decode() if f"{info}/entry_points.txt" in names else ""
        if "doctor-nox = doctor_nox.cli:main" not in entry:
            problems.append(f"entry point missing: {entry!r}")
        meta = z.read(f"{info}/METADATA").decode()
        for needle in ("Name: doctor-nox", f"Version: {VERSION}", "Apache-2.0", "Requires-Python: >=3.10",
                       "Provides-Extra: gui"):
            if needle.lower() not in meta.lower():
                problems.append(f"METADATA lacks {needle!r}")
        requires = [line for line in meta.splitlines() if line.lower().startswith("requires-dist:")]
        if [r for r in requires if "extra" not in r] or not any("pyside6>=6.5" in r.lower() for r in requires):
            problems.append(f"dependencies: expected only PySide6>=6.5 for the gui extra, got {requires}")
        if not any(n.startswith(info + "/") and n.endswith("LICENSE") for n in names):
            problems.append("LICENSE not in the wheel")
    if problems:
        for p in problems:
            print(f"::error title=Wheel contents::{p}")
        return 1
    print(f"::notice title=Wheel contents::{wheel.name}: {len(EXPECTED)} files as expected, the doctor-nox entry point, "
          "the gui extra and the licence")
    return 0


if __name__ == "__main__":
    sys.exit(main())
