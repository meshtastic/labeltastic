# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Provision nodes for QR-code pairing: `labeltastic-provision`.

Watches for nodes plugged in over USB. For each one it reads the node's BLE
address, sets Bluetooth to FIXED_PIN with a random 6-digit PIN, and writes a
connect link plus a QR code for it, e.g.

  https://meshtastic.org/connections?address=xAA:BB:CC:DD:EE:FF&pin=123456

Scanning that opens the Meshtastic Android app on its Connect prompt with the
PIN filled in. Every provisioned node is appended to provisioned.csv.

Usage:
  labeltastic-provision                       # watch USB, provision each new node
  labeltastic-provision --port /dev/ttyACM0   # provision one node and exit
  labeltastic-provision --link app            # meshtastic:// links instead of https://

The BLE address comes from DeviceMetadata.ble_address (firmware with
meshtastic/firmware#12281). On older firmware, --macaddr-fallback uses the
node's MAC instead, which equals the BLE address on nRF52 only, not ESP32.
"""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import re
import secrets
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

import qrcode

from .ports import looks_like_printer

LINK_BASES = {
    "web": "https://meshtastic.org",  # verified app link; falls back to the website without the app
    "app": "meshtastic://meshtastic",  # straight into the app; nothing useful without it
}
BLE_ADDRESS_FIELD = 17  # DeviceMetadata.ble_address
ADDRESS_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
CSV_FIELDS = ["time", "port", "node_id", "hw_model", "ble_address", "pin", "uri", "qr_png"]


def _read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def raw_string_field(msg_bytes: bytes, field_number: int) -> str:
    """A length-delimited field, found by number in serialized protobuf bytes."""
    pos = 0
    while pos < len(msg_bytes):
        key, pos = _read_varint(msg_bytes, pos)
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            _, pos = _read_varint(msg_bytes, pos)
        elif wire_type == 1:
            pos += 8
        elif wire_type == 5:
            pos += 4
        elif wire_type == 2:
            length, pos = _read_varint(msg_bytes, pos)
            if number == field_number:
                return msg_bytes[pos : pos + length].decode("utf-8", errors="replace")
            pos += length
        else:
            break  # groups: not used by any Meshtastic proto
    return ""


def ble_address_from_metadata(md) -> str:
    """DeviceMetadata.ble_address, uppercased; "" if the firmware didn't report one."""
    if md is None:
        return ""
    if "ble_address" in md.DESCRIPTOR.fields_by_name:
        return md.ble_address.upper()
    # meshtastic older than the field: protobuf keeps it as an unknown field and re-serializes it.
    return raw_string_field(md.SerializeToString(), BLE_ADDRESS_FIELD).upper()


def ble_address_from_macaddr(user: dict) -> str:
    raw = base64.b64decode(user.get("macaddr", "") or "")
    return ":".join(f"{b:02X}" for b in raw) if len(raw) == 6 else ""


def random_pin() -> str:
    # 100000-999999, as the firmware draws its own: a fixed_pin of 0 reads as "no fixed PIN" in places.
    return str(100_000 + secrets.randbelow(900_000))


def connect_uri(base: str, ble_address: str, pin: str) -> str:
    query = urlencode({"address": f"x{ble_address}", "pin": pin}, safe=":")
    return f"{base.rstrip('/')}/connections?{query}"


def node_ports(comports) -> set[str]:
    """Serial ports that could be a node: USB, and not something we know is a label printer.

    Radios on bridges outside RADIO_USB_VIDS (a CH340, an FTDI) still count: a box line
    plugs in whatever it ships, and the printer rule is the one that must not misfire."""
    return {p.device for p in comports if p.vid is not None and not looks_like_printer(p)}


def write_qr(uri: str, path: Path, show: bool = True) -> None:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    qr.make_image().save(path)
    if show:
        qr.print_ascii(invert=True)


def append_csv(path: Path, row: dict) -> None:
    new_file = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)


def _hw_model_name(md) -> str:
    from meshtastic.protobuf import mesh_pb2

    try:
        return mesh_pb2.HardwareModel.Name(md.hw_model) if md else ""
    except ValueError:  # a model newer than the installed meshtastic package
        return str(md.hw_model)


