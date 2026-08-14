# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Fonts, and drawing text that mixes glyphs with emoji.

Text comes from a system DejaVu; emoji come from the monochrome Noto Emoji
shipped in assets/. Characters neither font can print are dropped rather than
rendered as stray boxes on a thermal label.
"""

import itertools
from functools import cache
from pathlib import Path

from PIL import ImageFont

ASSETS = Path(__file__).resolve().parent / "assets"
TEXT_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # dev on mac
]
EMOJI_FONTS = [
    str(ASSETS / "NotoEmoji-Regular.ttf"),
    "/usr/share/fonts/truetype/noto/NotoEmoji-Regular.ttf",
]


@cache
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
        draw.textlength(r, font=(emoji_font(size) if e else text_font(size))) for e, r in runs(text)
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
