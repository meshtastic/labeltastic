#!/usr/bin/env python3
# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Provision Meshtastic nodes for QR-code pairing.

Watches for nodes plugged in over USB. For each one it reads the node's BLE
address, sets Bluetooth to FIXED_PIN with a random 6-digit PIN, and writes a
connect link plus a QR code for it:

    https://meshtastic.org/connections?address=xAA:BB:CC:DD:EE:FF&pin=123456

Every provisioned node is appended to provisioned.csv in the output folder.
Standalone: needs only meshtastic and qrcode[pil] (labeltastic's own
dependencies cover both), no printer.

    python scripts/provision_ble_pin.py                  # watch USB
    python scripts/provision_ble_pin.py --port /dev/ttyACM0
    python scripts/provision_ble_pin.py --link app       # meshtastic:// links

The BLE address comes from DeviceMetadata.ble_address, which needs firmware
with meshtastic/firmware#12281. On older firmware, --macaddr-fallback uses the
node's MAC instead; that equals the BLE address on nRF52 only, not ESP32.
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
import serial.tools.list_ports
from meshtastic import util
from meshtastic.protobuf import config_pb2, mesh_pb2
from meshtastic.serial_interface import SerialInterface

LINK_BASES = {
    "web": "https://meshtastic.org",  # verified app link; falls back to the website without the app
    "app": "meshtastic://meshtastic",  # straight into the app; nothing useful without it
}
# Niimbot label printers, as src/labeltastic/ports.py recognises them: never provision these.
PRINTER_USB_IDS = {(0x1A86, 0x7523)}
PRINTER_USB_VIDS = {0x3513}
BLE_ADDRESS_FIELD = 17  # DeviceMetadata.ble_address
ADDRESS_RE = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){5}$")
CSV_FIELDS = ["time", "port", "node_id", "hw_model", "ble_address", "pin", "uri", "qr_png"]


def read_varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        b = buf[pos]
        pos += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, pos
        shift += 7


def raw_string_field(msg_bytes: bytes, field_number: int) -> str:
    """Find a length-delimited field by number in serialized protobuf bytes."""
    pos = 0
    while pos < len(msg_bytes):
        key, pos = read_varint(msg_bytes, pos)
        number, wire_type = key >> 3, key & 7
        if wire_type == 0:
            _, pos = read_varint(msg_bytes, pos)
        elif wire_type == 1:
            pos += 8
        elif wire_type == 5:
            pos += 4
        elif wire_type == 2:
            length, pos = read_varint(msg_bytes, pos)
            value = msg_bytes[pos : pos + length]
            pos += length
            if number == field_number:
                return value.decode("utf-8", errors="replace")
        else:
            break  # groups are not used in Meshtastic protos
    return ""


def ble_address_from_metadata(md: mesh_pb2.DeviceMetadata | None) -> str:
    if md is None:
        return ""
    if "ble_address" in md.DESCRIPTOR.fields_by_name:
        return md.ble_address.upper()
    # Older meshtastic package: the field arrives as an unknown field, kept on re-serialization.
    return raw_string_field(md.SerializeToString(), BLE_ADDRESS_FIELD).upper()


def ble_address_from_macaddr(iface: SerialInterface) -> str:
    user = iface.getMyUser() or {}
    raw = base64.b64decode(user.get("macaddr", "") or "")
    return ":".join(f"{b:02X}" for b in raw) if len(raw) == 6 else ""


def candidate_ports(vids: set[int] | None) -> set[str]:
    ports = set()
    for p in serial.tools.list_ports.comports():
        if p.vid is None or p.vid in util.blacklistVids:
            continue
        if (p.vid, p.pid) in PRINTER_USB_IDS or p.vid in PRINTER_USB_VIDS:
            continue
        if vids and p.vid not in vids:
            continue
        ports.add(p.device)
    return ports


def connect_uri(base_url: str, ble_address: str, pin: str) -> str:
    query = urlencode({"address": f"x{ble_address}", "pin": pin}, safe=":")
    return f"{base_url.rstrip('/')}/connections?{query}"


def write_qr(uri: str, path: Path) -> None:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=4)
    qr.add_data(uri)
    qr.make(fit=True)
    qr.make_image().save(path)
    qr.print_ascii(invert=True)


def append_csv(path: Path, row: dict) -> None:
    new_file = not path.exists()
    with path.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            w.writeheader()
        w.writerow(row)


