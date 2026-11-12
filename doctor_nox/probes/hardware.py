"""CPU/memory, graphics drivers, session, audio, controllers, sensors.

All inventory comes from /proc and /sys where possible; commands are only
used for names (lspci), NVIDIA runtime state (nvidia-smi, only when the
nvidia module is loaded) and package/module facts (rpm -q, modinfo).
Nothing is loaded, unloaded, installed or configured.
"""
from __future__ import annotations

import os
import re

from ..model import ERROR, INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "hardware"
NVIDIA_OPEN_POLICY = "/usr/lib/modprobe.d/80-fe-xico-nvidia-open.conf"
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


@probe("hardware.graphics", CAT, "Graphics")
def graphics(ctx):
    gpus = _gpus(ctx)
    nvidia_image = ctx.exists(NVIDIA_OPEN_POLICY)
    ev = [f"image: {'NVIDIA Open edition' if nvidia_image else 'standard (Mesa/Nouveau/NVK) edition'}"]
    names = {}
    lspci = ctx.runner.run("lspci", "-nnk")
    if lspci.ok:
        for block in lspci.stdout.split("\n\n"):
            for line in block.splitlines():
                if re.search(r"VGA compatible|3D controller|Display controller", line):
                    names[line.split()[0]] = line.split(": ", 1)[-1][:120]
    if not gpus:
        return finding("hardware.graphics", CAT, "Graphics", UNKNOWN, "No DRM graphics devices found",
                       evidence=ev + [lspci.describe()], reason="/sys/class/drm lists no cardN devices.")
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
                if flavour == "proprietary" and nvidia_image:
                    sev = WARN
                    guidance.append("This image ships NVIDIA Open modules, but a proprietary module is loaded.")
            elif driver == "nouveau":
                nvk = any(ctx.exists(f"/usr/share/vulkan/icd.d/{f}") for f in
                          ("nouveau_icd.x86_64.json", "nouveau_icd.json"))
                desc = "Nouveau kernel driver" + (" + NVK Vulkan" if nvk else "")
                if nvidia_image:
                    sev = WARN
                    guidance += ["This is the NVIDIA Open image, but Nouveau is bound: the NVIDIA modules did "
                                 "not load for this kernel/GPU.",
                                 "Check `journalctl -b -k | grep -i nvidia` and `modinfo nvidia` in Foot. "
                                 "Older GPUs (pre-Turing) are not supported by NVIDIA Open."]
                else:
                    sev = INFO  # a valid, supported choice on the standard image
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
        if rank[sev] > rank[worst]:
            worst = sev
    if nvidia_image and not any(v == "0x10de" for _, v, *_ in gpus):
        worst = max(worst, INFO, key=rank.get)
        ev.append("NVIDIA Open image running without an NVIDIA GPU (harmless)")
    if not lspci.ok:
        ev.append(lspci.describe())
    return finding("hardware.graphics", CAT, "Graphics", worst, " · ".join(summary)[:200], evidence=ev,
                   guidance=guidance)


@probe("hardware.nvidia", CAT, "NVIDIA runtime")
def nvidia(ctx):
    gpus = [g for g in _gpus(ctx) if g[1] == "0x10de"]
    if not gpus:
        return finding("hardware.nvidia", CAT, "NVIDIA runtime", INFO, "No NVIDIA GPU; not applicable")
    if not ctx.exists("/sys/module/nvidia"):
        return finding("hardware.nvidia", CAT, "NVIDIA runtime", INFO,
                       "NVIDIA module not loaded; nvidia-smi not applicable",
                       evidence=["/sys/module/nvidia: absent"] +
                       ([f"{NVIDIA_OPEN_POLICY}: present"] if ctx.exists(NVIDIA_OPEN_POLICY) else []))
    kver = (ctx.try_text("/sys/module/nvidia/version", 64) or "").strip()
    res = ctx.runner.run("nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader")
    ev = [f"kernel module version: {kver or 'unknown'}", res.describe()]
    if res.status == "missing":
        return finding("hardware.nvidia", CAT, "NVIDIA runtime", WARN,
                       "nvidia module loaded but nvidia-smi is missing", evidence=ev,
                       guidance=["User-space NVIDIA packages (xorg-x11-drv-nvidia) may be absent."])
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
                       guidance=["Usually resolved by booting the deployment both were built for. "
                                 "Doctor does not change drivers."])
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
                       guidance=["FE-XicoOS targets Plasma on Wayland; X11 works but is not the default."])
    return finding("hardware.session", CAT, "Display session", INFO,
                   "Not running inside a graphical session", evidence=ev)


