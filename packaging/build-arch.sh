#!/usr/bin/env bash
# Build the Doctor NoX Arch package from a clean Git tree with
# packaging/arch/PKGBUILD. Output: dist/doctor-nox-VERSION-1-any.pkg.tar.zst.
# makepkg refuses to run as root: run this as a normal user with base-devel,
# python-build, python-installer and python-hatchling installed.
#
#   packaging/build-arch.sh [--allow-dirty]
#
# The tarball is `git archive` of the commit, named as the PKGBUILD expects,
# so makepkg uses it instead of downloading the release.
set -euo pipefail
cd "$(dirname "$0")/.."

allow_dirty=0
[[ "${1:-}" == --allow-dirty ]] && allow_dirty=1
if [[ $allow_dirty -eq 0 && -n "$(git status --porcelain)" ]]; then
    echo "build-arch: the working tree has uncommitted changes (use --allow-dirty for local experiments)" >&2
    exit 1
fi
version="$(tr -d '[:space:]' < VERSION)"
pkgver="$(sed -n 's/^pkgver=//p' packaging/arch/PKGBUILD)"
[[ "$pkgver" == "$version" ]] || { echo "build-arch: PKGBUILD pkgver $pkgver != VERSION $version" >&2; exit 1; }

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
ref=HEAD
[[ $allow_dirty -eq 1 ]] && ref="$(git stash create || true)" && ref="${ref:-HEAD}"
cp packaging/arch/PKGBUILD "$work/"
git archive --format=tar.gz --prefix="Doctor-NoX-$version/" -o "$work/doctor-nox-$version.tar.gz" "$ref"
(cd "$work" && makepkg --nodeps --skipchecksums --noconfirm)
mkdir -p dist
cp "$work"/doctor-nox-"$version"-*-any.pkg.tar.* dist/
ls -l dist/*.pkg.tar.*
