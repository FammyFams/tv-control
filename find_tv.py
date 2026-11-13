#!/usr/bin/env python3
"""Find smart TVs and streaming devices on the local network.

Sends SSDP (UPnP/DIAL) discovery requests, then reads each responder's
device description to report its name, maker, model and IP address.
Finds most Roku, Samsung, LG, Sony, Vizio, Fire TV, Android/Google TV
and Chromecast devices. For a Roku it also asks the device itself
(port 8060) for the name and room it was given in Roku settings.
Uses only the Python standard library.

Usage:
    python3 find_tv.py              # TVs and streaming devices
    python3 find_tv.py --roku       # Rokus only
    python3 find_tv.py --roku --scan  # also probe every address on the
                                      # local /24 (when discovery is blocked)
    python3 find_tv.py --all        # every UPnP device that answers
"""

import argparse
import ipaddress
import socket
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

SSDP_ADDR = ("239.255.255.250", 1900)
ROKU_PORT = 8060

# Search targets that TVs and streaming sticks commonly answer.
SEARCH_TARGETS = [
    "ssdp:all",
    "urn:dial-multiscreen-org:service:dial:1",          # Chromecast, Fire TV, most smart TVs
    "roku:ecp",                                         # Roku
    "urn:schemas-upnp-org:device:MediaRenderer:1",      # Samsung, LG, Sony, ...
    "urn:samsung.com:device:RemoteControlReceiver:1",   # Samsung
    "urn:lge-com:service:webos-second-screen:1",        # LG webOS
    "urn:schemas-sony-com:service:IRCC:1",              # Sony Bravia
]

TV_HINTS = (
    "tv", "roku", "chromecast", "fire", "bravia", "webos", "tizen", "vizio",
    "dial", "mediarenderer", "remotecontrolreceiver", "ircc", "googlecast",
    "android", "hisense", "tcl", "philips", "panasonic", "sharp", "insignia",
)

POWER_MODES = {
    "PowerOn": "on",
    "DisplayOff": "screen off (standby)",
    "Ready": "standby",
    "Headless": "on (no screen detected)",
}


def build_msearch(st, mx=2):
    return (
        "M-SEARCH * HTTP/1.1\r\n"
        f"HOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
        'MAN: "ssdp:discover"\r\n'
        f"MX: {mx}\r\n"
        f"ST: {st}\r\n"
        "\r\n"
    ).encode()


def parse_ssdp_response(data):
    """Turn an SSDP response into a dict of lower-cased header names."""
    headers = {}
    for line in data.decode(errors="replace").split("\r\n")[1:]:
        if ":" in line:
            key, _, value = line.partition(":")
            headers[key.strip().lower()] = value.strip()
    return headers


def ssdp_discover(timeout, targets):
    """Send M-SEARCH requests and collect responses keyed by LOCATION URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    sock.bind(("", 0))

    for _ in range(2):  # send twice; UDP multicast can drop packets
        for st in targets:
            try:
                sock.sendto(build_msearch(st), SSDP_ADDR)
            except OSError as exc:
                print(f"Could not send discovery packet: {exc}", file=sys.stderr)
                sock.close()
                return {}

    found = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            data, (ip, _) = sock.recvfrom(65507)
        except socket.timeout:
            continue
        except OSError:
            break
        headers = parse_ssdp_response(data)
        location = headers.get("location")
        if not location:
            continue
        entry = found.setdefault(location, {"ip": ip, "location": location, "st": set(), "server": ""})
        if headers.get("st"):
            entry["st"].add(headers["st"])
        if headers.get("server"):
            entry["server"] = headers["server"]
    sock.close()
    return found


def fetch_xml(url, timeout=3):
    """Fetch an XML document and return {tag: text} for its leaf elements."""
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        root = ET.fromstring(resp.read())
    fields = {}
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag not in fields and elem.text and elem.text.strip():
            fields[tag] = elem.text.strip()
    return fields


def fetch_description(location):
    """Read friendlyName / manufacturer / model from a UPnP description XML."""
    try:
        return fetch_xml(location)
    except Exception:
        return {}


def roku_device_info(ip, port=ROKU_PORT, timeout=3):
    """Ask a Roku for its device info. Returns a dict, or {"error": ...}."""
    try:
        return fetch_xml(f"http://{ip}:{port}/query/device-info", timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code == 403:
            return {"error": "the Roku refused (set Settings > System > Advanced system settings "
                             "> Control by mobile apps > Network access to Default or Permissive)"}
        return {"error": f"HTTP {exc.code}"}
    except Exception as exc:
        return {"error": str(exc)}


def is_roku(entry):
    text = " ".join([entry["server"], *entry["st"], entry["info"].get("manufacturer", ""),
                     entry["info"].get("deviceType", "")]).lower()
    return "roku" in text or urlparse(entry["location"]).port == ROKU_PORT


def looks_like_tv(entry):
    haystack = " ".join(
        [entry["server"], *entry["st"], *(str(v) for v in entry["info"].values())]
    ).lower()
    return any(hint in haystack for hint in TV_HINTS)


def local_ipv4():
    """Best guess at this computer's LAN address (no packets are sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        try:
            s.connect(("192.0.2.1", 80))  # TEST-NET address; connect() on UDP sends nothing
            ip = s.getsockname()[0]
        except OSError:
            return None
    return None if ip.startswith("127.") else ip


