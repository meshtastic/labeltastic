# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Meshtastic contact-QR nametag printer for Niimbot label printers.

DM the kiosk node the word "print" (or "badge"/"tag") and it prints a
nametag for the sender: a black HELLO-MY-NODE-IS header, their short/long
name (emoji included, via monochrome Noto Emoji), node ID, the Meshtastic
M-PWRD mark, and a Meshtastic shared-contact QR (https://meshtastic.org/v/#...)
that anyone can scan in the Meshtastic app (>= 2.6) to add them as a contact —
public key included, so the contact imports as PKC-verifiable.

Two printers, picked with --printer (see PROFILES):
  d110  12 mm head. Dynamic-length banner on a continuous roll, or --die-cut
        for gap-sensed 30x15 mm labels (compact layout).
  b1    48 mm head. Landscape card on 50x30 mm labels.

Usage on the Pi:
  labeltastic --printer-port /dev/ttyACM1          # radio on auto serial
  labeltastic --tcp localhost --printer-port ...   # meshtasticd
  labeltastic --printer b1 --die-cut --printer-port /dev/ttyACM1
  labeltastic --test --printer-port /dev/ttyACM1   # print own badge, no mesh trigger
  labeltastic --test --dry-run --sample            # render only, no radio needed
"""

import argparse
import sys
import time

from .contact import SAMPLE_NODE
from .fonts import HAS_EMOJI
from .kiosk import Kiosk
from .ports import resolve_ports
from .printing import print_label, probe_printer
from .profiles import PROFILES


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--serial", default=None, help="radio serial port (default: auto-detect)")
    p.add_argument(
        "--tcp", default=None, help="meshtasticd host instead of serial (e.g. localhost)"
    )
    p.add_argument("--conn", choices=["usb", "bluetooth"], default="usb", help="printer connection")
    p.add_argument(
        "--printer",
        choices=sorted(PROFILES),
        default="d110",
        help="printer model and label stock: "
        + "; ".join(f"{k} = {v.desc}" for k, v in sorted(PROFILES.items())),
    )
    p.add_argument(
        "--printer-port",
        dest="printer_addr",
        default=None,
        help="printer serial port (SET THIS — auto-detect may grab the radio!) or BT MAC",
    )
    p.add_argument(
        "--die-cut",
        action="store_true",
        help="gap-sensed die-cut labels instead of a continuous roll",
    )
    p.add_argument("--flip", action="store_true", help="rotate 180 if labels come out upside down")
    p.add_argument(
        "--rotate", type=int, choices=[90, 270], default=None, help=argparse.SUPPRESS
    )  # deprecated spelling of --flip
    p.add_argument(
        "--density",
        type=int,
        choices=range(1, 6),
        default=3,
        metavar="1-5",
        help="print density, clamped to the model's max",
    )
    p.add_argument("--cooldown", type=int, default=600, help="per-node cooldown, seconds")
    p.add_argument("--test", action="store_true", help="print a badge for this node and exit")
    p.add_argument(
        "--sample", action="store_true", help="print/render a canned node — needs no radio"
    )
    p.add_argument("--dry-run", action="store_true", help="write PNG instead of printing")
    p.add_argument("--out", default="/tmp/badge-test.png", help="--dry-run PNG path")
    p.add_argument("--debug", action="store_true", help="hex-dump printer packets")
    args = p.parse_args()

    if args.rotate == 270:  # --rotate 90 was the default, 270 meant "upside down"
        args.flip = True
    profile = PROFILES[args.printer]
    if args.density > profile.max_density:
        print(
            f"note: {profile.name} maxes out at density {profile.max_density}, "
            f"clamping {args.density}"
        )

    if args.debug:
        import logging

        logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
        logging.getLogger("niimprint").setLevel(logging.DEBUG)

    if not HAS_EMOJI:
        print("note: NotoEmoji-Regular.ttf not found — emoji will be stripped from labels")

    if args.sample:
        # layout/protocol check with no mesh in the room: skip the radio entirely
        if not args.dry_run:
            resolve_ports(args, need_radio=False)
        sys.exit(0 if print_label(SAMPLE_NODE, args, profile) else 1)

    resolve_ports(args)

    if args.tcp:
        from meshtastic.tcp_interface import TCPInterface

        interface = TCPInterface(hostname=args.tcp)
    else:
        from meshtastic.serial_interface import SerialInterface

        interface = SerialInterface(devPath=args.serial)

    my_num = interface.myInfo.my_node_num
    me = interface.nodesByNum.get(my_num, {"num": my_num})
    print(f"connected as {me.get('user', {}).get('longName', '?')} ({my_num:#x})")

    if args.test:
        ok = print_label(me, args, profile)
        sys.exit(0 if ok else 1)

    if not args.dry_run:
        err = probe_printer(args)
        print(f"printer check: {err or 'OK, responding to heartbeat'}")

    Kiosk(interface, args, profile)
    print('kiosk up — DM me "print" for a nametag')
    while True:
        time.sleep(60)
