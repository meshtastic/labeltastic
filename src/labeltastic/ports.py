# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Working out which serial port is the radio and which is the printer.

Neither meshtastic nor niimprint can auto-detect when BOTH a radio and the
printer are plugged in — each sees two serial ports and gives up (or grabs
the wrong one). Identify them by USB vendor/product ID instead.
"""

import os
import re
import sys
from pathlib import Path

PRINTER_USB_IDS = {(0x1A86, 0x7523)}  # CH340 bridge in older D110s
PRINTER_USB_VIDS = {0x3513}  # Yichip: newer D110s expose the MCU's native USB
RADIO_USB_VIDS = {0x10C4, 0x303A, 0x239A, 0x2886, 0x1915, 0x2E8A}
RADIO_USB_IDS = {(0x1A86, 0x55D4)}  # CH9102 shares CH340's vendor id


def looks_like_printer(p):
    name = f"{p.product or ''} {p.description or ''}".lower()
    # "b1" needs word boundaries — as a bare substring it matches half the USB
    # descriptors on earth. The B1's own VID/PID is unknown here, and a CH9102-
    # based one would collide with RADIO_USB_IDS, so pass --printer-port.
    return (
        (p.vid, p.pid) in PRINTER_USB_IDS
        or p.vid in PRINTER_USB_VIDS
        or "niim" in name
        or "d110" in name
        or "yichip" in name
        or re.search(r"\bb1\b", name) is not None
    )


def looks_like_radio(p):
    return p.vid in RADIO_USB_VIDS or (p.vid, p.pid) in RADIO_USB_IDS


def stable_path(dev):
    """Prefer /dev/serial/by-id/ symlinks — ttyACM numbering shuffles on replug."""
    by_id = Path("/dev/serial/by-id")
    if by_id.is_dir():
        for link in by_id.iterdir():
            if os.path.realpath(link) == os.path.realpath(dev):
                return str(link)
    return dev


def resolve_ports(args, need_radio=True):
    """Assign the radio and printer ports; die with a port table if ambiguous."""
    from serial.tools.list_ports import comports

    ports = [p for p in comports() if p.vid is not None]

    def real(dev):
        return os.path.realpath(dev) if dev else None

    if args.conn == "usb" and not args.printer_addr:
        taken = real(args.serial)
        cands = [p for p in ports if looks_like_printer(p) and real(p.device) != taken]
        if not cands and (args.serial or args.tcp):
            # radio is accounted for — the printer is whatever's left, if unique
            cands = [p for p in ports if real(p.device) != taken and not looks_like_radio(p)]
        if len(cands) == 1:
            args.printer_addr = stable_path(cands[0].device)

    if not args.serial and not args.tcp:
        taken = real(args.printer_addr)
        cands = [p for p in ports if looks_like_radio(p) and real(p.device) != taken]
        if not cands:
            cands = [p for p in ports if real(p.device) != taken and not looks_like_printer(p)]
        if len(cands) == 1:
            args.serial = stable_path(cands[0].device)

    print("serial ports:")
    for p in ports:
        tags = (" [printer?]" if looks_like_printer(p) else "") + (
            " [radio?]" if looks_like_radio(p) else ""
        )
        print(f"  {p.device}  {p.vid:04x}:{p.pid:04x}  {p.description}{tags}")
    print(f"radio   -> {args.tcp or args.serial or 'UNRESOLVED'}")
    if args.conn == "usb":
        print(f"printer -> {args.printer_addr or 'UNRESOLVED'}")
    else:
        print(f"printer -> bluetooth {args.printer_addr}")

    missing = []
    if need_radio and not args.tcp and not args.serial:
        missing.append("--serial <radio port>")
    if args.conn == "usb" and not args.printer_addr and not args.dry_run:
        missing.append("--printer-port <printer port>")
    if missing:
        sys.exit(
            f"can't tell the ports apart — pass {' and '.join(missing)} "
            "(stable names live in /dev/serial/by-id/)"
        )


def open_transport(args):
    from ._vendor.niimprint import BluetoothTransport, SerialTransport

    if args.conn == "usb":
        return SerialTransport(port=args.printer_addr or "auto")
    return BluetoothTransport(args.printer_addr)


def close_transport(args, transport):
    try:  # release the port so the next job/retry can't hit "busy"
        (transport._serial if args.conn == "usb" else transport._sock).close()
    except Exception:
        pass