def scan_for_rokus(port=ROKU_PORT, timeout=0.6):
    """Try port 8060 on every address in this computer's /24 network."""
    me = local_ipv4()
    if not me:
        print("Could not work out this computer's network address; skipping scan.", file=sys.stderr)
        return []
    net = ipaddress.ip_network(f"{me}/24", strict=False)
    print(f"Scanning {net} for Rokus on port {port}...")

    def probe(ip):
        try:
            with socket.create_connection((str(ip), port), timeout=timeout):
                return str(ip)
        except OSError:
            return None

    with ThreadPoolExecutor(max_workers=64) as pool:
        return [ip for ip in pool.map(probe, net.hosts()) if ip]


def print_roku(ip, info):
    roku = info.get("roku", {})
    name = (roku.get("user-device-name") or roku.get("friendly-device-name")
            or info.get("friendlyName") or "Roku")
    print(f"  {name}")
    if roku.get("user-device-location"):
        print(f"    Room       : {roku['user-device-location']}")
    print(f"    IP address : {ip}")
    model = " ".join(filter(None, [roku.get("vendor-name"), roku.get("model-name"),
                                   roku.get("model-number")]))
    if model:
        kind = "Roku TV" if roku.get("is-tv") == "true" else "Roku player/stick"
        print(f"    Device     : {model}  ({kind})")
    elif info.get("modelName"):
        print(f"    Device     : Roku {info['modelName']}")
    if roku.get("power-mode"):
        print(f"    Power      : {POWER_MODES.get(roku['power-mode'], roku['power-mode'])}")
    if roku.get("network-name"):
        print(f"    Wi-Fi      : {roku['network-name']}")
    if roku.get("serial-number"):
        print(f"    Serial     : {roku['serial-number']}")
    if roku.get("error"):
        print(f"    Details    : unavailable, {roku['error']}")
    print()


def print_generic(d):
    info = d["info"]
    name = info.get("friendlyName", "(unnamed device)")
    maker = info.get("manufacturer", "")
    model = " ".join(filter(None, [info.get("modelName"), info.get("modelNumber")]))
    print(f"  {name}")
    print(f"    IP address : {d['ip']}")
    if maker or model:
        print(f"    Device     : {' '.join(filter(None, [maker, model]))}")
    if d["server"]:
        print(f"    Server     : {d['server']}")
    print(f"    Description: {d['location']}")
    print()


def ip_sort_key(ip):
    try:
        return int(ipaddress.ip_address(ip))
    except ValueError:
        return 0


def main():
    parser = argparse.ArgumentParser(description="Find TVs on the local network.")
    parser.add_argument("--timeout", type=float, default=4.0, help="seconds to listen (default 4)")
    parser.add_argument("--all", action="store_true", help="list every UPnP device, not just TVs")
    parser.add_argument("--roku", action="store_true", help="look for Rokus only")
    parser.add_argument("--scan", action="store_true",
                        help="also probe every address on the local network for a Roku "
                             "(use when discovery finds nothing)")
    args = parser.parse_args()

    targets = ["roku:ecp"] if args.roku else SEARCH_TARGETS
    print(f"Searching the local network for {args.timeout:g}s...")
    found = ssdp_discover(args.timeout, targets)

    # One device often answers on several URLs; keep one per IP + name.
    devices = {}
    for entry in found.values():
        entry["info"] = fetch_description(entry["location"])
        key = (entry["ip"], entry["info"].get("friendlyName", entry["location"]))
        if key in devices:
            devices[key]["st"] |= entry["st"]
        else:
            devices[key] = entry

    rokus = {}  # ip -> info
    others = []
    for d in devices.values():
        if is_roku(d):
            rokus.setdefault(d["ip"], d["info"])
        elif not args.roku and (args.all or looks_like_tv(d)):
            others.append(d)

    if args.scan:
        for ip in scan_for_rokus():
            rokus.setdefault(ip, {})

    for ip, info in rokus.items():
        info["roku"] = roku_device_info(ip)

    total = len(rokus) + len(others)
    if not total:
        print("No Rokus found." if args.roku else "No TVs found.")
        print("Check that the TV is on (not in deep standby), that this computer is on the")
        print("same Wi-Fi/LAN as the TV, and that a firewall isn't blocking UDP port 1900.")
        if not args.scan:
            print("If your Wi-Fi blocks discovery, try:  python3 find_tv.py --roku --scan")
        return 1

    print(f"\nFound {total} device(s):\n")
    for ip in sorted(rokus, key=ip_sort_key):
        print_roku(ip, rokus[ip])
    for d in sorted(others, key=lambda d: ip_sort_key(d["ip"])):
        print_generic(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
