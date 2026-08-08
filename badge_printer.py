#!/usr/bin/env python3
"""Meshtastic contact-QR nametag printer for a Niimbot D110.

DM the kiosk node the word "print" (or "badge"/"tag") and it prints a
nametag for the sender: a black HELLO-MY-NODE-IS header, their short/long
name (emoji included, via monochrome Noto Emoji), node ID, and a Meshtastic
shared-contact QR (https://meshtastic.org/v/#...) that anyone can scan in
the Meshtastic app (>= 2.6) to add them as a contact — public key included,
so the contact imports as PKC-verifiable.

Default layout is a dynamic-length banner for continuous (endless) label
rolls. Pass --die-cut for gap-sensed 30x15 mm labels (compact layout).

Usage on the Pi:
  python badge_printer.py --printer-port /dev/ttyACM1          # radio on auto serial
  python badge_printer.py --tcp localhost --printer-port ...   # meshtasticd
  python badge_printer.py --test --printer-port /dev/ttyACM1   # print own badge, no mesh trigger
  python badge_printer.py --test --dry-run                     # just write /tmp/badge-test.png

Emoji need NotoEmoji-Regular.ttf next to this script (scp it along).
"""

import argparse
import base64
import itertools
import queue
import re
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from pubsub import pub

from meshtastic.protobuf import admin_pb2, mesh_pb2

HEAD_PX = 96            # D110 head is 96 dots (~12 mm @ 203 dpi)
GAP_LABEL_LEN_PX = 236  # ~30 mm of a 30x15 die-cut label
MAX_NAME_PX = 340       # cap on the name column in banner mode
TRIGGER = re.compile(r"\b(print|badge|tag)\b", re.I)

HERE = Path(__file__).resolve().parent
TEXT_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # dev on mac
]
EMOJI_FONTS = [
    str(HERE / "NotoEmoji-Regular.ttf"),
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
]


@lru_cache(maxsize=None)
def _truetype(path, size):
    return ImageFont.truetype(path, size)


def _first_font(candidates, size):
    for path in candidates:
        if Path(path).exists():
            return _truetype(path, size)
    return None


def text_font(size):
    return _first_font(TEXT_FONTS, size) or ImageFont.load_default(size)


def emoji_font(size):
    return _first_font(EMOJI_FONTS, size)


HAS_EMOJI = emoji_font(16) is not None

# Joiners/selectors/skin tones render as stray boxes in monochrome — drop them.
ZAP = {0x200D, 0xFE0E, 0xFE0F} | set(range(0x1F3FB, 0x1F400))


def is_emoji(c):
    o = ord(c)
    return o >= 0x1F000 or 0x2600 <= o <= 0x27BF or 0x2B00 <= o <= 0x2BFF


def clean(s):
    """Keep what we can actually print: text via DejaVu, emoji via Noto Emoji."""
    out = []
    for c in s:
        if ord(c) in ZAP or not c.isprintable():
            continue
        if is_emoji(c):
            if HAS_EMOJI:
                out.append(c)
        elif ord(c) < 0x2500:  # conservative: glyphs DejaVu reliably has
            out.append(c)
    return " ".join("".join(out).split())


def runs(text):
    for emoji, group in itertools.groupby(text, key=is_emoji):
        yield emoji, "".join(group)


def mixed_len(draw, text, size):
    return sum(
        draw.textlength(r, font=(emoji_font(size) if e else text_font(size)))
        for e, r in runs(text)
    )


def draw_mixed(draw, xy, text, size, fill):
    x, y_center = xy
    for e, r in runs(text):
        font = emoji_font(size) if e else text_font(size)
        draw.text((x, y_center), r, font=font, anchor="lm", fill=fill)
        x += draw.textlength(r, font=font)


def fit(draw, text, max_width, size, min_size=9):
    """Shrink font to fit; if min size still overflows, ellipsize the text."""
    for s in range(size, min_size - 1, -1):
        if mixed_len(draw, text, s) <= max_width:
            return text, s
    while len(text) > 1 and mixed_len(draw, text + "…", min_size) > max_width:
        text = text[:-1]
    return text + "…", min_size


