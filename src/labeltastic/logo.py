# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The Meshtastic M-PWRD mark, drawn with PIL primitives."""

from functools import cache

from PIL import Image, ImageDraw

from .fonts import text_font

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


@cache
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
