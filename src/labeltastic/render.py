# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The three badge layouts.

Each renders an 8-bit greyscale label in *reading* orientation; profiles.py
knows how far to rotate it for the head. See profiles.PROFILES for which
layout a printer and label stock select.
"""

from PIL import Image, ImageDraw

from .contact import name_lines, qr_image
from .fonts import draw_mixed, fit, mixed_len, text_font
from .logo import mpwrd_logo

MAX_NAME_PX = 340       # cap on the name column in banner mode

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