def contact_url(node, max_name_bytes=None):
    """Build https://meshtastic.org/v/#<base64(SharedContact)> from a nodedb entry."""
    user = node.get("user", {})
    u = mesh_pb2.User()
    u.id = user.get("id", "")
    long_name = user.get("longName", "")
    if max_name_bytes is not None:
        long_name = long_name.encode()[:max_name_bytes].decode(errors="ignore")
    u.long_name = long_name
    u.short_name = user.get("shortName", "")
    pk = user.get("publicKey")
    if pk:
        try:
            u.public_key = base64.b64decode(pk)
        except Exception:
            pass
    hw = user.get("hwModel")
    if hw:
        try:
            u.hw_model = mesh_pb2.HardwareModel.Value(hw)
        except ValueError:
            pass
    sc = admin_pb2.SharedContact(node_num=node["num"], user=u)
    b64 = base64.urlsafe_b64encode(sc.SerializeToString()).decode().rstrip("=")
    return f"https://meshtastic.org/v/#{b64}"


def qr_image(node, side=HEAD_PX):
    # Modules must stay >= 2 printer dots (0.25 mm) or phones can't scan the
    # thermal print. Long names push the QR past version 7 and modules to
    # 1 dot, so progressively truncate the long name in the QR payload only —
    # the printed text keeps the full name.
    for cut in (None, 32, 24, 16):
        url = contact_url(node, cut)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        if (qr.modules_count + 2) * 2 <= side:
            break
    # Integer module size that fits the head; label margin supplies the quiet zone.
    qr.box_size = max(1, side // (qr.modules_count + 2))
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    canvas = Image.new("L", (side, side), 255)
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


def name_lines(node):
    user = node.get("user", {})
    short = clean(user.get("shortName", ""))
    if not short:  # nothing printable survived — fall back to the hex suffix
        short = f"{node['num'] & 0xFFFF:04X}"
    long_ = clean(user.get("longName", "")) or "?"
    nid = user.get("id", f"!{node['num']:08x}")
    return short, long_, nid


def render_banner(node):
    """Dynamic-length badge for continuous rolls: HELLO header, name, QR."""
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    short, long_, nid = name_lines(node)
    short, s1 = fit(probe, short, MAX_NAME_PX, 46)
    long_, s2 = fit(probe, long_, MAX_NAME_PX, 20)
    nid, s3 = fit(probe, nid, MAX_NAME_PX, 13)
    name_w = int(max(mixed_len(probe, short, s1),
                     mixed_len(probe, long_, s2),
                     mixed_len(probe, nid, s3)))

    hdr_w = int(max(probe.textlength("HELLO", font=text_font(26)),
                    probe.textlength("MY NODE IS", font=text_font(12)))) + 20

    total = hdr_w + 12 + name_w + 12 + HEAD_PX + 4
    label = Image.new("L", (total, HEAD_PX), 255)
    d = ImageDraw.Draw(label)

    d.rectangle([0, 0, hdr_w, HEAD_PX - 1], fill=0)
    cx = hdr_w // 2
    d.text((cx, 32), "HELLO", font=text_font(26), anchor="mm", fill=255)
    d.text((cx, 62), "MY NODE IS", font=text_font(12), anchor="mm", fill=255)

    x = hdr_w + 12
    draw_mixed(d, (x, 30), short, s1, 0)
    draw_mixed(d, (x, 66), long_, s2, 0)
    draw_mixed(d, (x, 87), nid, s3, 0)

    label.paste(qr_image(node), (total - HEAD_PX - 4, 0))
    return label


def render_compact(node):
    """Fixed 30x15 layout for gap-sensed die-cut labels: QR left, text right."""
    label = Image.new("L", (GAP_LABEL_LEN_PX, HEAD_PX), 255)
    label.paste(qr_image(node), (0, 0))
    d = ImageDraw.Draw(label)
    x = HEAD_PX + 6
    width = GAP_LABEL_LEN_PX - x - 2
    short, long_, nid = name_lines(node)
    for text, start, y in ((short, 34, 22), (long_, 16, 56), (nid, 12, 81)):
        text, size = fit(d, text, width, start)
        draw_mixed(d, (x, y), text, size, 0)
    return label


def make_label(node, args):
    return render_compact(node) if args.die_cut else render_banner(node)


def print_label(img, args):
    if args.dry_run:
        img.save("/tmp/badge-test.png")
        print(f"dry run: wrote /tmp/badge-test.png ({img.width}x{img.height})")
        return True

    # Feed direction: head is 96 wide, so rotate the banner upright.
    img = img.transpose(Image.ROTATE_270 if args.rotate == 90 else Image.ROTATE_90)
    assert img.width <= HEAD_PX

    from niimprint import BluetoothTransport, PrinterClient, SerialTransport

    class ContinuousClient(PrinterClient):
        # print_image() hardcodes set_label_type(1) (gap-sensed die-cut);
        # continuous rolls are Niimbot label type 3.
        def set_label_type(self, n):
            return super().set_label_type(3)

    cls = PrinterClient if args.die_cut else ContinuousClient
    for attempt in (1, 2):
        try:
            if args.conn == "usb":
                transport = SerialTransport(port=args.printer_addr or "auto")
            else:
                transport = BluetoothTransport(args.printer_addr)
            cls(transport).print_image(img, density=3)
            return True
        except Exception as e:
            print(f"print attempt {attempt} failed: {e}")
            time.sleep(2)
    return False


class Kiosk:
    def __init__(self, interface, args):
        self.interface = interface
        self.args = args
        self.jobs = queue.Queue()
        self.last_print = {}  # nodenum -> monotonic time
        self.my_num = interface.myInfo.my_node_num
        threading.Thread(target=self.worker, daemon=True).start()
        pub.subscribe(self.on_text, "meshtastic.receive.text")

    def reply(self, dest, msg):
        try:
            self.interface.sendText(msg, destinationId=dest)
        except Exception as e:
            print(f"reply to {dest:#x} failed: {e}")

    def on_text(self, packet, interface):
        if packet.get("to") != self.my_num:
            return  # DMs only; broadcast triggers would melt the label roll at DEFCON
        sender = packet.get("from")
        text = packet.get("decoded", {}).get("text", "")
        if not sender or not TRIGGER.search(text):
            self.reply(sender, 'DM me "print" and I\'ll print your contact-QR nametag.')
            return
        wait = self.args.cooldown - (time.monotonic() - self.last_print.get(sender, -1e9))
        if wait > 0:
            self.reply(sender, f"Easy there — one badge per {self.args.cooldown // 60} min. {int(wait)}s left.")
            return
        node = self.interface.nodesByNum.get(sender)
        if not node or not node.get("user", {}).get("id"):
            self.reply(sender, "I heard you but don't have your nodeinfo yet. Wait a minute and try again.")
            return
        self.last_print[sender] = time.monotonic()
        self.jobs.put((sender, node))
        self.reply(sender, "Printing your nametag... come grab it!")

    def worker(self):
        while True:
            sender, node = self.jobs.get()
            name = node.get("user", {}).get("longName", hex(sender))
            print(f"printing badge for {name} ({sender:#x})")
            if print_label(make_label(node, self.args), self.args):
                print("  done")
            else:
                self.reply(sender, "Printer jammed/unhappy. Poke the humans at the table.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--serial", default=None, help="radio serial port (default: auto-detect)")
    p.add_argument("--tcp", default=None, help="meshtasticd host instead of serial (e.g. localhost)")
    p.add_argument("--conn", choices=["usb", "bluetooth"], default="usb", help="printer connection")
    p.add_argument("--printer-port", dest="printer_addr", default=None,
                   help="printer serial port (SET THIS — auto-detect may grab the radio!) or BT MAC")
    p.add_argument("--die-cut", action="store_true",
                   help="gap-sensed 30x15 labels instead of a continuous roll")
    p.add_argument("--rotate", type=int, choices=[90, 270], default=90,
                   help="flip to 270 if labels come out upside down")
    p.add_argument("--cooldown", type=int, default=600, help="per-node cooldown, seconds")
    p.add_argument("--test", action="store_true", help="print a badge for this node and exit")
    p.add_argument("--dry-run", action="store_true", help="write PNG instead of printing")
    args = p.parse_args()

    if not HAS_EMOJI:
        print("note: NotoEmoji-Regular.ttf not found — emoji will be stripped from labels")
    if args.conn == "usb" and not args.printer_addr and not args.dry_run:
        print("WARNING: no --printer-port; niimprint auto-detect may grab the radio's port.")

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
        ok = print_label(make_label(me, args), args)
        sys.exit(0 if ok else 1)

    Kiosk(interface, args)
    print('kiosk up — DM me "print" for a nametag')
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
