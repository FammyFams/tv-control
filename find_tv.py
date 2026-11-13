#!/usr/bin/env python3
"""Find smart TVs and streaming devices on the local network.

Sends SSDP (UPnP/DIAL) discovery requests, then reads each responder's
device description to report its name, maker, model and IP address.
Finds most Roku, Samsung, LG, Sony, Vizio, Fire TV, Android/Google TV
and Chromecast devices. Uses only the Python standard library.

Usage:
    python3 find_tv.py            # TVs and streaming devices only
    python3 find_tv.py --all      # every UPnP device that answers
    python3 find_tv.py --timeout 6
"""

import argparse
import socket
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

SSDP_ADDR = ("239.255.255.250", 1900)

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


def ssdp_discover(timeout):
    """Send M-SEARCH requests and collect responses keyed by LOCATION URL."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
    sock.settimeout(0.5)
    sock.bind(("", 0))

    for _ in range(2):  # send twice; UDP multicast can drop packets
        for st in SEARCH_TARGETS:
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


def fetch_description(location, timeout=3):
    """Read friendlyName / manufacturer / model from a UPnP description XML."""
    info = {}
    try:
        with urllib.request.urlopen(location, timeout=timeout) as resp:
            root = ET.fromstring(resp.read())
    except Exception:
        return info
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag in ("friendlyName", "manufacturer", "modelName", "modelNumber", "deviceType") and tag not in info:
            if elem.text and elem.text.strip():
                info[tag] = elem.text.strip()
    return info


def looks_like_tv(entry):
    haystack = " ".join(
        [entry["server"], *entry["st"], *(str(v) for v in entry["info"].values())]
    ).lower()
    return any(hint in haystack for hint in TV_HINTS)


def main():
    parser = argparse.ArgumentParser(description="Find TVs on the local network.")
    parser.add_argument("--timeout", type=float, default=4.0, help="seconds to listen (default 4)")
    parser.add_argument("--all", action="store_true", help="list every UPnP device, not just TVs")
    args = parser.parse_args()

    print(f"Searching the local network for {args.timeout:g}s...")
    found = ssdp_discover(args.timeout)

    devices = {}
    for entry in found.values():
        entry["info"] = fetch_description(entry["location"])
        # One device often answers on several URLs; keep one per IP + name.
        key = (entry["ip"], entry["info"].get("friendlyName", entry["location"]))
        if key in devices:
            devices[key]["st"] |= entry["st"]
        else:
            devices[key] = entry

    shown = [d for d in devices.values() if args.all or looks_like_tv(d)]
    shown.sort(key=lambda d: tuple(int(p) for p in d["ip"].split(".")))

    if not shown:
        print("No TVs found.")
        print("Check that the TV is on (not in deep standby), that this computer is on the")
        print("same Wi-Fi/LAN as the TV, and that a firewall isn't blocking UDP port 1900.")
        print("Run with --all to see every device that answered.")
        return 1

    print(f"\nFound {len(shown)} device(s):\n")
    for d in shown:
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
        print(f"    Description: {d['location']}  (port {urlparse(d['location']).port})")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
