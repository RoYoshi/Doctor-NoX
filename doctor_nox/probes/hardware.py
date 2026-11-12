"""CPU/memory, graphics drivers, session, audio, controllers, sensors.

All inventory comes from /proc and /sys where possible; commands are only
used for names (lspci), NVIDIA runtime state (nvidia-smi, only when the
nvidia module is loaded) and module facts (modinfo; rpm -q on the Fedora
family). Nothing is loaded, unloaded, installed or configured.
"""
from __future__ import annotations

import os
import re

from ..model import ERROR, INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "hardware"
MODULE_TREES = ("/usr/lib/modules", "/lib/modules")
VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel", "0x1af4": "virtio",
           "0x1234": "QEMU/Bochs", "0x15ad": "VMware", "0x80ee": "VirtualBox", "0x1b36": "QEMU/QXL"}
VIRTUAL_DRIVERS = {"virtio-pci", "virtio_gpu", "bochs", "bochs-drm", "qxl", "vmwgfx", "vboxvideo", "cirrus"}
HYPERKIN = "2e24"


def _gib(kib):
    return f"{kib / 1048576:.1f} GiB"


@probe("hardware.cpu_memory", CAT, "CPU & memory")
def cpu_memory(ctx):
    cpuinfo = ctx.try_text("/proc/cpuinfo", 4 * 1024 * 1024) or ""
    meminfo = ctx.try_text("/proc/meminfo", 64 * 1024) or ""
    if not cpuinfo or not meminfo:
        return finding("hardware.cpu_memory", CAT, "CPU & memory", UNKNOWN, "CPU/memory information unavailable",
                       reason="/proc/cpuinfo or /proc/meminfo unreadable")
    models = [l.split(":", 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith("model name")]
    threads = sum(1 for l in cpuinfo.splitlines() if l.startswith("processor"))
    mem = {}
    for line in meminfo.splitlines():
        m = re.match(r"(\w+):\s+(\d+) kB", line)
        if m:
            mem[m.group(1)] = int(m.group(2))
    total, avail = mem.get("MemTotal", 0), mem.get("MemAvailable", 0)
    ev = [f"CPU: {models[0] if models else 'unknown'} · {threads} threads",
          f"memory: {_gib(total)} total, {_gib(avail)} available now",
          f"swap: {_gib(mem.get('SwapTotal', 0))} total, {_gib(mem.get('SwapFree', 0))} free"]
    if total and avail / total < 0.05:
        return finding("hardware.cpu_memory", CAT, "CPU & memory", WARN,
                       f"Memory nearly exhausted: {_gib(avail)} of {_gib(total)} available", evidence=ev,
                       guidance=["Point-in-time value. Check System Monitor for the process using memory."])
    return finding("hardware.cpu_memory", CAT, "CPU & memory", PASS,
                   f"{threads} threads · {_gib(total)} RAM ({_gib(avail)} available)", evidence=ev)


def _gpus(ctx):
    """[(card, vendor_id, device_id, driver, pci_slot)] from /sys/class/drm."""
    out = []
    for card in ctx.listdir("/sys/class/drm"):
        if not re.fullmatch(r"card\d+", card):
            continue
        base = f"/sys/class/drm/{card}/device"
        vendor = (ctx.try_text(f"{base}/vendor", 32) or "").strip().lower()
        device = (ctx.try_text(f"{base}/device", 32) or "").strip().lower()
        driver_link = ctx.path(f"{base}/driver")
        driver = os.path.basename(os.readlink(driver_link)) if os.path.islink(driver_link) else ""
        dev_link = ctx.path(base)
        slot = os.path.basename(os.readlink(dev_link)) if os.path.islink(dev_link) else ""
        out.append((card, vendor, device, driver, slot))
    return out


def _nvidia_flavour(ctx):
    """'open' / 'proprietary' / '' from the loaded module's banner, else modinfo license."""
    banner = ctx.try_text("/proc/driver/nvidia/version", 4096) or ""
    if "Open Kernel Module" in banner:
        return "open", banner.splitlines()[0][:160]
    if "Kernel Module" in banner:
        return "proprietary", banner.splitlines()[0][:160]
    res = ctx.runner.run("modinfo", "-k", ctx.kernel, "-F", "license", "nvidia")
    if res.ok:
        lic = res.stdout.strip()
        return ("open" if ("MIT" in lic or "GPL" in lic) else "proprietary"), f"modinfo license: {lic}"
    return "", res.describe()


def _nvk(ctx) -> bool:
    return any(name.startswith("nouveau_icd") for name in ctx.listdir("/usr/share/vulkan/icd.d"))


@probe("hardware.graphics", CAT, "Graphics")
def graphics(ctx):
    gpus = _gpus(ctx)
    ev = []
    names = {}
    lspci = ctx.runner.run("lspci", "-nnk")
    if lspci.ok:
        for block in lspci.stdout.split("\n\n"):
            for line in block.splitlines():
                if re.search(r"VGA compatible|3D controller|Display controller", line):
                    names[line.split()[0]] = line.split(": ", 1)[-1][:120]
    if not gpus:
        return finding("hardware.graphics", CAT, "Graphics", UNKNOWN, "No DRM graphics devices found",
                       evidence=[lspci.describe()], reason="/sys/class/drm lists no cardN devices.")
    worst, summary = PASS, []
    rank = {PASS: 0, INFO: 1, UNKNOWN: 2, WARN: 3, ERROR: 4}
    guidance = []
    for card, vendor, device, driver, slot in gpus:
        vname = VENDORS.get(vendor, vendor or "unknown vendor")
        label = names.get(slot.split(":", 1)[-1] if slot.count(":") == 2 else slot, "") or names.get(slot, "")
        sev, desc = PASS, ""
        if vendor == "0x10de":
            if driver == "nvidia":
                flavour, fev = _nvidia_flavour(ctx)
                ev.append(fev)
                desc = {"open": "NVIDIA Open kernel driver", "proprietary": "NVIDIA proprietary kernel driver"}.get(
                    flavour, "NVIDIA kernel driver (flavour unknown)")
            elif driver == "nouveau":
                desc = "Nouveau kernel driver" + (" + NVK Vulkan" if _nvk(ctx) else "")
                state, mev = module_state(ctx, "nvidia")
                ev.append(f"NVIDIA module for this kernel: {state} ({mev})")
                if state in ("available", "builtin"):
                    sev = WARN
                    guidance += ["The NVIDIA driver is installed for this kernel, but Nouveau claimed the GPU "
                                 "first, so the NVIDIA driver is not in use.",
                                 "If you installed or updated the NVIDIA driver recently, restart first. "
                                 "Otherwise `journalctl -b -k | grep -iE 'nvidia|nouveau'` shows why."]
                else:
                    sev = INFO  # Nouveau (with NVK) is a valid, supported choice
            elif not driver:
                sev, desc = WARN, "no kernel driver bound"
            else:
                desc = f"{driver} driver"
        elif vendor in ("0x1002", "0x8086"):
            desc = f"{driver} driver" if driver else "no kernel driver bound"
            sev = PASS if driver in ("amdgpu", "i915", "xe", "radeon") else WARN
        elif driver in VIRTUAL_DRIVERS or vendor in ("0x1af4", "0x1234", "0x15ad", "0x80ee", "0x1b36"):
            sev, desc = INFO, f"virtual GPU ({driver or 'no driver'})"
        else:
            sev, desc = (INFO if driver else WARN), f"{driver or 'no'} driver"
        ev.append(f"{card}: {vname} [{vendor}:{device}] {label} → {desc} (pci {slot or '?'})")
        summary.append(f"{vname}: {desc}")
        if sev == WARN and not driver:
            guidance.append(f"{card} has no kernel driver, so it runs without acceleration or not at all. "
                            "`journalctl -b -k` shows whether a driver tried to load.")
        if rank[sev] > rank[worst]:
            worst = sev
    if not lspci.ok:
        ev.append(lspci.describe())
    return finding("hardware.graphics", CAT, "Graphics", worst, " · ".join(summary)[:200], evidence=ev,
                   guidance=guidance)


NVIDIA_SMI_PACKAGE = {"fedora": "xorg-x11-drv-nvidia-cuda (RPM Fusion)", "debian": "nvidia-smi (Debian) or "
                      "nvidia-utils (Ubuntu)", "arch": "nvidia-utils", "suse": "nvidia-compute-utils"}


@probe("hardware.nvidia", CAT, "NVIDIA runtime")
def nvidia(ctx):
    gpus = [g for g in _gpus(ctx) if g[1] == "0x10de"]
    if not gpus:
        return finding("hardware.nvidia", CAT, "NVIDIA runtime", INFO, "No NVIDIA GPU; not applicable")
    if not ctx.exists("/sys/module/nvidia"):
        return finding("hardware.nvidia", CAT, "NVIDIA runtime", INFO,
                       "NVIDIA module not loaded; nvidia-smi not applicable",
                       evidence=["/sys/module/nvidia: absent"])
    kver = (ctx.try_text("/sys/module/nvidia/version", 64) or "").strip()
    res = ctx.runner.run("nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader")
    ev = [f"kernel module version: {kver or 'unknown'}", res.describe()]
    if res.status == "missing":
        package = NVIDIA_SMI_PACKAGE.get(ctx.platform.family, "the NVIDIA utilities package")
        return finding("hardware.nvidia", CAT, "NVIDIA runtime", WARN,
                       "nvidia module loaded but nvidia-smi is missing", evidence=ev,
                       guidance=[f"The NVIDIA user-space tools may be missing; they come with {package}."])
    if not res.ok:
        return finding("hardware.nvidia", CAT, "NVIDIA runtime", WARN, "nvidia-smi did not respond", evidence=ev,
                       reason=res.describe(),
                       guidance=["A kernel/user-space version mismatch or a GPU fault can cause this. "
                                 "`journalctl -b -k | grep -i nvrm` shows driver errors."])
    rows = [r.strip() for r in res.stdout.splitlines() if r.strip()]
    ev += [f"GPU: {r}" for r in rows]
    user_ver = rows[0].rsplit(",", 1)[-1].strip() if rows and "," in rows[0] else ""
    if kver and user_ver and kver != user_ver:
        return finding("hardware.nvidia", CAT, "NVIDIA runtime", WARN,
                       f"Driver mismatch: kernel module {kver}, user space {user_ver}", evidence=ev,
                       guidance=["A driver update that has not taken effect yet: restart so the new kernel "
                                 "module loads. Doctor does not change drivers."])
    return finding("hardware.nvidia", CAT, "NVIDIA runtime", PASS,
                   f"NVIDIA driver {user_ver or kver} responding", evidence=ev)


@probe("hardware.session", CAT, "Display session")
def session(ctx):
    kind = ctx.env.get("XDG_SESSION_TYPE", "")
    desktop = ctx.env.get("XDG_CURRENT_DESKTOP", "")
    ev = [f"XDG_SESSION_TYPE={kind or 'unset'}", f"XDG_CURRENT_DESKTOP={desktop or 'unset'}",
          f"WAYLAND_DISPLAY={'set' if ctx.env.get('WAYLAND_DISPLAY') else 'unset'}",
          f"DISPLAY={'set' if ctx.env.get('DISPLAY') else 'unset'}"]
    if kind == "wayland":
        return finding("hardware.session", CAT, "Display session", PASS,
                       f"Wayland session{' · ' + desktop if desktop else ''}", evidence=ev)
    if kind == "x11":
        return finding("hardware.session", CAT, "Display session", INFO,
                       f"X11 session{' · ' + desktop if desktop else ''}", evidence=ev,
                       guidance=["X11 works for everyday use. Some newer features, such as HDR and "
                                 "per-monitor scaling, need a Wayland session where your desktop offers one."])
    return finding("hardware.session", CAT, "Display session", INFO,
                   "Not running inside a graphical session", evidence=ev)


@probe("hardware.audio", CAT, "Audio")
def audio(ctx):
    cards_text = ctx.try_text("/proc/asound/cards", 64 * 1024)
    cards = [l.split("]:", 1)[1].strip() for l in (cards_text or "").splitlines() if "]:" in l]
    runtime = ctx.env.get("XDG_RUNTIME_DIR", "")
    usable = runtime.startswith("/")
    pipewire = usable and ctx.exists(f"{runtime}/pipewire-0")
    pulse = usable and ctx.exists(f"{runtime}/pulse/native")
    server = "PipeWire" if pipewire else "PulseAudio" if pulse else ""
    ev = [f"ALSA cards: {len(cards)}"] + [f"  {c[:100]}" for c in cards[:8]] + \
         [f"sound server: {server or 'none found'}" if usable else "sound server: unknown (no XDG_RUNTIME_DIR)"]
    if pipewire and pulse:
        ev.append("PulseAudio clients are served (pulse/native socket present)")
    if cards_text is None:
        return finding("hardware.audio", CAT, "Audio", UNKNOWN, "Sound card list unavailable", evidence=ev,
                       reason="/proc/asound/cards unreadable")
    if not cards:
        return finding("hardware.audio", CAT, "Audio", INFO, "No sound cards detected", evidence=ev,
                       guidance=["Normal on some VMs or with only USB/Bluetooth audio disconnected."])
    if usable and not server:
        return finding("hardware.audio", CAT, "Audio", WARN, "Sound hardware present but no sound server is running",
                       evidence=ev, guidance=["`systemctl --user status pipewire wireplumber` (PipeWire) or "
                                              "`systemctl --user status pulseaudio` shows why."])
    return finding("hardware.audio", CAT, "Audio", PASS,
                   f"{len(cards)} sound card(s)" + (f" · {server} running" if server else ""), evidence=ev)


# ── controllers ──────────────────────────────────────────────────────────────
XBOX_INTERFACES = {("ff", "5d", "01"): "Xbox 360 wired", ("ff", "47", "d0"): "Xbox One/Series",
                   ("ff", "5d", "81"): "Xbox 360 wireless receiver"}


def usb_devices(ctx):
    """[(vid, pid, product, [(iface, kind, driver)])] for devices exposing Xbox-protocol interfaces or Hyperkin."""
    found = []
    base = "/sys/bus/usb/devices"
    names = ctx.listdir(base)
    for dev in names:
        if ":" in dev:
            continue
        vid = (ctx.try_text(f"{base}/{dev}/idVendor", 16) or "").strip().lower()
        pid = (ctx.try_text(f"{base}/{dev}/idProduct", 16) or "").strip().lower()
        if not vid:
            continue
        product = (ctx.try_text(f"{base}/{dev}/product", 256) or "").strip()[:80]
        ifaces = []
        for iface in names:
            if not iface.startswith(dev + ":"):
                continue
            key = tuple((ctx.try_text(f"{base}/{iface}/{attr}", 16) or "").strip().lower()
                        for attr in ("bInterfaceClass", "bInterfaceSubClass", "bInterfaceProtocol"))
            link = ctx.path(f"{base}/{iface}/driver")
            driver = os.path.basename(os.readlink(link)) if os.path.islink(link) else ""
            if key in XBOX_INTERFACES or vid == HYPERKIN:
                ifaces.append((iface, XBOX_INTERFACES.get(key, "/".join(key)), driver))
        if ifaces or vid == HYPERKIN:
            found.append((vid, pid, product, ifaces))
    return found


def joysticks(ctx):
    text = ctx.try_text("/proc/bus/input/devices", 512 * 1024) or ""
    out = []
    for block in text.split("\n\n"):
        handlers = re.search(r"^H: Handlers=(.*)$", block, re.M)
        if handlers and re.search(r"\bjs\d+", handlers.group(1)):
            name = re.search(r'^N: Name="(.*)"$', block, re.M)
            ids = re.search(r"Vendor=(\w+) Product=(\w+)", block)
            out.append((name.group(1)[:80] if name else "unknown",
                        f"{ids.group(1)}:{ids.group(2)}" if ids else "?"))
    return out


def module_state(ctx, name):
    """('loaded'|'builtin'|'available'|'absent'|'unknown', evidence) for a module and the running kernel."""
    if ctx.exists(f"/sys/module/{name}"):
        return "loaded", f"/sys/module/{name} present (module loaded)"
    for tree in MODULE_TREES:
        moddir = f"{tree}/{ctx.kernel}"
        builtin = ctx.try_text(f"{moddir}/modules.builtin", 2 * 1024 * 1024)
        if builtin and re.search(rf"(^|/){re.escape(name)}\.ko$", builtin, re.M):
            return "builtin", f"{moddir}/modules.builtin lists {name}"
        dep = ctx.try_text(f"{moddir}/modules.dep", 8 * 1024 * 1024)
        if dep is not None:
            hit = re.search(rf"^(\S*/{re.escape(name)}\.ko(?:\.xz|\.zst|\.gz)?):", dep, re.M)
            return ("available", f"{moddir}/modules.dep: {hit.group(1)}") if hit else \
                   ("absent", f"{moddir}/modules.dep has no {name} entry")
    res = ctx.runner.run("modinfo", "-k", ctx.kernel, "-F", "filename", name)
    if res.ok and res.stdout.strip():
        return "available", f"modinfo: {res.stdout.strip()[:160]}"
    if res.status == "exit":
        return "absent", res.describe()
    return "unknown", res.describe()


def xpad_state(ctx):
    return module_state(ctx, "xpad")


def modules_extra(ctx):
    """Fedora family: is kernel-modules-extra (which carries xpad) installed for the RUNNING kernel?

    ('installed'|'other-kernel'|'absent'|'unknown'|'', evidence); '' where the package does not exist.
    """
    if ctx.platform.family != "fedora" or not ctx.platform.has("rpm"):
        return "", ""
    exact = ctx.runner.run("rpm", "-q", "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n",
                           f"kernel-modules-extra-{ctx.kernel}")
    if exact.ok:
        return "installed", f"rpm: {exact.stdout.strip()}"
    if exact.status != "exit":
        return "unknown", exact.describe()
    anyver = ctx.runner.run("rpm", "-q", "--queryformat=%{NAME} %{VERSION}-%{RELEASE}.%{ARCH}\\n",
                            "kernel-modules-extra")
    if anyver.ok:
        return "other-kernel", f"installed for other kernel(s) only: {anyver.stdout.strip()[:160]}"
    return "absent", f"kernel-modules-extra-{ctx.kernel} is not installed"


def _xpad_fix(ctx, extra):
    """How xpad gets onto this system, in the distribution's own terms."""
    p = ctx.platform
    if p.image_based:
        return ("On an image-based system the image has to include xpad for its kernel; a live package "
                "install is not the fix. Doctor installs and loads nothing.")
    if p.family == "fedora":
        return ("xpad ships in kernel-modules-extra: `sudo dnf install kernel-modules-extra`, then restart. "
                "Doctor installs and loads nothing.")
    if p.id == "ubuntu" or "ubuntu" in p.id_like:
        return ("Ubuntu ships xpad in linux-modules-extra: `sudo apt install linux-modules-extra-$(uname -r)`. "
                "Doctor installs and loads nothing.")
    return ("Your distribution's standard kernel normally includes xpad; a custom or minimal kernel may "
            "leave it out. Doctor installs and loads nothing.")


@probe("hardware.controllers", CAT, "Game controllers")
def controllers(ctx):
    js = joysticks(ctx)
    usb = usb_devices(ctx)
    xpad, xpad_ev = xpad_state(ctx)
    extra, extra_ev = modules_extra(ctx)
    ev = [f"running kernel: {ctx.kernel}", f"xpad: {xpad} ({xpad_ev})"] + \
         ([f"kernel-modules-extra: {extra_ev}"] if extra else []) + \
         [f"joystick devices (/dev/input/js*): {len(js)}"] + [f"  {n} [{i}]" for n, i in js[:8]]
    xbox_unbound = []
    for vid, pid, product, ifaces in usb:
        tag = " (Hyperkin)" if vid == HYPERKIN else ""
        ev.append(f"USB {vid}:{pid} {product}{tag}")
        for iface, kind, driver in ifaces:
            ev.append(f"  interface {iface}: {kind} → driver {driver or 'none'}")
            if kind in XBOX_INTERFACES.values() and not driver:
                xbox_unbound.append(f"{vid}:{pid} {product}".strip())
    explain = ("xpad drives controllers that speak the Xbox USB protocol. HID-mode gamepads (many "
               "Hyperkin, 8BitDo and generic pads) work without it, so a missing xpad only matters for "
               "Xbox-protocol devices.")
    no_extra = extra in ("absent", "other-kernel")
    if xbox_unbound and xpad in ("absent", "unknown"):
        why = ("xpad is not available for the running kernel"
               + (f" because kernel-modules-extra-{ctx.kernel} is not installed" if no_extra else ""))
        return finding("hardware.controllers", CAT, "Game controllers", WARN,
                       "Xbox-protocol controller connected but no driver can claim it", evidence=ev,
                       reason=why, guidance=[explain, _xpad_fix(ctx, extra)])
    if xbox_unbound:
        return finding("hardware.controllers", CAT, "Game controllers", WARN,
                       "Xbox-protocol controller connected but not bound to a driver", evidence=ev,
                       guidance=[explain, "Replug the controller; `journalctl -k -b | grep -i xpad` shows "
                                          "whether the kernel tried to bind it."])
    if xpad in ("loaded", "builtin", "available"):
        summary = {"loaded": "xpad loaded", "builtin": "xpad built into the kernel",
                   "available": "xpad available for this kernel (loads when a controller connects)"}[xpad]
        return finding("hardware.controllers", CAT, "Game controllers", PASS,
                       summary + (f" · {len(js)} gamepad(s) visible" if js else ""), evidence=ev, guidance=[explain])
    if xpad == "absent":
        return finding("hardware.controllers", CAT, "Game controllers", INFO,
                       f"xpad not shipped for kernel {ctx.kernel}" +
                       (" (kernel-modules-extra not installed)" if no_extra else ""),
                       evidence=ev, guidance=[explain, _xpad_fix(ctx, extra)])
    return finding("hardware.controllers", CAT, "Game controllers", UNKNOWN, "Controller driver state unknown",
                   evidence=ev, reason=xpad_ev)


@probe("hardware.sensors", CAT, "Temperatures")
def sensors(ctx):
    readings, over = [], []
    for hw in ctx.listdir("/sys/class/hwmon"):
        base = f"/sys/class/hwmon/{hw}"
        name = (ctx.try_text(f"{base}/name", 64) or hw).strip()
        for entry in ctx.listdir(base):
            m = re.fullmatch(r"(temp\d+)_input", entry)
            if not m:
                continue
            try:
                value = int((ctx.try_text(f"{base}/{entry}", 32) or "").strip()) / 1000
            except ValueError:
                continue
            crit_raw = (ctx.try_text(f"{base}/{m.group(1)}_crit", 32) or "").strip()
            crit = int(crit_raw) / 1000 if crit_raw.lstrip("-").isdigit() else None
            readings.append((value, f"{name}/{m.group(1)}: {value:.0f} °C" + (f" (crit {crit:.0f} °C)" if crit else "")))
            if crit and crit > 0 and value >= crit:
                over.append(readings[-1][1])
    if not readings:
        return finding("hardware.sensors", CAT, "Temperatures", INFO, "No temperature sensors exposed")
    readings.sort(reverse=True)
    ev = [r[1] for r in readings[:12]]
    if over:
        return finding("hardware.sensors", CAT, "Temperatures", WARN, f"At or above critical: {over[0]}",
                       evidence=ev, guidance=["Check cooling/airflow; the hardware may throttle or shut down."])
    return finding("hardware.sensors", CAT, "Temperatures", PASS,
                   f"Hottest sensor {readings[0][0]:.0f} °C; none at a reported critical limit", evidence=ev)
