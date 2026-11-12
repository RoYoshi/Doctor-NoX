"""Local network state only: links, addresses, default route, resolver config.

Doctor sends no packets and triggers no connectivity checks. Being offline
is reported as INFO, never as an OS fault. Wi-Fi network names are never read.
Addresses appear only in the full local report; the sharing report redacts them.
"""
from __future__ import annotations

import fcntl
import ipaddress
import socket
import struct

from ..model import INFO, PASS, UNKNOWN, WARN
from . import finding, probe

CAT = "network"
SIOCGIFADDR = 0x8915


def ipv4_addresses(ctx, ifname):
    """Primary IPv4 address via a read-only ioctl (live system); fixture file otherwise."""
    if str(ctx.root) != "/":
        text = ctx.try_text(f"/fixture/ipv4/{ifname}", 256)
        return [l.strip() for l in (text or "").splitlines() if l.strip()]
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            raw = fcntl.ioctl(s.fileno(), SIOCGIFADDR, struct.pack("256s", ifname[:15].encode()))
        return [socket.inet_ntoa(raw[20:24])]
    except OSError:
        return []


def ipv6_global(ctx):
    """{ifname: [addresses]} for global-scope IPv6 from /proc/net/if_inet6."""
    out = {}
    for line in (ctx.try_text("/proc/net/if_inet6", 256 * 1024) or "").splitlines():
        parts = line.split()
        if len(parts) != 6 or parts[3] != "00":  # scope 00 = global
            continue
        try:
            addr = ipaddress.IPv6Address(int(parts[0], 16))
        except ValueError:
            continue
        out.setdefault(parts[5], []).append(str(addr))
    return out


def interfaces(ctx):
    rows = []
    for name in ctx.listdir("/sys/class/net"):
        if name == "lo":
            continue
        base = f"/sys/class/net/{name}"
        virtual = "/devices/virtual/" in str(ctx.path(base).resolve()) if ctx.path(base).is_symlink() else False
        oper = (ctx.try_text(f"{base}/operstate", 32) or "unknown").strip()
        carrier = (ctx.try_text(f"{base}/carrier", 8) or "").strip() == "1"
        wireless = ctx.exists(f"{base}/wireless") or ctx.exists(f"{base}/phy80211")
        rows.append({"name": name, "virtual": virtual, "oper": oper, "carrier": carrier,
                     "kind": "wireless" if wireless else ("virtual" if virtual else "wired")})
    return rows


@probe("network.link", CAT, "Network links")
def link(ctx):
    if not ctx.exists("/sys/class/net"):
        return finding("network.link", CAT, "Network links", UNKNOWN, "Network interfaces unavailable",
                       reason="/sys/class/net missing")
    rows = interfaces(ctx)
    v6 = ipv6_global(ctx)
    ev, connected = [], []
    for r in rows:
        if r["kind"] == "virtual":
            continue
        v4 = [a for a in ipv4_addresses(ctx, r["name"]) if not a.startswith("169.254.")]
        addrs = v4 + v6.get(r["name"], [])
        ev.append(f"{r['name']} ({r['kind']}): {r['oper']}, carrier {'yes' if r['carrier'] else 'no'}"
                  + (f", addresses: {', '.join(addrs[:4])}" if addrs else ", no routable address"))
        if r["oper"] == "up" and addrs:
            connected.append(r["name"])
    virtual = [r["name"] for r in rows if r["kind"] == "virtual"]
    if virtual:
        ev.append(f"virtual interfaces ignored: {len(virtual)}")
    if not [r for r in rows if r["kind"] != "virtual"]:
        return finding("network.link", CAT, "Network links", INFO, "No physical network interface",
                       evidence=ev)
    if connected:
        return finding("network.link", CAT, "Network links", PASS,
                       f"Connected on {', '.join(connected)}", evidence=ev)
    return finding("network.link", CAT, "Network links", INFO, "Offline: no interface has a routable address",
                   evidence=ev, guidance=["Offline is not an OS fault. Updates and Flatpak installs wait "
                                          "until you are connected."])


def _hex_ipv4(h):
    try:
        return socket.inet_ntoa(struct.pack("<I", int(h, 16)))
    except (ValueError, struct.error):
        return "?"


