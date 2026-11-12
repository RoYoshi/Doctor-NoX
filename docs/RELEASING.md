# Releasing Doctor NoX

Doctor NoX is released as signed `noarch` RPMs, one per supported Fedora
release (currently 45 and 44, built from the same commit). FE-XicoOS installs a
specific, reviewed release; it never builds Doctor from this repository's
`main` branch. The other formats are built and tested on every commit but not
yet released: see [Other formats](#other-formats).

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

1. **Create the key** (on your machine; a passphrase is recommended):

   ```bash
   gpg --quick-gen-key "Doctor NoX RPM signing <99851152+RoYoshi@users.noreply.github.com>" rsa4096 sign 3y
   gpg --list-secret-keys --with-colons | awk -F: '/^fpr/ {print $10; exit}'   # the fingerprint
   ```

2. **Commit the public key and its fingerprint** in a pull request:

   ```bash
   gpg --armor --export FINGERPRINT > packaging/RPM-GPG-KEY-doctor-nox
   echo FINGERPRINT > packaging/signing-key.fingerprint
   ```

3. **Store the private key as secrets** of this repository (not FE-XicoOS):

   ```bash
   gpg --armor --export-secret-keys FINGERPRINT | gh secret set DOCTOR_RPM_SIGNING_KEY -R RoYoshi/Doctor-NoX
   gh secret set DOCTOR_RPM_SIGNING_PASSPHRASE -R RoYoshi/Doctor-NoX    # prompts; skip if no passphrase
   ```

   Keep an offline backup of the private key. Losing it means a new key, and
   FE-XicoOS must then pin the new public key in a reviewed change.

4. **Protect the release environment.** In *Settings → Environments*, create
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
   - runs the source tests and builds the source tarball, SRPM and RPM for
     Fedora 45 and 44;
   - signs every RPM and verifies it with `rpmkeys --checksig` against the
     committed public key only;
   - installs each RPM in a clean Fedora container with RPM signature
     enforcement on, then runs the installed-package checks as an
     unprivileged user: CLI, reports, schema, redaction, GUI smoke and self
     tests, and equivalence with the source tree;
   - writes a provenance record per RPM: hashes, signature, source commit and
     workflow run. It also writes `SHA256SUMS`, signed as `SHA256SUMS.asc`;
   - creates a **draft** GitHub release `vX.Y.Z`. Nothing is published yet.
3. Review the draft: assets, notes and checks. Then publish it by hand.

A green build alone never makes a release usable. It becomes usable only
after you publish it and FE-XicoOS pins it in a reviewed pull request.

## Verifying a release by hand

```bash
rpmkeys --import RPM-GPG-KEY-doctor-nox        # compare its fingerprint with packaging/signing-key.fingerprint
rpmkeys --checksig doctor-nox-X.Y.Z-1.fc45.noarch.rpm   # must report "signatures OK"
gpg --verify SHA256SUMS.asc SHA256SUMS && sha256sum -c SHA256SUMS
```

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

## Other formats

Every commit's CI builds and tests these (see `.github/workflows/ci.yml`) and
keeps them as workflow artifacts. The release workflow does not publish them
yet; each one needs a decision and an account that belong to the owner.

| Format | Built by | Still needed before it is released |
|---|---|---|
| openSUSE RPM | `packaging/build-rpm.sh` on Tumbleweed | signing in the release workflow, or an openSUSE Build Service project |
| .deb (Debian 13, Ubuntu 24.04+) | `packaging/build-deb.sh` | attaching to releases, or a signed APT repository / Launchpad PPA |
| Arch package and AUR | `packaging/build-arch.sh` with `packaging/arch/PKGBUILD` | an AUR account and SSH key; at release time `updpkgsums` and `makepkg --printsrcinfo > .SRCINFO`, then push to `aur.archlinux.org/doctor-nox.git` |
| Python sdist and wheel (pip, pipx) | `python3 -m build` | a PyPI project with a trusted publisher for this repository's release workflow |
| Flatpak | not yet | see below |

**Flatpak.** A sandboxed Doctor would not see most of what it checks: the
host's `/var` and `/run` (package databases, restart flags, journal files),
its mount table and its processes. It would need the
`org.freedesktop.Flatpak` permission to run host commands, which Flathub
reviews closely, and every file-based check would have to report "not
visible from the sandbox" rather than a false pass. That is a design decision
to make before any manifest is written.