@probe("hardware.audio", CAT, "Audio")
def audio(ctx):
    cards_text = ctx.try_text("/proc/asound/cards", 64 * 1024)
    cards = [l.split("]:", 1)[1].strip() for l in (cards_text or "").splitlines() if "]:" in l]
    runtime = ctx.env.get("XDG_RUNTIME_DIR", "")
    pw = bool(runtime) and os.path.exists(ctx.path(f"{runtime}/pipewire-0")) if runtime.startswith("/") else False
    ev = [f"ALSA cards: {len(cards)}"] + [f"  {c[:100]}" for c in cards[:8]] + \
         [f"PipeWire socket: {'present' if pw else 'absent' if runtime else 'unknown (no XDG_RUNTIME_DIR)'}"]
    if cards_text is None:
        return finding("hardware.audio", CAT, "Audio", UNKNOWN, "Sound card list unavailable", evidence=ev,
                       reason="/proc/asound/cards unreadable")
    if not cards:
        return finding("hardware.audio", CAT, "Audio", INFO, "No sound cards detected", evidence=ev,
                       guidance=["Normal on some VMs or with only USB/Bluetooth audio disconnected."])
    if runtime and not pw:
        return finding("hardware.audio", CAT, "Audio", WARN, "Sound hardware present but PipeWire is not running",
                       evidence=ev, guidance=["`systemctl --user status pipewire wireplumber` shows why."])
    return finding("hardware.audio", CAT, "Audio", PASS,
                   f"{len(cards)} sound card(s)" + (" · PipeWire running" if pw else ""), evidence=ev)


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


def xpad_state(ctx):
    """('loaded'|'builtin'|'available'|'absent'|'unknown', evidence)."""
    if ctx.exists("/sys/module/xpad"):
        return "loaded", "/sys/module/xpad present (module loaded)"
    moddir = f"/usr/lib/modules/{ctx.kernel}"
    builtin = ctx.try_text(f"{moddir}/modules.builtin", 2 * 1024 * 1024)
    if builtin and re.search(r"(^|/)xpad\.ko$", builtin, re.M):
        return "builtin", f"{moddir}/modules.builtin lists xpad"
    dep = ctx.try_text(f"{moddir}/modules.dep", 8 * 1024 * 1024)
    if dep is not None:
        hit = re.search(r"^(\S*/xpad\.ko(?:\.xz|\.zst|\.gz)?):", dep, re.M)
        return ("available", f"{moddir}/modules.dep: {hit.group(1)}") if hit else \
               ("absent", f"{moddir}/modules.dep has no xpad entry")
    res = ctx.runner.run("modinfo", "-k", ctx.kernel, "-F", "filename", "xpad")
    if res.ok and res.stdout.strip():
        return "available", f"modinfo: {res.stdout.strip()[:160]}"
    if res.status == "exit":
        return "absent", res.describe()
    return "unknown", res.describe()


def modules_extra(ctx):
    """('installed'|'other-kernel'|'absent'|'unknown', evidence) for the RUNNING kernel."""
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


@probe("hardware.controllers", CAT, "Game controllers")
def controllers(ctx):
    js = joysticks(ctx)
    usb = usb_devices(ctx)
    xpad, xpad_ev = xpad_state(ctx)
    extra, extra_ev = modules_extra(ctx)
    ev = [f"running kernel: {ctx.kernel}", f"xpad: {xpad} ({xpad_ev})", f"kernel-modules-extra: {extra_ev}",
          f"joystick devices (/dev/input/js*): {len(js)}"] + [f"  {n} [{i}]" for n, i in js[:8]]
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
    if xbox_unbound and xpad in ("absent", "unknown"):
        why = ("xpad is not available for the running kernel"
               + (f" because kernel-modules-extra-{ctx.kernel} is not installed" if extra in ("absent", "other-kernel") else ""))
        return finding("hardware.controllers", CAT, "Game controllers", WARN,
                       "Xbox-protocol controller connected but no driver can claim it", evidence=ev,
                       reason=why, guidance=[explain,
                                            "On an image-based system the fix belongs in the image "
                                            "(ship kernel-modules-extra or xpad for this kernel), not in a "
                                            "live package install. Doctor installs and loads nothing."])
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
                       (" (kernel-modules-extra not installed)" if extra in ("absent", "other-kernel") else ""),
                       evidence=ev, guidance=[explain])
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