@probe("network.route", CAT, "Default route")
def route(ctx):
    v4 = ctx.try_text("/proc/net/route", 256 * 1024)
    v6 = ctx.try_text("/proc/net/ipv6_route", 1024 * 1024)
    if v4 is None and v6 is None:
        return finding("network.route", CAT, "Default route", UNKNOWN, "Routing table unavailable",
                       reason="/proc/net/route unreadable")
    ev = []
    for line in (v4 or "").splitlines()[1:]:
        f = line.split()
        if len(f) >= 8 and f[1] == "00000000" and f[7] == "00000000":
            ev.append(f"IPv4 default via {_hex_ipv4(f[2])} dev {f[0]}")
    for line in (v6 or "").splitlines():
        f = line.split()
        if len(f) == 10 and f[0] == "0" * 32 and f[1] == "00" and f[9] != "lo":
            gw = str(ipaddress.IPv6Address(int(f[4], 16))) if f[4] != "0" * 32 else "on-link"
            ev.append(f"IPv6 default via {gw} dev {f[9]}")
    if ev:
        return finding("network.route", CAT, "Default route", PASS, f"{len(ev)} default route(s)", evidence=ev)
    return finding("network.route", CAT, "Default route", INFO, "No default route (offline or local-only network)",
                   evidence=["/proc/net/route and /proc/net/ipv6_route list no default route"])


@probe("network.dns", CAT, "DNS configuration")
def dns(ctx):
    text = ctx.try_text("/etc/resolv.conf", 64 * 1024)
    if text is None:
        return finding("network.dns", CAT, "DNS configuration", WARN, "/etc/resolv.conf is missing or unreadable",
                       guidance=["Name resolution will fail. On Fedora it normally links to "
                                 "systemd-resolved's stub file."])
    servers = [l.split()[1] for l in text.splitlines() if l.startswith("nameserver") and len(l.split()) > 1]
    search = sum(1 for l in text.splitlines() if l.startswith(("search", "domain")))
    ev = [f"nameservers: {', '.join(servers) or 'none'}", f"search/domain lines: {search} (contents not shown)"]
    stub = servers == ["127.0.0.53"] or "127.0.0.53" in servers
    if stub:
        from .updates import unit_show
        props, res = unit_show(ctx, "systemd-resolved.service")
        state = props.get("ActiveState", "")
        ev.append(f"systemd-resolved: {state or res.describe()}")
        if state and state != "active":
            return finding("network.dns", CAT, "DNS configuration", WARN,
                           "DNS points at systemd-resolved, but it is not running", evidence=ev,
                           guidance=["`systemctl status systemd-resolved` shows why."])
        return finding("network.dns", CAT, "DNS configuration", PASS if state else UNKNOWN,
                       "Using systemd-resolved" if state else "systemd-resolved state unknown", evidence=ev)
    if not servers:
        return finding("network.dns", CAT, "DNS configuration", INFO, "No DNS servers configured (offline?)",
                       evidence=ev)
    return finding("network.dns", CAT, "DNS configuration", PASS, f"{len(servers)} DNS server(s) configured",
                   evidence=ev)


@probe("network.manager", CAT, "NetworkManager")
def manager(ctx):
    res = ctx.runner.run("nmcli", "-t", "-f", "STATE,CONNECTIVITY", "general")
    if res.status == "missing":
        return finding("network.manager", CAT, "NetworkManager", INFO, "NetworkManager CLI not installed")
    if not res.ok:
        return finding("network.manager", CAT, "NetworkManager", UNKNOWN, "NetworkManager not reachable",
                       reason=res.describe())
    state, _, conn = res.stdout.strip().partition(":")
    ev = [f"state={state}", f"last known connectivity={conn or 'unknown'} (reported by NetworkManager; "
                            "Doctor did not test)"]
    if state.startswith("connected"):
        return finding("network.manager", CAT, "NetworkManager", PASS,
                       f"{state}" + (f" · connectivity {conn}" if conn else ""), evidence=ev)
    if state in ("disconnected", "asleep"):
        return finding("network.manager", CAT, "NetworkManager", INFO, f"NetworkManager reports {state}", evidence=ev)
    return finding("network.manager", CAT, "NetworkManager", INFO, f"NetworkManager state: {state or 'unknown'}",
                   evidence=ev)
