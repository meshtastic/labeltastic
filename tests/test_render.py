# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""End-to-end render and encode, with no printer and no radio.

Deliberately not pixel-golden: the text fonts come from the host, so exact
pixels differ between a Pi, a CI runner and a dev laptop. What must hold
everywhere is the geometry and the wire format.
"""

import struct

import pytest
from PIL import Image

from labeltastic._vendor.niimprint import PrinterClient
from labeltastic.contact import SAMPLE_NODE
from labeltastic.fonts import TEXT_FONTS, _first_font
from labeltastic.logo import mpwrd_logo
from labeltastic.profiles import PROFILES, pick_layout, to_printer_orientation

needs_a_text_font = pytest.mark.skipif(
    _first_font(TEXT_FONTS, 12) is None,
    reason="no DejaVu/Arial on this host — install fonts-dejavu-core",
)


def encode(img):
    return list(PrinterClient._encode_image_counted(None, img))


def render(profile_name, label_type):
    profile = PROFILES[profile_name]
    layout = pick_layout(profile, label_type)
    img = layout.render(SAMPLE_NODE, profile)
    return profile, layout, img


CASES = [("d110", 3), ("d110", 1), ("b1", 1)]


@needs_a_text_font
@pytest.mark.parametrize("profile_name,label_type", CASES)
def test_render_produces_a_printable_page(profile_name, label_type):
    profile, layout, img = render(profile_name, label_type)
    out = to_printer_orientation(img, layout, profile)
    assert out.width == profile.head_px
    assert out.height > 0
    assert img.mode == "L"


@needs_a_text_font
@pytest.mark.parametrize("profile_name,label_type", CASES)
def test_die_cut_pages_never_overrun_the_label(profile_name, label_type):
    profile, layout, img = render(profile_name, label_type)
    if layout is not profile.die_layout:
        pytest.skip("continuous roll — length is free")
    out = to_printer_orientation(img, layout, profile)
    assert out.height <= profile.die_len_px


@needs_a_text_font
@pytest.mark.parametrize("profile_name,label_type", CASES)
def test_every_inked_row_carries_its_per_third_counts(profile_name, label_type):
    # The failure this catches prints blank or garbled with no error from the
    # printer — see Profile.__post_init__.
    profile, layout, img = render(profile_name, label_type)
    pkts = encode(to_printer_orientation(img, layout, profile))
    inked = [p for p in pkts if p.type == 0x85]
    assert inked, "a badge with no ink is not a badge"
    assert not [p for p in inked if p.data[2:5] == b"\x00\x00\x00"]


@needs_a_text_font
@pytest.mark.parametrize("profile_name,label_type", CASES)
def test_the_page_has_ink_in_every_third_of_its_length(profile_name, label_type):
    # A layout that renders its QR off-canvas still passes the geometry checks;
    # this notices a third of the label coming out blank.
    _, _, img = render(profile_name, label_type)
    long_axis = max(img.size)
    horizontal = img.width >= img.height
    for i in range(3):
        lo, hi = long_axis * i // 3, long_axis * (i + 1) // 3
        band = img.crop((lo, 0, hi, img.height) if horizontal else (0, lo, img.width, hi))
        assert band.getextrema()[0] < 128, f"third {i} of the label is blank"


def test_a_blank_page_encodes_as_empty_rows_not_inked_ones():
    pkts = encode(Image.new("L", (96, 32), 255))
    assert pkts
    assert all(p.type == 0x84 for p in pkts)  # 0x84 = blank-row run


def test_identical_rows_compress_into_one_packet():
    img = Image.new("L", (96, 40), 255)
    for y in range(40):
        img.putpixel((0, y), 0)  # 40 identical inked rows
    inked = [p for p in encode(img) if p.type == 0x85]
    assert len(inked) == 1
    # header is >H3BB: row, three per-third counts, repeat — then the row bytes
    row, c0, c1, c2, repeat = struct.unpack(">H3BB", inked[0].data[:6])
    assert (row, repeat) == (0, 40)
    assert (c0, c1, c2) == (1, 0, 0)  # the single lit pixel, in the first third
    assert len(inked[0].data) == 6 + 96 // 8


@needs_a_text_font
@pytest.mark.parametrize("frame", [True, False])
def test_the_logo_is_pure_black_and_white(frame):
    # Anti-aliased grey reaches niimprint's convert("1"), which dithers it into
    # noise at this size.
    assert set(mpwrd_logo(48, frame=frame).tobytes()) <= {0, 255}


@needs_a_text_font
def test_inverting_the_logo_swaps_ink_and_paper():
    normal = mpwrd_logo(40, frame=False)
    inverted = mpwrd_logo(40, invert=True, frame=False)
    assert normal.size == inverted.size
    assert bytes(255 - p for p in normal.tobytes()) == inverted.tobytes()


@needs_a_text_font
def test_the_framed_logo_is_taller_relative_to_its_width_than_the_bare_mark():
    # frame=True adds the border and the PWRD band below the Ms.
    framed, bare = mpwrd_logo(64), mpwrd_logo(64, frame=False)
    assert framed.height == bare.height == 64
    assert framed.width < bare.width
