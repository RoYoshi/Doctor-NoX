#!/usr/bin/env bash
# Build the Doctor NoX source tarball, SRPM and noarch RPM from a clean
# Git tree. Output: dist/. Needs rpm-build, python3-devel, desktop-file-utils.
#
#   packaging/build-rpm.sh [--allow-dirty]
#
# The tarball contains exactly the committed files (git archive), so a build
# can always be traced to a commit. The spec Version must match VERSION.
set -euo pipefail
cd "$(dirname "$0")/.."

allow_dirty=0
[[ "${1:-}" == --allow-dirty ]] && allow_dirty=1
if [[ $allow_dirty -eq 0 && -n "$(git status --porcelain)" ]]; then
    echo "build-rpm: the working tree has uncommitted changes (use --allow-dirty for local experiments)" >&2
    exit 1
fi

version="$(tr -d '[:space:]' < VERSION)"
[[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "build-rpm: VERSION '$version' is not X.Y.Z" >&2; exit 1; }
spec_version="$(rpmspec -q --qf '%{VERSION}\n' --srpm packaging/doctor-nox.spec)"
[[ "$spec_version" == "$version" ]] || { echo "build-rpm: spec Version $spec_version != VERSION $version" >&2; exit 1; }
app_version="$(python3 -I -c 'import sys; sys.path.insert(0, "."); from doctor_nox import VERSION; print(VERSION)')"
[[ "$app_version" == "$version" ]] || { echo "build-rpm: doctor_nox.VERSION $app_version != VERSION $version" >&2; exit 1; }

top="$PWD/dist/rpmbuild"
rm -rf dist
mkdir -p "$top"/{SOURCES,SPECS,SRPMS,RPMS,BUILD,BUILDROOT}
if [[ $allow_dirty -eq 1 ]]; then
    # Local experiments only: include uncommitted changes.
    stash="$(git stash create || true)"
    git archive --format=tar.gz --prefix="doctor-nox-$version/" -o "$top/SOURCES/doctor-nox-$version.tar.gz" "${stash:-HEAD}"
else
    git archive --format=tar.gz --prefix="doctor-nox-$version/" -o "$top/SOURCES/doctor-nox-$version.tar.gz" HEAD
fi
cp packaging/doctor-nox.spec "$top/SPECS/"
rpmbuild --define "_topdir $top" -ba "$top/SPECS/doctor-nox.spec"
mkdir -p dist
cp "$top"/SRPMS/*.src.rpm "$top"/RPMS/noarch/*.noarch.rpm dist/
ls -l dist/*.rpm
