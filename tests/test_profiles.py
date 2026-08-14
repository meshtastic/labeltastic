# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Profile geometry and the roll -> layout -> orientation chain.

The failure mode these guard against is silent: a bad width or rotation prints
a blank or garbled label and the printer reports success either way.
"""

from types import SimpleNamespace

import pytest
from PIL import Image

from labeltastic.profiles import (
    PROFILES,
    Layout,
    Profile,
    pick_layout,
    roll_label_type,
    to_printer_orientation,
)


def dummy_layout(rotate_cw=0):
    return Layout(lambda node, profile: None, rotate_cw, "dummy")


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_head_width_divides_into_thirds_of_whole_bytes(name):
    # _encode_image_counted drops the per-third black-pixel counts unless
    # ceil(width/8) % 3 == 0, and the printer ACKs the countless rows anyway.
    head_px = PROFILES[name].head_px
    assert head_px % 24 == 0
    assert (head_px // 8) % 3 == 0


def test_a_head_off_the_24_dot_grid_is_refused_at_construction():
    with pytest.raises(ValueError, match="multiple of 24"):
        Profile(
            name="bad",
            head_px=100,
            die_len_px=236,
            die_layout=dummy_layout(),
            roll_layout=dummy_layout(),
            default_label_type=1,
            max_density=3,
            min_protocol=0,
            desc="",
        )


@pytest.mark.parametrize("label_type,expected", [(1, "die"), (2, "die"), (3, "roll")])
def test_pick_layout_maps_label_type_to_layout(label_type, expected):
    profile = PROFILES["d110"]
    got = pick_layout(profile, label_type)
    assert got is (profile.die_layout if expected == "die" else profile.roll_layout)


def test_die_cut_flag_wins_without_consulting_the_roll():
    def explode():
        raise AssertionError("should not have asked the roll")

    args = SimpleNamespace(die_cut=True)
    client = SimpleNamespace(get_rfid=explode)
    assert roll_label_type(client, args, PROFILES["d110"]) == 1


def test_unreadable_rfid_falls_back_to_the_profile_default(capsys):
    def boom():
        raise RuntimeError("no tag")

    args = SimpleNamespace(die_cut=False)
    client = SimpleNamespace(get_rfid=boom)
    for name in ("d110", "b1"):
        profile = PROFILES[name]
        assert roll_label_type(client, args, profile) == profile.default_label_type
    # The old code swallowed this; a wrong default is worth a line on the console.
    assert "RFID read failed" in capsys.readouterr().out


def test_a_readable_rfid_tag_selects_the_label_type():
    args = SimpleNamespace(die_cut=False)
    for tag_type in (1, 2, 3):
        client = SimpleNamespace(get_rfid=lambda t=tag_type: {"type": t})
        assert roll_label_type(client, args, PROFILES["d110"]) == tag_type


def test_a_nonsense_rfid_type_falls_back_to_the_default():
    args = SimpleNamespace(die_cut=False)
    client = SimpleNamespace(get_rfid=lambda: {"type": 99})
    assert roll_label_type(client, args, PROFILES["d110"]) == 3


@pytest.mark.parametrize("name", sorted(PROFILES))
def test_every_layout_ends_up_exactly_head_wide(name):
    profile = PROFILES[name]
    for layout in (profile.die_layout, profile.roll_layout):
        # Author at the layout's own orientation, then turn it to face the head.
        authored = (
            (profile.die_len_px, profile.head_px)
            if layout.rotate_cw
            else (profile.head_px, profile.die_len_px)
        )
        img = to_printer_orientation(Image.new("L", authored, 255), layout, profile)
        assert img.width == profile.head_px


def test_a_narrow_label_is_centred_rather_than_hugging_an_edge():
    profile = PROFILES["b1"]
    layout = dummy_layout(0)
    img = Image.new("L", (100, 50), 0)  # all ink, so the padding is visible
    out = to_printer_orientation(img, layout, profile)
    assert out.width == 384
    left = (384 - 100) // 2
    assert out.getpixel((left - 1, 25)) == 255  # padding
    assert out.getpixel((left + 1, 25)) == 0  # the label itself


def test_an_oversized_label_is_a_layout_bug_not_a_printer_fault():
    with pytest.raises(ValueError, match="LAYOUT BUG"):
        to_printer_orientation(Image.new("L", (500, 50)), dummy_layout(0), PROFILES["b1"])


def test_flip_adds_180_degrees():
    profile = PROFILES["b1"]
    img = Image.new("L", (384, 100), 255)
    img.putpixel((0, 0), 0)  # top-left corner marker
    flipped = to_printer_orientation(img, dummy_layout(0), profile, flip=True)
    assert flipped.getpixel((383, 99)) == 0
    assert flipped.getpixel((0, 0)) == 255
