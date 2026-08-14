# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Printer profiles: what head a model has, what stock it runs, which layout
that selects, and how the authored label is turned to face the head."""

from collections import namedtuple
from dataclasses import dataclass

from PIL import Image

from .render import render_banner, render_card, render_compact

DOTS_PER_MM = 8  # 203 dpi heads, near enough (7.992 dots/mm)

# render_* in render.py, table below: the profiles name the renderers.
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
    head_px: int  # dots across the head
    die_len_px: int  # feed rows for one die-cut label
    die_layout: Layout  # label types 1 (gap) and 2 (black mark)
    roll_layout: Layout  # label type 3 (continuous)
    default_label_type: int  # used when the roll has no readable RFID tag
    max_density: int
    min_protocol: int  # force the v3+ print path even if the printer lies
    desc: str

    def __post_init__(self):
        # _encode_image_counted splits the black-pixel counts over thirds of the
        # head and silently ships zeros unless ceil(width/8) divides by 3; widths
        # off a byte boundary additionally shift every row (its to_bytes right-
        # aligns). Both are blank or garbled prints with no error, so refuse the
        # geometry here rather than debug it on the label roll.
        if self.head_px % 24:
            raise ValueError(f"{self.name}: head_px must be a multiple of 24, got {self.head_px}")


PROFILES = {
    "d110": Profile(
        name="d110",
        head_px=96,
        die_len_px=236,
        die_layout=Layout(render_compact, 90, "compact 30x15"),
        roll_layout=Layout(render_banner, 90, "banner"),
        default_label_type=3,  # no tag — assume the endless roll they planned on
        max_density=3,  # upstream caps d11/d110/b18 here
        min_protocol=0,
        desc="12 mm head, 30x15 mm die-cut or continuous roll",
    ),
    "b1": Profile(
        name="b1",
        head_px=384,
        die_len_px=236,
        # 50x30 stock is 50 mm across the roll and 30 mm along the feed, so the
        # card is laid out across the head. Continuous rolls print the same
        # fixed-length page: a banner scaled 4x would be 30 cm per badge.
        die_layout=Layout(render_card, 0, "card 50x30"),
        roll_layout=Layout(render_card, 0, "card 50x30"),
        default_label_type=1,  # 50x30 is die-cut; type 3 would drift every label
        max_density=5,
        min_protocol=3,
        desc="48 mm head, 50x30 mm die-cut labels",
    ),
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
        print(f"  RFID read failed ({e}) — assuming label type {profile.default_label_type}")
        rfid = None
    if rfid and rfid.get("type") in (1, 2, 3):
        return rfid["type"]
    return profile.default_label_type


def pick_layout(profile, label_type):
    return profile.die_layout if label_type in (1, 2) else profile.roll_layout


# PIL names rotations counter-clockwise; every angle here is clockwise.
CW_TRANSPOSE = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def to_printer_orientation(img, layout, profile, flip=False):
    """Turn an authored label into what the head prints: width = dots across the
    head, height = feed rows (see set_dimension's arg order in niimprint)."""
    cw = (layout.rotate_cw + (180 if flip else 0)) % 360
    if cw:
        img = img.transpose(CW_TRANSPOSE[cw])
    if img.width > profile.head_px:
        raise ValueError(
            f"LAYOUT BUG: {layout.desc} is {img.width} dots across but the "
            f"{profile.name} head is only {profile.head_px} — not a printer fault"
        )
    if img.width < profile.head_px:  # centre it rather than hug one edge
        canvas = Image.new("L", (profile.head_px, img.height), 255)
        canvas.paste(img, ((profile.head_px - img.width) // 2, 0))
        img = canvas
    return img
