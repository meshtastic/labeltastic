#!/usr/bin/env python3
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
  python badge_printer.py --printer-port /dev/ttyACM1          # radio on auto serial
  python badge_printer.py --tcp localhost --printer-port ...   # meshtasticd
  python badge_printer.py --printer b1 --die-cut --printer-port /dev/ttyACM1
  python badge_printer.py --test --printer-port /dev/ttyACM1   # print own badge, no mesh trigger
  python badge_printer.py --test --dry-run --sample            # render only, no radio needed

Emoji need NotoEmoji-Regular.ttf next to this script (scp it along).
"""

import argparse
import base64
import itertools
import os
import queue
import re
import sys
import threading
import time
from collections import namedtuple
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont
from pubsub import pub

from meshtastic.protobuf import admin_pb2, mesh_pb2, portnums_pb2

DOTS_PER_MM = 8         # 203 dpi heads, near enough (7.992 dots/mm)
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


def qr_image(node, side, min_module_px=2):
    # Modules must stay >= min_module_px printer dots (2 dots = 0.25 mm) or
    # phones can't scan the thermal print. Long names push the QR past version 7
    # and modules below the floor, so progressively truncate the long name in the
    # QR payload only — the printed text keeps the full name. A bigger `side`
    # wants a bigger floor too, or a long name silently buys extra modules
    # instead of the extra dots per module the room was meant for.
    for cut in (None, 32, 24, 16):
        url = contact_url(node, cut)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        if (qr.modules_count + 2) * min_module_px <= side:
            break
    # Integer module size that fits the head; label margin supplies the quiet zone.
    qr.box_size = max(1, side // (qr.modules_count + 2))
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    canvas = Image.new("L", (side, side), 255)
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


# The Meshtastic "M-PWRD" mark, traced from M-PWRD_BW_Border.svg in
# meshtastic/design: rounded frame, mountain M on top, PWRD in a band below.
# Coordinates are that file's own user units with the frame's top-left moved to
# the origin, so one scale factor drives every part.
LOGO_W, LOGO_H = 2.28891, 2.20630    # frame bounding box
LOGO_INSET = 0.1                     # frame stroke, same on all four sides
LOGO_R_OUT, LOGO_R_IN = 0.25, 0.15   # outer and inner corner radii
LOGO_BAND = (1.25733, 1.35733)       # bar dividing the M from the wordmark
LOGO_SLASH = ((0.38985, 1.11826), (0.27480, 1.03796),
              (0.85635, 0.20476), (0.97139, 0.28505))
LOGO_CHEVRON = ((1.49623, 0.25395), (2.04204, 1.03727), (1.91897, 1.12302),
                (1.42250, 0.41052), (0.92756, 1.12419), (0.80430, 1.03871),
                (1.34843, 0.25412))  # the SVG rounds this apex; 2 dots, skipped
LOGO_WORD = (0.23894, 1.51383, 1.80207, 0.44351)  # PWRD ink box: x, y, w, h
LOGO_MARK = (0.27480, 0.20476, 1.76724, 0.91943)  # the Ms alone, likewise


def wordmark(size):
    """PWRD cropped to its ink, so it can be scaled into the band exactly
    rather than through some font's idea of cap height."""
    img = Image.new("L", (size * 5, size * 2), 255)
    ImageDraw.Draw(img).text((size // 4, size // 4), "PWRD",
                             font=text_font(size), fill=0)
    return img.crop(img.point(lambda p: 255 - p).getbbox())


@lru_cache(maxsize=None)
def mpwrd_logo(height, invert=False, frame=True):
    """The M-PWRD mark, `height` dots tall, as hard black and white.

    Drawn 4x and thresholded instead of left anti-aliased: niimprint's
    convert("1") Floyd-Steinbergs whatever grey it is handed, which scatters a
    mark this small into noise. Jagged diagonals print better than dithered
    ones. invert=True for dropping it into one of the black header bands.

    frame=False gives the bare Ms — no border, no wordmark. PWRD is only a
    fifth of the frame's height, so anywhere the whole logo has to fit a gap
    rather than claim its own space it lands under a millimetre and prints as a
    smudge. Only the banner, which can just grow, gets the framed version."""
    mx, my, mw, mh = LOGO_MARK
    box_w, box_h = (LOGO_W, LOGO_H) if frame else (mw, mh)
    s = height * 4 / box_h
    img = Image.new("L", (round(box_w * s), height * 4), 255)
    d = ImageDraw.Draw(img)

    if frame:
        def box(x0, y0, x1, y1, r, fill):
            d.rounded_rectangle([x0 * s, y0 * s, x1 * s - 1, y1 * s - 1],
                                radius=r * s, fill=fill)

        box(0, 0, LOGO_W, LOGO_H, LOGO_R_OUT, 0)
        box(LOGO_INSET, LOGO_INSET, LOGO_W - LOGO_INSET, LOGO_H - LOGO_INSET,
            LOGO_R_IN, 255)
        d.rectangle([LOGO_INSET * s, LOGO_BAND[0] * s,
                     (LOGO_W - LOGO_INSET) * s - 1, LOGO_BAND[1] * s - 1], fill=0)

        wx, wy, ww, wh = LOGO_WORD
        word = wordmark(160).resize((max(1, round(ww * s)), max(1, round(wh * s))),
                                    Image.LANCZOS)
        img.paste(word, (round(wx * s), round(wy * s)))

    ox, oy = (0, 0) if frame else (-mx * s, -my * s)
    for poly in (LOGO_SLASH, LOGO_CHEVRON):
        d.polygon([(x * s + ox, y * s + oy) for x, y in poly], fill=0)

    img = img.resize((max(1, round(box_w / box_h * height)), height), Image.LANCZOS)
    ink, paper = (255, 0) if invert else (0, 255)
    return img.point(lambda p: ink if p < 128 else paper)


# --sample: the public key is 2/3 of the QR payload, so a node without one
# renders a comfortably small QR and proves nothing about the real thing. The
# emoji and the long name exercise clean()/draw_mixed() and fit()'s ellipsizing.
SAMPLE_NODE = {
    "num": 0xDEADBEEF,
    "user": {
        "id": "!deadbeef",
        "longName": "Wandering Packet Goblin",
        "shortName": "🐛 WPG",
        "publicKey": base64.b64encode(bytes(range(32))).decode(),
        "hwModel": "HELTEC_V3",
    },
}


def name_lines(node):
    user = node.get("user", {})
    short = clean(user.get("shortName", ""))
    if not short:  # nothing printable survived — fall back to the hex suffix
        short = f"{node['num'] & 0xFFFF:04X}"
    long_ = clean(user.get("longName", "")) or "?"
    nid = user.get("id", f"!{node['num']:08x}")
    return short, long_, nid


BANNER_LOGO_PX = 64   # a column of its own on the endless roll
COMPACT_LOGO_PX = 24  # bare Ms, so this is height of mark not of frame


def render_banner(node, profile):
    """Dynamic-length badge for continuous rolls: HELLO header, name, QR.

    Reads along the feed, so the short axis is the head width."""
    across = profile.head_px
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

    # The roll is continuous, so the mark buys its own column rather than
    # squeezing the name — it can't go in the header band, which is 96 dots
    # tall with HELLO/MY NODE IS already filling all but the bottom 26.
    logo = mpwrd_logo(BANNER_LOGO_PX)
    total = hdr_w + 12 + name_w + 12 + logo.width + 10 + across + 4
    label = Image.new("L", (total, across), 255)
    d = ImageDraw.Draw(label)

    d.rectangle([0, 0, hdr_w, across - 1], fill=0)
    cx = hdr_w // 2
    d.text((cx, 32), "HELLO", font=text_font(26), anchor="mm", fill=255)
    d.text((cx, 62), "MY NODE IS", font=text_font(12), anchor="mm", fill=255)

    x = hdr_w + 12
    draw_mixed(d, (x, 30), short, s1, 0)
    draw_mixed(d, (x, 66), long_, s2, 0)
    draw_mixed(d, (x, 87), nid, s3, 0)

    label.paste(logo, (x + name_w + 12, (across - logo.height) // 2))
    label.paste(qr_image(node, across), (total - across - 4, 0))
    return label


def render_compact(node, profile):
    """Fixed 30x15 layout for gap-sensed die-cut labels: QR left, text right.

    Reads along the feed, same as the banner."""
    across, length = profile.head_px, profile.die_len_px
    label = Image.new("L", (length, across), 255)
    label.paste(qr_image(node, across), (0, 0))
    d = ImageDraw.Draw(label)
    logo = mpwrd_logo(COMPACT_LOGO_PX, frame=False)
    label.paste(logo, (length - 2 - logo.width, across - 4 - logo.height))
    x = across + 6
    width = length - x - 2
    short, long_, nid = name_lines(node)
    # Only the node ID gives up width to the mark. Node IDs are 9 characters, so
    # the column it leaves is still roomy, while the names keep the full width —
    # they are what anyone actually reads off the badge.
    for text, start, y, w in ((short, 34, 22, width), (long_, 16, 56, width),
                              (nid, 12, 81, width - logo.width - 6)):
        text, size = fit(d, text, w, start)
        draw_mixed(d, (x, y), text, size, 0)
    return label


CARD_BAND_PX = 56    # 7 mm header band. Bigger burns a lot of dots at once:
                     # 384x56 is already ~21k, vs the D110 banner's ~10k.
CARD_MARGIN_PX = 16  # 2 mm — the 48 mm head may sit off-centre on 50 mm stock,
                     # so keep ink away from both edges and let it clip white.
CARD_QR_PX = 168
CARD_LOGO_PX = 42    # bare Ms again, filling the band: the wordmark is only
                     # 1/5 of the frame's height, so even here it came out
                     # under a millimetre. Rides in the band, costing no space.


def render_card(node, profile):
    """Landscape card for 50x30 labels: header band on top, name block, QR right.

    Unlike the D110 layouts this reads ACROSS the head, so the canvas is already
    in printer orientation and its layout rotation is 0."""
    w, h = profile.head_px, profile.die_len_px
    label = Image.new("L", (w, h), 255)
    d = ImageDraw.Draw(label)

    d.rectangle([0, 0, w - 1, CARD_BAND_PX - 1], fill=0)
    cx = w // 2
    d.text((cx, 19), "HELLO", font=text_font(26), anchor="mm", fill=255)
    d.text((cx, 43), "MY NODE IS", font=text_font(15), anchor="mm", fill=255)
    label.paste(mpwrd_logo(CARD_LOGO_PX, invert=True, frame=False),
                (CARD_MARGIN_PX, (CARD_BAND_PX - CARD_LOGO_PX) // 2))

    qr_x = w - CARD_MARGIN_PX - CARD_QR_PX
    label.paste(qr_image(node, CARD_QR_PX, min_module_px=3), (qr_x, CARD_BAND_PX + 6))

    x = CARD_MARGIN_PX
    width = qr_x - 8 - x
    short, long_, nid = name_lines(node)
    for text, start, y in ((short, 56, 110), (long_, 24, 168), (nid, 20, 206)):
        text, size = fit(d, text, width, start, min_size=12)
        draw_mixed(d, (x, y), text, size, 0)
    return label


# render_* above, table below: the profiles name the renderers.
Layout = namedtuple("Layout", "render rotate_cw desc")


@dataclass(frozen=True)
class Profile:
    """A printer and the label stock it runs.

    head_px is the head's dot count verbatim. die_len_px is round(mm * 8) less a
    few dots of registration slack, so the raster never overruns the die-cut gap
    (30 mm -> 236, not 240).

    Note the label descriptions name axes in opposite orders, because that is how
    the stock is sold: the D110's "30x15" is feed x across, the B1's "50x30" is
    across x feed.
    """
    name: str
    head_px: int             # dots across the head
    die_len_px: int          # feed rows for one die-cut label
    die_layout: Layout       # label types 1 (gap) and 2 (black mark)
    roll_layout: Layout      # label type 3 (continuous)
    default_label_type: int  # used when the roll has no readable RFID tag
    max_density: int
    min_protocol: int        # force the v3+ print path even if the printer lies
    desc: str

    def __post_init__(self):
        # _encode_image_counted splits the black-pixel counts over thirds of the
        # head and silently ships zeros unless ceil(width/8) divides by 3; widths
        # off a byte boundary additionally shift every row (its to_bytes right-
        # aligns). Both are blank or garbled prints with no error, so refuse the
        # geometry here rather than debug it on the label roll.
        if self.head_px % 24:
            raise ValueError(f"{self.name}: head_px must be a multiple of 24, "
                             f"got {self.head_px}")


PROFILES = {
    "d110": Profile(
        name="d110", head_px=96, die_len_px=236,
        die_layout=Layout(render_compact, 90, "compact 30x15"),
        roll_layout=Layout(render_banner, 90, "banner"),
        default_label_type=3,  # no tag — assume the endless roll they planned on
        max_density=3,         # upstream caps d11/d110/b18 here
        min_protocol=0, desc="12 mm head, 30x15 mm die-cut or continuous roll"),
    "b1": Profile(
        name="b1", head_px=384, die_len_px=236,
        # 50x30 stock is 50 mm across the roll and 30 mm along the feed, so the
        # card is laid out across the head. Continuous rolls print the same
        # fixed-length page: a banner scaled 4x would be 30 cm per badge.
        die_layout=Layout(render_card, 0, "card 50x30"),
        roll_layout=Layout(render_card, 0, "card 50x30"),
        default_label_type=1,  # 50x30 is die-cut; type 3 would drift every label
        max_density=5,
        min_protocol=3, desc="48 mm head, 50x30 mm die-cut labels"),
}


def roll_label_type(client, args, profile):
    """Ask the roll's RFID tag what's loaded: 1=die-cut gaps, 2=black mark,
    3=continuous. Falls back to the command-line flags, then the profile's
    default, when unreadable (third-party rolls have no tag)."""
    if args.die_cut:
        return 1
    try:
        rfid = client.get_rfid()
    except Exception as e:
        # bare pass here used to hide a get_rfid() parse error as a wrong default
        print(f"  RFID read failed ({e}) — assuming label type "
              f"{profile.default_label_type}")
        rfid = None
    if rfid and rfid.get("type") in (1, 2, 3):
        return rfid["type"]
    return profile.default_label_type


def pick_layout(profile, label_type):
    return profile.die_layout if label_type in (1, 2) else profile.roll_layout


# PIL names rotations counter-clockwise; every angle here is clockwise.
CW_TRANSPOSE = {90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}


def to_printer_orientation(img, layout, profile, flip=False):
    """Turn an authored label into what the head prints: width = dots across the
    head, height = feed rows (see set_dimension's arg order in niimprint)."""
    cw = (layout.rotate_cw + (180 if flip else 0)) % 360
    if cw:
        img = img.transpose(CW_TRANSPOSE[cw])
    if img.width > profile.head_px:
        raise ValueError(
            f"LAYOUT BUG: {layout.desc} is {img.width} dots across but the "
            f"{profile.name} head is only {profile.head_px} — not a printer fault")
    if img.width < profile.head_px:  # centre it rather than hug one edge
        canvas = Image.new("L", (profile.head_px, img.height), 255)
        canvas.paste(img, ((profile.head_px - img.width) // 2, 0))
        img = canvas
    return img


def dump_packets(img):
    """Run the real encoder over the printer-oriented image and report what went
    out. With no B1 to hand this is the only check on the wire format that a
    dry run can make — the PNG proves the layout and nothing else."""
    from niimprint import PrinterClient

    pkts = list(PrinterClient._encode_image_counted(None, img))
    inked = [p for p in pkts if p.type == 0x85]
    # data[2:5] are the per-third black-pixel counts; all-zero on an inked row
    # means ceil(width/8) didn't divide by 3 and the counts were dropped
    blind = sum(1 for p in inked if p.data[2:5] == b"\x00\x00\x00")
    print(f"  encoder: {len(pkts)} packets for {img.width}x{img.height}, "
          f"max data {max((len(p.data) for p in pkts), default=0)} B, "
          f"{len(inked)} inked rows, {blind} missing per-third counts")
    if pkts:
        print(f"  first packet: {':'.join(f'{b:02x}' for b in pkts[0].to_bytes())}")


# Neither meshtastic nor niimprint can auto-detect when BOTH a radio and the
# printer are plugged in — each sees two serial ports and gives up (or grabs
# the wrong one). Identify them by USB vendor/product ID instead.
PRINTER_USB_IDS = {(0x1A86, 0x7523)}  # CH340 bridge in older D110s
PRINTER_USB_VIDS = {0x3513}  # Yichip: newer D110s expose the MCU's native USB
RADIO_USB_VIDS = {0x10C4, 0x303A, 0x239A, 0x2886, 0x1915, 0x2E8A}
RADIO_USB_IDS = {(0x1A86, 0x55D4)}  # CH9102 shares CH340's vendor id


def looks_like_printer(p):
    name = f"{p.product or ''} {p.description or ''}".lower()
    # "b1" needs word boundaries — as a bare substring it matches half the USB
    # descriptors on earth. The B1's own VID/PID is unknown here, and a CH9102-
    # based one would collide with RADIO_USB_IDS, so pass --printer-port.
    return ((p.vid, p.pid) in PRINTER_USB_IDS or p.vid in PRINTER_USB_VIDS
            or "niim" in name or "d110" in name or "yichip" in name
            or re.search(r"\bb1\b", name) is not None)


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
            " [radio?]" if looks_like_radio(p) else "")
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
        sys.exit(f"can't tell the ports apart — pass {' and '.join(missing)} "
                 "(stable names live in /dev/serial/by-id/)")


def open_transport(args):
    from niimprint import BluetoothTransport, SerialTransport
    if args.conn == "usb":
        return SerialTransport(port=args.printer_addr or "auto")
    return BluetoothTransport(args.printer_addr)


def close_transport(args, transport):
    try:  # release the port so the next job/retry can't hit "busy"
        (transport._serial if args.conn == "usb" else transport._sock).close()
    except Exception:
        pass


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
    from niimprint import PrinterClient
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
        print(f"dry run: wrote {args.out} ({img.width}x{img.height}, {layout.desc}, "
              f"{profile.name} density {density})")
        dump_packets(to_printer_orientation(img, layout, profile, args.flip))
        return True

    from niimprint import PrinterClient
    from niimprint.printer import InfoEnum

    # Models whose heartbeat lid bit is inverted (niimbluelib's list, plus
    # 2320 observed on a Yichip-USB D110 fw 10.51: reports 1 while printing).
    INVERTED_LID = {272, 273, 274, 512, 513, 514, 1792, 2304, 2320,
                    2560, 3584, 3840, 4352, 5120}

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
            img = to_printer_orientation(layout.render(node, profile), layout,
                                         profile, args.flip)
            pv = client.get_protocol_version()
            print(f"  roll type {client.label_type} -> {layout.desc} "
                  f"({img.height}px long, devicetype {devicetype}, protocol v{pv})")
            if pv >= 3 or profile.min_protocol >= 3:
                # Matches what official clients send v3+ printers. The legacy
                # sequence also works on D110_M (verified) — this path is kept
                # for its honest status-poll completion and official framing.
                # min_protocol forces it for families that need it: the legacy
                # path sends uncompressed zero-count rows, which this hardware
                # ACKs and then prints blank.
                client.print_image_new(img, density=density,
                                       label_type=client.label_type, v4=pv >= 4)
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


class Kiosk:
    def __init__(self, interface, args, profile):
        self.interface = interface
        self.args = args
        self.profile = profile
        self.jobs = queue.Queue()
        self.last_print = {}  # nodenum -> monotonic time
        self.my_num = interface.myInfo.my_node_num
        self.printer_lock = threading.Lock()
        threading.Thread(target=self.worker, daemon=True).start()
        if not args.dry_run:
            threading.Thread(target=self.keepalive, daemon=True).start()
        self.last_rx = time.monotonic()
        self.pending = {}  # nodenum -> monotonic time; waiting for their nodeinfo
        pub.subscribe(self.on_text, "meshtastic.receive.text")
        pub.subscribe(self.on_user, "meshtastic.receive.user")
        pub.subscribe(self.on_any, "meshtastic.receive")
        pub.subscribe(self.on_conn_lost, "meshtastic.connection.lost")
        print(f"kiosk node num {self.my_num:#x}")

    def on_conn_lost(self, interface=None):
        # the meshtastic lib won't reconnect on its own — die loudly so a
        # wrapper loop can restart us
        print("RADIO CONNECTION LOST — exiting so a restart loop can revive us")
        os._exit(70)

    def on_any(self, packet, interface):
        try:
            self.last_rx = time.monotonic()
            if not self.args.debug:
                return  # per-packet firehose — a con mesh never shuts up
            d = packet.get("decoded", {})
            frm, to = packet.get("from"), packet.get("to")
            if isinstance(frm, int) and isinstance(to, int):
                print(f"rx {d.get('portnum', '?'):24} from={frm:#010x} to={to:#010x}")
            else:
                print(f"rx {d.get('portnum', '?')} {packet.get('fromId')} -> {packet.get('toId')}")
        except Exception as e:
            # a raising subscriber kills the meshtastic reader thread — never do
            print(f"on_any error (ignored): {e}")

    def keepalive(self):
        # These printers auto-power-off when idle. A heartbeat every 45 s resets that
        # timer, and "printer went missing" shows up at the console before the
        # next guest finds out the hard way. Also watches the mesh rx stream:
        # a busy mesh gone silent means the serial reader wedged.
        alive = True
        while True:
            with self.printer_lock:
                err = probe_printer(self.args)
            if err and alive:
                print(f"printer went missing: {err}")
            elif not err and not alive:
                print("printer is back")
            alive = not err
            quiet = time.monotonic() - self.last_rx
            if quiet > 900:
                print(f"no mesh packets for {int(quiet)}s — assuming wedged radio link, exiting for restart")
                os._exit(71)
            if quiet > 300:
                print(f"WARNING: no mesh packets for {int(quiet)}s")
            time.sleep(45)

    def reply(self, dest, msg):
        if dest is None:
            return
        try:
            self.interface.sendText(msg, destinationId=dest)
        except Exception as e:
            print(f"reply to {dest} failed: {e}")

    def on_text(self, packet, interface):
        try:
            self._on_text(packet)
        except Exception as e:
            # a raising subscriber kills the meshtastic reader thread — never do
            print(f"on_text error (ignored): {e}")

    def _on_text(self, packet):
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
            # Common on a huge mesh: small nodedbs evict constantly. Ask them
            # for their nodeinfo; on_user prints the badge when it arrives.
            self.pending[sender] = time.monotonic()
            self.request_nodeinfo(sender)
            self.reply(sender, "Don't know you yet — asking your node for its info. Badge prints when it answers.")
            return
        self.last_print[sender] = time.monotonic()
        self.jobs.put((sender, node))
        self.reply(sender, "Printing your nametag... come grab it!")

    def request_nodeinfo(self, dest):
        """Send our User with wantResponse — same exchange the phone apps use."""
        try:
            me = self.interface.nodesByNum.get(self.my_num, {}).get("user", {})
            u = mesh_pb2.User()
            u.id = me.get("id", "")
            u.long_name = me.get("longName", "")
            u.short_name = me.get("shortName", "")
            self.interface.sendData(u, destinationId=dest,
                                    portNum=portnums_pb2.PortNum.NODEINFO_APP,
                                    wantResponse=True)
        except Exception as e:
            print(f"nodeinfo request to {dest:#x} failed: {e}")

    def on_user(self, packet, interface):
        try:
            sender = packet.get("from")
            asked = self.pending.pop(sender, None)
            if asked is None:
                return
            if time.monotonic() - asked > 300:
                return  # they asked ages ago; don't surprise-print
            # build from the packet itself — nodedb update order isn't guaranteed
            user = packet.get("decoded", {}).get("user", {})
            node = self.interface.nodesByNum.get(sender)
            if not node or not node.get("user", {}).get("id"):
                node = {"num": sender, "user": user}
            if not node.get("user", {}).get("id"):
                return
            self.last_print[sender] = time.monotonic()
            self.jobs.put((sender, node))
            self.reply(sender, "Got your info — printing your nametag!")
        except Exception as e:
            # a raising subscriber kills the meshtastic reader thread — never do
            print(f"on_user error (ignored): {e}")

    def worker(self):
        while True:
            sender, node = self.jobs.get()
            name = node.get("user", {}).get("longName", hex(sender))
            print(f"printing badge for {name} ({sender:#x})")
            with self.printer_lock:
                ok = print_label(node, self.args, self.profile)
            if ok:
                print("  done")
            else:
                self.reply(sender, "Printer jammed/unhappy. Poke the humans at the table.")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--serial", default=None, help="radio serial port (default: auto-detect)")
    p.add_argument("--tcp", default=None, help="meshtasticd host instead of serial (e.g. localhost)")
    p.add_argument("--conn", choices=["usb", "bluetooth"], default="usb", help="printer connection")
    p.add_argument("--printer", choices=sorted(PROFILES), default="d110",
                   help="printer model and label stock: "
                        + "; ".join(f"{k} = {v.desc}" for k, v in sorted(PROFILES.items())))
    p.add_argument("--printer-port", dest="printer_addr", default=None,
                   help="printer serial port (SET THIS — auto-detect may grab the radio!) or BT MAC")
    p.add_argument("--die-cut", action="store_true",
                   help="gap-sensed die-cut labels instead of a continuous roll")
    p.add_argument("--flip", action="store_true",
                   help="rotate 180 if labels come out upside down")
    p.add_argument("--rotate", type=int, choices=[90, 270], default=None,
                   help=argparse.SUPPRESS)  # deprecated spelling of --flip
    p.add_argument("--density", type=int, choices=range(1, 6), default=3,
                   metavar="1-5", help="print density, clamped to the model's max")
    p.add_argument("--cooldown", type=int, default=600, help="per-node cooldown, seconds")
    p.add_argument("--test", action="store_true", help="print a badge for this node and exit")
    p.add_argument("--sample", action="store_true",
                   help="print/render a canned node — needs no radio")
    p.add_argument("--dry-run", action="store_true", help="write PNG instead of printing")
    p.add_argument("--out", default="/tmp/badge-test.png", help="--dry-run PNG path")
    p.add_argument("--debug", action="store_true", help="hex-dump printer packets")
    args = p.parse_args()

    if args.rotate == 270:  # --rotate 90 was the default, 270 meant "upside down"
        args.flip = True
    profile = PROFILES[args.printer]
    if args.density > profile.max_density:
        print(f"note: {profile.name} maxes out at density {profile.max_density}, "
              f"clamping {args.density}")

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


if __name__ == "__main__":
    main()
