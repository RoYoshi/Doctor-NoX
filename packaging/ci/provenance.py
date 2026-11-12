#!/usr/bin/python3
"""Write FILE.provenance.json for a release asset: what it is, its hash, and where it came from.

    provenance.py FILE FORMAT BUILT_ON [KEY=VALUE ...]

FORMAT is rpm, deb, arch, wheel or sdist; BUILT_ON names the build container.
Extra KEY=VALUE pairs are recorded as given (for example python=3.14). For an
RPM, the package fields and signature are read with rpm -qp. Used by
release.yml inside GitHub Actions (it reads the GITHUB_* variables).
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def rpm_fields(path):
    def q(fmt):
        return subprocess.run(["rpm", "-qp", "--qf", fmt, path], check=True, capture_output=True, text=True).stdout
    return {"package": q("%{NAME}"), "version": q("%{VERSION}"), "release": q("%{RELEASE}"),
            "arch": q("%{ARCH}"), "signature": q("%{RSAHEADER:pgpsig}")}


def main():
    path, kind, built_on, *extra = sys.argv[1:]
    root = Path(__file__).resolve().parents[2]
    env = os.environ
    record = {
        "file": os.path.basename(path), "format": kind, "sha256": sha256(path),
        "version": (root / "VERSION").read_text().strip(),
        "source": {"repository": env["GITHUB_REPOSITORY"], "commit": env["GITHUB_SHA"]},
        "build": {"workflow_run": f'{env["GITHUB_SERVER_URL"]}/{env["GITHUB_REPOSITORY"]}/actions/runs/{env["GITHUB_RUN_ID"]}',
                  "runner": f"GitHub-hosted ubuntu-24.04, {built_on}"},
    }
    if kind == "rpm":
        record.update(rpm_fields(path))
        record["signing_key_fingerprint"] = (root / "packaging/signing-key.fingerprint").read_text().strip()
    for item in extra:
        key, _, value = item.partition("=")
        record[key] = value
    with open(f"{path}.provenance.json", "w") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
    print(f"::notice title=Built {record['file']}::sha256 {record['sha256']}")


if __name__ == "__main__":
    main()
