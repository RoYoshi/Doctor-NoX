# Releasing Doctor NoX

Each Doctor NoX release has a package for every supported system, all built
from the same commit:

| System | File | Signed |
|---|---|---|
| Fedora 45 and 44 | `doctor-nox-X.Y.Z-1.fc45.noarch.rpm`, `…fc44…` (+ SRPMs) | RPM signature |
| openSUSE Tumbleweed | `doctor-nox-X.Y.Z-1.opensuse.noarch.rpm` (+ SRPM) | RPM signature |
| Debian 13, Ubuntu 24.04+ | `doctor-nox_X.Y.Z_all.deb` | via `SHA256SUMS.asc` |
| Arch Linux | `doctor-nox-X.Y.Z-1-any.pkg.tar.zst` | via `SHA256SUMS.asc` |
| pip / pipx | `doctor_nox-X.Y.Z-py3-none-any.whl`, `doctor_nox-X.Y.Z.tar.gz` | via `SHA256SUMS.asc` |

Every file is listed in `SHA256SUMS`, signed with the same key as the RPMs, and
has a `.provenance.json` with its hash, the source commit and the workflow
run. FE-XicoOS installs a specific, reviewed release; it never builds Doctor
from this repository's `main` branch.

## Versioning

- Doctor has its own [semantic version](https://semver.org): `MAJOR.MINOR.PATCH`,
  independent of FE-XicoOS.
  - **MAJOR:** an incompatible change to the CLI, its exit codes or the report
    schema (`doctor-nox/report`, `schema_version`).
  - **MINOR:** new checks or features.
  - **PATCH:** fixes.
- The version is kept in `VERSION`, `Version:` in `packaging/doctor-nox.spec`,
  `VERSION` in `doctor_nox/__init__.py` (which `pyproject.toml` reads),
  `packaging/debian/changelog` and `pkgver` in `packaging/arch/PKGBUILD`. Each
  build script checks that its own file agrees with `VERSION`.
- The RPM `Release:` restarts at `1` for each version. Raise it only for
  packaging-only fixes.
- A release is tagged `vX.Y.Z` on the exact commit it was built from.

## One-time setup (repository owner)

The RPM signing key belongs to the project owner. It is created on your own
machine, stored as a GitHub Actions secret, and never shared in chats, issues
or commits. Only the **public** key is committed.

Run these in your own terminal, signed in to GitHub as yourself. In a
Codespace, first run `unset GITHUB_TOKEN GH_TOKEN` and `gh auth login`: the
Codespace's own token cannot set secrets. The commands keep the key's
fingerprint in `$fpr`, so run them all in the same terminal.

1. **Choose the key.** The committed key (`packaging/signing-key.fingerprint`)
   is Doctor NoX's own, `15E1018F7CDEF196E2B0C3F0AA19D45E300CE63D`, made in
   November 2026. If you have it (where you made it, or your backup), use it:

   ```bash
   fpr=15E1018F7CDEF196E2B0C3F0AA19D45E300CE63D
   gpg --list-secret-keys "$fpr"    # must show a "sec" line; if not, make a new key instead
   ```

   Otherwise make a new key (a passphrase is recommended) and commit its
   public key and fingerprint to `main`:

   ```bash
   gpg --quick-gen-key "Doctor NoX RPM signing <99851152+RoYoshi@users.noreply.github.com>" rsa4096 sign 3y
   fpr=$(gpg --list-secret-keys --with-colons "Doctor NoX RPM signing" | awk -F: '/^fpr/ {print $10; exit}')
   echo "$fpr"                     # stop here if this is empty or not the new key
   gpg --armor --export "$fpr" > packaging/RPM-GPG-KEY-doctor-nox
   echo "$fpr" > packaging/signing-key.fingerprint
   ```

   FE-XicoOS then has to pin the new public key too, in a reviewed change.

2. **Store the private key as secrets** of this repository (not FE-XicoOS):

   ```bash
   gpg --armor --export-secret-keys "$fpr" | gh secret set DOCTOR_RPM_SIGNING_KEY -R RoYoshi/Doctor-NoX
   gh secret set DOCTOR_RPM_SIGNING_PASSPHRASE -R RoYoshi/Doctor-NoX    # prompts; skip if no passphrase
   ```

   Keep an offline backup of the private key. Losing it means a new key, and
   FE-XicoOS must then pin the new public key in a reviewed change.

3. **Protect the release environment.** In *Settings → Environments*, create
   `doctor-release`, add yourself as a required reviewer, and limit it to the
   `main` branch.

## Making a release

1. Bump the version in `VERSION`, the spec (`Version:` and a `%changelog`
   entry), `doctor_nox/__init__.py`, `packaging/debian/changelog`,
   `packaging/arch/PKGBUILD` and the `<releases>` list in
   `data/io.github.royoshi.doctornox.metainfo.xml`, and merge once CI is green.
2. Start **Actions → Release → Run workflow** on `main` with the version.
   After you approve the `doctor-release` environment, the workflow:
   - checks the version, that the tag is new, and that the secret key is
     exactly the committed, pinned public key;
   - runs the source tests and builds the RPMs for Fedora 45 and 44 and
     openSUSE Tumbleweed, the .deb on Debian 13, the Arch package with
     `makepkg`, and the Python sdist and wheel;
   - signs every RPM and verifies it with `rpmkeys --checksig` against the
     committed public key only;
   - installs each package in a clean container (the RPMs with signature
     checks on; the .deb also on Ubuntu 26.04 and 24.04; the wheel with
     pipx, with and without the GUI extra), then runs the installed-package
     checks as an unprivileged user: CLI, reports, schema, redaction, GUI
     smoke and self tests, and equivalence with the source tree;
   - writes a provenance record per file: hash, source commit and workflow
     run (and the signature, for RPMs). It also writes `SHA256SUMS` for every
     package, signed as `SHA256SUMS.asc`;
   - creates a **draft** GitHub release `vX.Y.Z`, with a table of which file
     is for which system. Nothing is published yet.
3. Review the draft: assets, notes and checks. Then publish it by hand.

A green build alone never makes a release usable. It becomes usable only
after you publish it and FE-XicoOS pins it in a reviewed pull request.

## Verifying a release by hand

```bash
gpg --import RPM-GPG-KEY-doctor-nox             # compare its fingerprint with packaging/signing-key.fingerprint
gpg --verify SHA256SUMS.asc SHA256SUMS && sha256sum -c SHA256SUMS
rpmkeys --import RPM-GPG-KEY-doctor-nox         # for RPMs, as root
rpmkeys --checksig doctor-nox-X.Y.Z-1.fc45.noarch.rpm   # must report "signatures OK"
```

The Arch package is built against Arch's Python of the day. After Arch moves
to a newer Python it no longer loads; build `packaging/arch/PKGBUILD` instead
(or install from the AUR once it is there).

## How FE-XicoOS consumes a release

FE-XicoOS keeps the signed RPMs for the pinned version in its repository
(`build_files/doctor/`), with the version, each RPM's SHA-256, and the
public key and its fingerprint. Every image build, including local and
offline ones, then installs exactly the reviewed bytes and downloads nothing.

Before installing, the image build checks:
- the SHA-256;
- the OpenPGP signature against the pinned key;
- the package name, version and release.

It fails if any check fails; it never skips Doctor. Updating Doctor in
FE-XicoOS is a normal pull request that replaces those files.

## Not set up yet: PyPI, the AUR and Flathub

Each needs an account and a decision that belong to the owner; nothing here
publishes to them.

| Where | What it needs |
|---|---|
| PyPI (`pipx install doctor-nox`) | a PyPI project `doctor-nox` with a trusted publisher for this repository's release workflow, and an upload job using it |
| AUR | an AUR account with an SSH key. At release time: set `pkgver`, run `updpkgsums` (pins the tarball checksum) and `makepkg --printsrcinfo > .SRCINFO`, then push `PKGBUILD` and `.SRCINFO` to `aur.archlinux.org/doctor-nox.git` |
| Flathub | a submission of `packaging/flatpak/io.github.royoshi.doctornox.yml`, with the `dir` source replaced by the release's git tag and commit |

**Flathub and the Flatpak's permission.** The Flatpak runs its checks on the
host through `flatpak-spawn --host`, which needs `--talk-name=org.freedesktop.Flatpak`.
Flathub's linter reports that permission as an error
(`finish-args-flatpak-spawn-access`). Flathub grants exceptions case by case,
through a pull request to the linter's exceptions file, so the submission has
to explain why: a diagnostics tool that inspects the system cannot do so from
inside the sandbox, and what it runs on the host is its own read-only command
line.

**Flathub and the app ID.** For an app hosted on GitHub, Flathub works out the
repository's address from the ID, taking its last part as written:
`io.github.royoshi.doctornox` points at `github.com/royoshi/doctornox`, which is
not this repository. Before submitting, either change the ID so its last part
reads `Doctor-NoX`, or ask the reviewers to accept the current one; Flathub
says it can grant that after checking by hand. The ID also names the desktop
file, the metainfo and the icons in every package, so changing it is the
owner's decision.

The Flatpak's CI job builds a bundle (`io.github.royoshi.doctornox.flatpak`)
for testing; it is not part of releases.
