#!/usr/bin/env bash
# Build the Doctor NoX .deb (Debian 13+, Ubuntu 24.04+) from a clean Git tree.
# Output: dist/doctor-nox_VERSION_all.deb. Needs dpkg-dev, debhelper, dh-python,
# pybuild-plugin-pyproject, python3-hatchling and desktop-file-utils.
#
#   packaging/build-deb.sh [--allow-dirty]
#
# Like build-rpm.sh, the source is exactly the committed files (git archive).
# debian/ lives in packaging/debian and is copied into the unpacked source.
set -euo pipefail
cd "$(dirname "$0")/.."

allow_dirty=0
[[ "${1:-}" == --allow-dirty ]] && allow_dirty=1
if [[ $allow_dirty -eq 0 && -n "$(git status --porcelain)" ]]; then
    echo "build-deb: the working tree has uncommitted changes (use --allow-dirty for local experiments)" >&2
    exit 1
fi
version="$(tr -d '[:space:]' < VERSION)"
deb_version="$(dpkg-parsechangelog -l packaging/debian/changelog -S Version)"
[[ "$deb_version" == "$version" ]] || { echo "build-deb: debian/changelog $deb_version != VERSION $version" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
ref=HEAD
[[ $allow_dirty -eq 1 ]] && ref="$(git stash create || true)" && ref="${ref:-HEAD}"
git archive --format=tar --prefix="doctor-nox-$version/" "$ref" | tar -x -C "$work"
cp -r packaging/debian "$work/doctor-nox-$version/debian"
(cd "$work/doctor-nox-$version" && dpkg-buildpackage --build=binary --no-sign)
mkdir -p dist
cp "$work"/doctor-nox_"$version"_all.deb dist/
ls -l dist/*.deb
