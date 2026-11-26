# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The three badge layouts.

Each renders an 8-bit greyscale label in *reading* orientation; profiles.py
knows how far to rotate it for the head. See profiles.PROFILES for which
layout a printer and label stock select.
"""

from PIL import Image, ImageDraw

from .contact import name_lines, qr_image
from .fonts import condensed_font, draw_mixed, fit, is_emoji, mixed_len, text_font
from .logo import mpwrd_logo

MAX_NAME_PX = 340  # cap on the name column in banner mode

BANNER_LOGO_PX = 64  # a column of its own on the endless roll
COMPACT_LOGO_PX = 18  # bare Ms, so this is height of mark not of frame


def render_banner(node, profile):
    """Dynamic-length badge for continuous rolls: HELLO header, name, QR.

    Reads along the feed, so the short axis is the head width."""
    across = profile.head_px
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    short, long_, nid = name_lines(node)
    short, s1 = fit(probe, short, MAX_NAME_PX, 46)
    long_, s2 = fit(probe, long_, MAX_NAME_PX, 20)
    nid, s3 = fit(probe, nid, MAX_NAME_PX, 13)
    name_w = int(
        max(mixed_len(probe, short, s1), mixed_len(probe, long_, s2), mixed_len(probe, nid, s3))
    )

    hdr_w = (
        int(
            max(
                probe.textlength("HELLO", font=text_font(26)),
                probe.textlength("MY NODE IS", font=text_font(12)),
            )
        )
        + 20
    )

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


# Die-cut labels end where the gap sensor says, give or take a few rows, so ink
# keeps this far from both ends of the label.
COMPACT_END_PX = 3
# 12 mm stock is exactly the head's 96 dots, so text keeps this far from the top
# and bottom edges. The QR doesn't: a keyed contact needs all 96 at 2 dots/module.
COMPACT_EDGE_PX = 2
# Below this the text column counts as narrow (30 mm labels) and drops the band.
COMPACT_NARROW_PX = 180
# The HELLO MY NODE IS band's height on longer labels.
BAND_PX = 22


def band(label, d, x, y, w, h, text, size):
    """Black band with white text as tall as the band allows and 4 dots of black
    each side. Condensed face; if it is still too wide it is squeezed sideways
    rather than made shorter, because height is what reads from a step away."""
    d.rectangle([x, y, x + w - 1, y + h - 1], fill=0)
    f = condensed_font(size)
    left, top, right, bottom = d.textbbox((0, 0), text, font=f, anchor="ls")
    word = Image.new("L", (right - left, bottom - top), 0)
    ImageDraw.Draw(word).text((-left, -top), text, font=f, anchor="ls", fill=255)
    room = w - 8
    if word.width > room:
        word = word.resize((room, word.height), Image.Resampling.LANCZOS)
        # Grey from the resample would dither into noise at niimprint's convert("1").
        word = word.point(lambda v: 255 if v >= 128 else 0)
    label.paste(255, (x + (w - word.width) // 2, y + (h - word.height) // 2), word)


def wrap_two(d, text, width, start, min_size):
    """Split text at a space into two lines that each fit width, largest size first.
    None when it has no space or no size fits."""
    words = text.split()
    for size in range(start, min_size - 1, -1):
        for i in range(len(words) - 1, 0, -1):
            a, b = " ".join(words[:i]), " ".join(words[i:])
            if mixed_len(d, a, size) <= width and mixed_len(d, b, size) <= width:
                return (a, b), size
    return None


def wrap_lines(d, text, width, size, max_lines):
    """Greedy word wrap at `size`; the lines, or None if it needs more than
    max_lines or a single word is wider than `width`."""
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if mixed_len(d, trial, size) <= width:
            current = trial
            continue
        if not current or mixed_len(d, word, size) > width:
            return None
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines if 0 < len(lines) <= max_lines else None


def render_compact(node, profile):
    """Name tag for gap-sensed die-cut labels, any length: QR left, then a HELLO MY
    NODE IS band over the names (not on 30 mm, where the names need the height), the
    node ID and mark on the bottom row.

    A short name that is just an emoji sits on the right, above the mark, like a pin
    on a name tag, and the long name becomes the big line. Long names wrap to two lines rather than shrinking past reading.
    Reads along the feed, same as the banner."""
    across, length = profile.head_px, profile.die_len_px
    label = Image.new("L", (length, across), 255)
    d = ImageDraw.Draw(label)
    e = COMPACT_EDGE_PX

    # qr_image centres the code in an across x across square; keep only its width,
    # ink plus a one-module quiet zone, so the text column gets the slack.
    qr = qr_image(node, across)
    ink = qr.point(lambda v: 255 if v < 128 else 0).getbbox()
    if ink:
        qr = qr.crop((max(0, ink[0] - 2), 0, min(across, ink[2] + 2), across))
    label.paste(qr, (COMPACT_END_PX, 0))
    x = COMPACT_END_PX + qr.width + 6
    width = length - x - COMPACT_END_PX
    narrow = width < COMPACT_NARROW_PX

    # On a narrow (30 mm) label the band would leave the names too little room, so
    # they get its height instead; longer labels carry it.
    if narrow:
        top = e
    else:
        band(label, d, x, e, width, BAND_PX, "HELLO MY NODE IS", 21)
        top = e + BAND_PX

    logo = mpwrd_logo(COMPACT_LOGO_PX, frame=False)
    label.paste(logo, (length - COMPACT_END_PX - logo.width, across - e - logo.height))

    short, long_, nid = name_lines(node)
    # The short name is shown once, as typed. An emoji-only one (one emoji fills the
    # 4 bytes a short name allows) becomes the flair and the long name the big line;
    # anything else, emoji included, is the big line itself.
    emoji_only = all(is_emoji(c) or not c.strip() for c in short)
    flair = short.strip() if emoji_only else ""
    letters = "" if emoji_only else short
    text_w = width
    if flair and narrow:
        # 30 mm is too narrow to share: the long name takes the full width and all
        # the height above the bottom row, where the emoji sits between the node ID
        # and the mark.
        row = 22  # bottom row height: ID, emoji, mark
        row_y = across - e - row // 2
        # A dot under the row's height and a dot up: emoji glyphs reach below the
        # baseline further than text, and the 12 mm label's edge is right there.
        flair_size = row - 2
        flair_w = int(mixed_len(d, flair, flair_size))
        flair_x = length - COMPACT_END_PX - logo.width - 4 - flair_w
        draw_mixed(d, (flair_x, row_y - 1), flair, flair_size, 0)
        id_text, id_size = fit(d, nid, flair_x - x - 4, 16)
        draw_mixed(d, (x, row_y), id_text, id_size, 0)
        avail = across - e - row - 2 - top
        for name_size in range(34, 11, -1):
            lines = wrap_lines(d, long_, width, name_size, 3)
            if lines and len(lines) * (name_size + 2) <= avail:
                break
        else:
            text, name_size = fit(d, long_, width, 18)
            lines = [text]
        y = top + (avail - len(lines) * (name_size + 2)) // 2
        for line in lines:
            draw_mixed(d, (x, y + name_size // 2 + 1), line, name_size, 0)
            y += name_size + 2
        return label
    if flair:
        # Fill the space above the mark, but on a short label stop at a quarter of
        # the column so the names keep their room (never under 26: still a picture).
        size = across - e - logo.height - 4 - top - 2
        while size > 26 and mixed_len(d, flair, size) > max(26, width // 4):
            size -= 1
        flair_w = int(mixed_len(d, flair, size))
        # Centred in the space above the mark.
        flair_y = (top + across - e - logo.height - 4) // 2
        draw_mixed(d, (length - COMPACT_END_PX - flair_w, flair_y), flair, size, 0)
        text_w = width - flair_w - 6

    # Line sizes and centres below the band's (or, narrow, the label's) top edge.
    if narrow:
        big, long_max, wrapped_big, wrapped_line, under = 38, 20, 32, 16, (19, 50, 16, 40)
        alone_max, alone_wrap = 30, 24
    else:
        big, long_max, wrapped_big, wrapped_line, under = 32, 18, 24, 13, (15, 40, 11, 27)
        alone_max, alone_wrap = 26, 18
    big_y, long_y, wrapped_big_y, wrapped_y = under
    id_y = across - e - 9

    if letters:
        text, size = fit(d, long_, text_w, long_max)
        lines = wrap_two(d, long_, text_w, wrapped_line, 11) if size < 12 else None
        if lines:
            # The short name gives up a little height for the long name's second line.
            (a, b), line = lines
            short_text, short_size = fit(d, letters, text_w, wrapped_big)
            draw_mixed(d, (x, top + wrapped_big_y), short_text, short_size, 0)
            draw_mixed(d, (x, top + wrapped_y), a, line, 0)
            draw_mixed(d, (x, top + wrapped_y + line), b, line, 0)
        else:
            short_text, short_size = fit(d, letters, text_w, big)
            draw_mixed(d, (x, top + big_y), short_text, short_size, 0)
            draw_mixed(d, (x, top + long_y), text, size, 0)
    else:
        # Nothing but emoji in the short name: the long name is the name people read.
        text, size = fit(d, long_, text_w, alone_max)
        lines = wrap_two(d, long_, text_w, alone_wrap, 13) if size < 15 else None
        # Centred in the space above the ID row, which nothing else shares here.
        mid = (top + id_y - 9) // 2
        if lines:
            (a, b), line = lines
            first = mid - (line + 2) // 2
            draw_mixed(d, (x, first), a, line, 0)
            draw_mixed(d, (x, first + line + 2), b, line, 0)
        else:
            draw_mixed(d, (x, mid), text, size, 0)

    # The node ID is what people copy down, so it stays readable.
    text, size = fit(d, nid, width - logo.width - 6, 16)
    draw_mixed(d, (x, id_y), text, size, 0)
    return label


# 7 mm header band. Bigger burns a lot of dots at once: 384x56 is already
# ~21k, vs the D110 banner's ~10k.
CARD_BAND_PX = 56
# 2 mm — the 48 mm head may sit off-centre on 50 mm stock, so keep ink away
# from both edges and let it clip white.
CARD_MARGIN_PX = 16
CARD_QR_PX = 168
# Bare Ms again, filling the band: the wordmark is only 1/5 of the frame's
# height, so even here it came out under a millimetre. Rides in the band,
# costing no space.
CARD_LOGO_PX = 42


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
    label.paste(
        mpwrd_logo(CARD_LOGO_PX, invert=True, frame=False),
        (CARD_MARGIN_PX, (CARD_BAND_PX - CARD_LOGO_PX) // 2),
    )

    qr_x = w - CARD_MARGIN_PX - CARD_QR_PX
    label.paste(qr_image(node, CARD_QR_PX, min_module_px=3), (qr_x, CARD_BAND_PX + 6))

    x = CARD_MARGIN_PX
    width = qr_x - 8 - x
    short, long_, nid = name_lines(node)
    for text, start, y in ((short, 56, 110), (long_, 24, 168), (nid, 20, 206)):
        text, size = fit(d, text, width, start, min_size=12)
        draw_mixed(d, (x, y), text, size, 0)
    return label