def provision(port: str, args, done: set[str]) -> str | None:
    """Provision the node on `port`. Returns its BLE address, or None if skipped or failed."""
    print(f"\n[{port}] connecting...")
    iface = SerialInterface(devPath=port, noNodes=False, timeout=args.timeout)
    try:
        md = iface.metadata
        address = ble_address_from_metadata(md)
        if not address and args.macaddr_fallback:
            address = ble_address_from_macaddr(iface)
            if address:
                print(
                    f"[{port}] no ble_address from firmware; using MAC {address} (correct on nRF52 only)"
                )
        if not ADDRESS_RE.match(address):
            print(
                f"[{port}] firmware did not report a BLE address (needs firmware with #12281); skipping"
            )
            return None
        if address in done and not args.force:
            print(
                f"[{port}] {address} already provisioned this run; skipping (it rebooted after the PIN change)"
            )
            return address

        # 100000-999999, as the firmware draws its own: a fixed_pin of 0 reads as "no fixed PIN" in places.
        pin = str(100_000 + secrets.randbelow(900_000))
        node = iface.localNode
        bt = node.localConfig.bluetooth
        bt.enabled = True
        bt.mode = config_pb2.Config.BluetoothConfig.PairingMode.FIXED_PIN
        bt.fixed_pin = int(pin)
        node.writeConfig("bluetooth")  # the node reboots to apply it
        print(f"[{port}] {address}: Bluetooth set to FIXED_PIN {pin}")

        uri = connect_uri(LINK_BASES[args.link], address, pin)
        png = args.out / f"{address.replace(':', '')}.png"
        write_qr(uri, png)
        print(f"[{port}] {uri}\n[{port}] QR code: {png}")

        user = iface.getMyUser() or {}
        append_csv(
            args.out / "provisioned.csv",
            {
                "time": dt.datetime.now().isoformat(timespec="seconds"),
                "port": port,
                "node_id": user.get("id", ""),
                "hw_model": mesh_pb2.HardwareModel.Name(md.hw_model) if md else "",
                "ble_address": address,
                "pin": pin,
                "uri": uri,
                "qr_png": str(png),
            },
        )
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
    ap.add_argument(
        "--port", help="provision this serial port once instead of watching for new ones"
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=Path("provisioned"),
        help="folder for QR codes and provisioned.csv",
    )
    ap.add_argument(
        "--link",
        choices=sorted(LINK_BASES),
        default="web",
        help="web = https://meshtastic.org/connections?... (opens the app, or the website "
        "without it); app = meshtastic://meshtastic/connections?... (app only)",
    )
    ap.add_argument(
        "--vid",
        type=lambda s: int(s, 16),
        action="append",
        help="only watch USB vendor id (hex), repeatable",
    )
    ap.add_argument(
        "--include-existing",
        action="store_true",
        help="also provision nodes already plugged in at start",
    )
    ap.add_argument("--once", action="store_true", help="exit after the first node is provisioned")
    ap.add_argument(
        "--force", action="store_true", help="re-provision a node already done in this run"
    )
    ap.add_argument(
        "--macaddr-fallback",
        action="store_true",
        help="on old firmware, use the node MAC (nRF52 only)",
    )
    ap.add_argument(
        "--timeout", type=int, default=30, help="seconds to wait for the node to answer"
    )
    ap.add_argument("--poll", type=float, default=1.0, help="seconds between USB scans")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    done: set[str] = set()

    if args.port:
        return 0 if provision(args.port, args, done) else 1

    vids = set(args.vid) if args.vid else None
    seen = set() if args.include_existing else candidate_ports(vids)
    if seen:
        print(
            f"Ignoring ports already present: {', '.join(sorted(seen))} (use --include-existing to provision them)"
        )
    print("Waiting for a node to be plugged in... (Ctrl-C to stop)")
    try:
        while True:
            current = candidate_ports(vids)
            for port in sorted(current - seen):
                time.sleep(2)  # let the port finish enumerating
                try:
                    address = provision(port, args, done)
                except Exception as e:
                    print(f"[{port}] failed: {e}")
                    address = None
                if address and args.once and address in done:
                    return 0
            seen = current
            time.sleep(args.poll)
    except KeyboardInterrupt:
        print(f"\nStopped. {len(done)} node(s) provisioned; see {args.out / 'provisioned.csv'}")
        return 0


if __name__ == "__main__":
    sys.exit(main())
