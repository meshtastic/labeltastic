# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Getting a rendered label onto the head — or, in a dry run, onto disk."""

import time

from .ports import close_transport, open_transport
from .profiles import pick_layout, roll_label_type, to_printer_orientation


def dump_packets(img):
    """Run the real encoder over the printer-oriented image and report what went
    out. With no B1 to hand this is the only check on the wire format that a
    dry run can make — the PNG proves the layout and nothing else."""
    from ._vendor.niimprint import PrinterClient

    pkts = list(PrinterClient._encode_image_counted(None, img))
    inked = [p for p in pkts if p.type == 0x85]
    # data[2:5] are the per-third black-pixel counts; all-zero on an inked row
    # means ceil(width/8) didn't divide by 3 and the counts were dropped
    blind = sum(1 for p in inked if p.data[2:5] == b"\x00\x00\x00")
    print(
        f"  encoder: {len(pkts)} packets for {img.width}x{img.height}, "
        f"max data {max((len(p.data) for p in pkts), default=0)} B, "
        f"{len(inked)} inked rows, {blind} missing per-third counts"
    )
    if pkts:
        print(f"  first packet: {':'.join(f'{b:02x}' for b in pkts[0].to_bytes())}")


def wake_printer(client, tries=4):
    """Heartbeat until the printer answers — nudges a dozing printer awake."""
    for i in range(tries):
        try:
            return client.heartbeat()
        except RuntimeError:
            if i == tries - 1:
                raise
            time.sleep(1.5)


def probe_printer(args):
    """Heartbeat the printer; returns None if healthy, else the error text."""
    from ._vendor.niimprint import PrinterClient

    transport = None
    try:
        transport = open_transport(args)
        wake_printer(PrinterClient(transport), tries=2)
        return None
    except Exception as e:
        return str(e)
    finally:
        if transport:
            close_transport(args, transport)


def print_label(node, args, profile):
    density = min(args.density, profile.max_density)
    if args.dry_run:
        # no printer, so no RFID: fall back the same way roll_label_type would
        layout = pick_layout(profile, 1 if args.die_cut else profile.default_label_type)
        img = layout.render(node, profile)
        img.save(args.out)
        print(
            f"dry run: wrote {args.out} ({img.width}x{img.height}, {layout.desc}, "
            f"{profile.name} density {density})"
        )
        dump_packets(to_printer_orientation(img, layout, profile, args.flip))
        return True

    from ._vendor.niimprint import PrinterClient
    from ._vendor.niimprint.printer import InfoEnum

    # Models whose heartbeat lid bit is inverted (niimbluelib's list, plus
    # 2320 observed on a Yichip-USB D110 fw 10.51: reports 1 while printing).
    INVERTED_LID = {272, 273, 274, 512, 513, 514, 1792, 2304, 2320, 2560, 3584, 3840, 4352, 5120}

    class BadgePrinter(PrinterClient):
        # print_image() hardcodes set_label_type(1); use what the roll says.
        label_type = 1

        def set_label_type(self, n):
            return super().set_label_type(self.label_type)

    for attempt in (1, 2):
        transport = None
        try:
            transport = open_transport(args)
            client = BadgePrinter(transport)
            hb = wake_printer(client)  # fail here = power/port problem, not protocol
            devicetype = client.get_info(InfoEnum.DEVICETYPE)
            lid = hb.get("closingstate") if hb else None
            if lid is not None and lid != (1 if devicetype in INVERTED_LID else 0):
                print("  WARNING: printer says its lid is OPEN — press it shut until it clicks")
            client.label_type = roll_label_type(client, args, profile)
            layout = pick_layout(profile, client.label_type)
            img = to_printer_orientation(layout.render(node, profile), layout, profile, args.flip)
            pv = client.get_protocol_version()
            print(
                f"  roll type {client.label_type} -> {layout.desc} "
                f"({img.height}px long, devicetype {devicetype}, protocol v{pv})"
            )
            if pv >= 3 or profile.min_protocol >= 3:
                # Matches what official clients send v3+ printers. The legacy
                # sequence also works on D110_M (verified) — this path is kept
                # for its honest status-poll completion and official framing.
                # min_protocol forces it for families that need it: the legacy
                # path sends uncompressed zero-count rows, which this hardware
                # ACKs and then prints blank.
                client.print_image_new(
                    img, density=density, label_type=client.label_type, v4=pv >= 4
                )
            else:
                client.print_image(img, density=density)
            print("  printed!")
            return True
        except Exception as e:
            print(f"print attempt {attempt} failed: {e}")
            time.sleep(2)
        finally:
            if transport:
                close_transport(args, transport)
    return False
