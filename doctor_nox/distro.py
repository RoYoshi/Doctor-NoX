"""Which Linux this is, so each check only runs where it means something.

Everything comes from os-release and the filesystem, read through the probe
context (ctx.path), so a test can describe any distribution with a fixture
tree. Nothing here runs a command.

  family            fedora | debian | arch | suse | other  (from ID and ID_LIKE)
  image             ostree | steamos | transactional | ""  (image-based or read-only OS)
  package_managers  what is installed: dnf, rpm, rpm-ostree, bootc, apt, dpkg,
                    pacman, zypper, flatpak, snap, nix, apk, xbps, emerge
  init              systemd | other
  security          selinux | apparmor | ""  (the active mandatory access control)
"""
from __future__ import annotations

from dataclasses import dataclass, field

FAMILIES = ("fedora", "debian", "arch", "suse", "other")
FAMILY_TITLE = {"fedora": "Fedora / RHEL family", "debian": "Debian / Ubuntu family", "arch": "Arch family",
                "suse": "openSUSE / SUSE family", "other": "other Linux"}

#: os-release ID / ID_LIKE words that name a family. ID is checked first, then ID_LIKE in order.
_FAMILY_WORDS = {
    "fedora": "fedora", "rhel": "fedora", "centos": "fedora", "almalinux": "fedora", "rocky": "fedora",
    "debian": "debian", "ubuntu": "debian",
    "arch": "arch", "archlinux": "arch",
    "suse": "suse", "opensuse": "suse", "sles": "suse", "sled": "suse",
}
#: os-release IDs of read-only, image-updated systems that are not OSTree.
_STEAMOS = {"steamos"}
_TRANSACTIONAL = {"opensuse-microos", "opensuse-aeon", "opensuse-kalpa", "sl-micro", "sle-micro"}

#: Package managers by the executable that proves they are installed.
_TOOLS = (
    ("dnf", ("/usr/bin/dnf", "/usr/bin/dnf5")),
    ("rpm", ("/usr/bin/rpm",)),
    ("rpm-ostree", ("/usr/bin/rpm-ostree",)),
    ("bootc", ("/usr/bin/bootc",)),
    ("apt", ("/usr/bin/apt-get",)),
    ("dpkg", ("/usr/bin/dpkg",)),
    ("pacman", ("/usr/bin/pacman",)),
    ("zypper", ("/usr/bin/zypper",)),
    ("flatpak", ("/usr/bin/flatpak",)),
    ("snap", ("/usr/bin/snap",)),
    ("nix", ("/nix/var/nix", "/run/current-system/sw/bin/nix")),
    ("apk", ("/sbin/apk", "/usr/sbin/apk")),
    ("xbps", ("/usr/bin/xbps-install",)),
    ("emerge", ("/usr/bin/emerge",)),
)


@dataclass(frozen=True)
class Platform:
    id: str = ""
    id_like: tuple[str, ...] = ()
    name: str = ""
    version: str = ""
    family: str = "other"
    image: str = ""
    package_managers: tuple[str, ...] = ()
    init: str = "other"
    security: str = ""
    os_release: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def systemd(self) -> bool:
        return self.init == "systemd"

    @property
    def ostree(self) -> bool:
        return self.image == "ostree"

    @property
    def image_based(self) -> bool:
        return bool(self.image)

    def has(self, tool: str) -> bool:
        return tool in self.package_managers

    def describe(self) -> str:
        bits = [self.name or self.id or "unknown Linux", FAMILY_TITLE[self.family]]
        if self.image:
            bits.append({"ostree": "image-based (OSTree)", "steamos": "image-based (SteamOS)",
                         "transactional": "transactional updates"}[self.image])
        return " · ".join(bits)


def family_of(os_id: str, id_like: tuple[str, ...]) -> str:
    for word in (os_id, *id_like):
        word = word.lower()
        if word in _FAMILY_WORDS:
            return _FAMILY_WORDS[word]
        if word.startswith("opensuse") or word.startswith("sle"):
            return "suse"
    return "other"


def detect(ctx) -> Platform:
    data = ctx.os_release()
    os_id = data.get("ID", "").lower()
    id_like = tuple(w.lower() for w in data.get("ID_LIKE", "").split())
    family = family_of(os_id, id_like)
    if ctx.exists("/run/ostree-booted"):
        image = "ostree"
    elif os_id in _STEAMOS:
        image = "steamos"
    elif os_id in _TRANSACTIONAL or "microos" in id_like:
        image = "transactional"
    else:
        image = ""
    tools = tuple(name for name, paths in _TOOLS if any(ctx.exists(p) for p in paths))
    init = "systemd" if ctx.exists("/run/systemd/system") else "other"
    return Platform(id=os_id, id_like=id_like, name=data.get("PRETTY_NAME") or data.get("NAME", ""),
                    version=data.get("VERSION_ID", ""), family=family, image=image,
                    package_managers=tools, init=init, security=_security(ctx), os_release=data)


def _security(ctx) -> str:
    """The active mandatory access control. LSM list first; then each module's own switch."""
    lsm = (ctx.try_text("/sys/kernel/security/lsm", 4096) or "").strip().split(",")
    if "selinux" in lsm or ctx.exists("/sys/fs/selinux/enforce"):
        return "selinux"
    enabled = (ctx.try_text("/sys/module/apparmor/parameters/enabled", 16) or "").strip()
    if "apparmor" in lsm or enabled == "Y":
        return "apparmor"
    return ""


# ── Applicability rules: when a check runs ───────────────────────────────────
@dataclass(frozen=True)
class Scope:
    """A rule for when a check applies, with the words used to explain it."""
    text: str
    test: object  # Callable[[Platform], bool]

    def __call__(self, platform: Platform) -> bool:
        return bool(self.test(platform))


EVERYWHERE = Scope("every Linux system", lambda p: True)
SYSTEMD = Scope("systems running systemd", lambda p: p.systemd)
OSTREE = Scope("image-based OSTree systems (bootc, Fedora Atomic, Universal Blue…)", lambda p: p.ostree)
PACKAGE_BASED = Scope("package-based systems (not image-based)", lambda p: not p.image_based)
FLATPAK = Scope("systems with Flatpak", lambda p: p.has("flatpak"))
SNAP = Scope("systems with Snap", lambda p: p.has("snap"))
KNOWN_FAMILY = Scope("Fedora, Debian/Ubuntu, Arch and openSUSE families",
                     lambda p: p.family in ("fedora", "debian", "arch", "suse"))