def provision(port: str, args, done: set[str]) -> str | None:
    """Provision the node on `port`. Returns its BLE address, or None if it was skipped."""
    from meshtastic.protobuf import config_pb2
    from meshtastic.serial_interface import SerialInterface

    print(f"\n[{port}] connecting...")
    iface = SerialInterface(devPath=port, timeout=args.timeout)
    try:
        md = iface.metadata
        user = iface.getMyUser() or {}
        address = ble_address_from_metadata(md)
        if not address and args.macaddr_fallback:
            address = ble_address_from_macaddr(user)
            if address:
                print(f"[{port}] no ble_address from firmware; using MAC {address} (nRF52 only)")
        if not ADDRESS_RE.match(address):
            print(f"[{port}] firmware reported no BLE address (needs firmware#12281); skipping")
            return None
        if address in done:
            print(f"[{port}] {address} already done this run (it rebooted after the PIN change)")
            return address

        pin = random_pin()
        # Record the PIN before the node takes it: a failed write after would lock it out.
        uri = connect_uri(LINK_BASES[args.link], address, pin)
        png = args.out / f"{address.replace(':', '')}.png"
        write_qr(uri, png)
        print(f"[{port}] {uri}\n[{port}] QR code: {png}")
        append_csv(
            args.out / "provisioned.csv",
            {
                "time": dt.datetime.now().isoformat(timespec="seconds"),
                "port": port,
                "node_id": user.get("id", ""),
                "hw_model": _hw_model_name(md),
                "ble_address": address,
                "pin": pin,
                "uri": uri,
                "qr_png": str(png),
            },
        )

        node = iface.localNode
        bt = node.localConfig.bluetooth
        bt.enabled = True
        bt.mode = config_pb2.Config.BluetoothConfig.PairingMode.FIXED_PIN
        bt.fixed_pin = int(pin)
        node.writeConfig("bluetooth")  # the node reboots to apply it
        print(f"[{port}] {address}: Bluetooth set to FIXED_PIN {pin}")
        done.add(address)
        return address
    finally:
        try:
            iface.close()
        except Exception:  # the node may already be rebooting
            pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--port", help="provision this serial port once instead of watching USB")
    ap.add_argument(
        "--out", type=Path, default=Path("provisioned"), help="folder for QR PNGs and the CSV"
    )
    ap.add_argument(
        "--link",
        choices=sorted(LINK_BASES),
        default="web",
        help="web = https://meshtastic.org/connections?... (opens the app, or the website "
        "without it); app = meshtastic://meshtastic/connections?... (app only)",
    )
    ap.add_argument(
        "--include-existing", action="store_true", help="also provision nodes plugged in at start"
    )
    ap.add_argument("--once", action="store_true", help="exit after the first node is provisioned")
    ap.add_argument(
        "--macaddr-fallback",
        action="store_true",
        help="old firmware: use the node MAC (nRF52 only)",
    )
    ap.add_argument("--timeout", type=int, default=30, help="seconds to wait for the node")
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between USB scans")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()

    from serial.tools.list_ports import comports

    if args.port:
        if any(p.device == args.port and looks_like_printer(p) for p in comports()):
            print(f"[{args.port}] looks like a label printer, not a node; skipping")
            return 1
        return 0 if provision(args.port, args, done) else 1

    seen = set() if args.include_existing else node_ports(comports())
    if seen:
        print(f"ignoring ports already present: {', '.join(sorted(seen))} (--include-existing)")
    print("waiting for a node to be plugged in... (Ctrl-C to stop)")
    try:
        while True:
            current = node_ports(comports())
            for port in sorted(current - seen):
                time.sleep(2)  # let the port finish enumerating
                try:
                    address = provision(port, args, done)
                except Exception as e:
                    print(f"[{port}] failed: {e}")
                    address = None
                if args.once and address in done:
                    return 0
            seen = current
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print(f"\nstopped: {len(done)} node(s) provisioned, see {args.out / 'provisioned.csv'}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
