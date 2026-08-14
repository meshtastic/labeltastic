# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Telling the radio and the printer apart. Getting this wrong means the kiosk
opens the printer as a radio (or vice versa) and hangs with no useful error."""

from dataclasses import dataclass

from labeltastic.ports import looks_like_printer, looks_like_radio


@dataclass
class FakePort:
    vid: int
    pid: int
    description: str = ""
    product: str = ""
    device: str = "/dev/ttyACM0"


def test_ch340_bridge_is_a_printer():
    assert looks_like_printer(FakePort(0x1A86, 0x7523))


def test_yichip_native_usb_is_a_printer():
    assert looks_like_printer(FakePort(0x3513, 0x0001))


def test_ch9102_is_a_radio_despite_sharing_ch340s_vendor_id():
    # 1a86 is CH340's vendor too, so a VID-only rule would call this a printer
    # and hand the kiosk's radio port to niimprint.
    p = FakePort(0x1A86, 0x55D4)
    assert looks_like_radio(p)
    assert not looks_like_printer(p)


def test_known_radio_vendors_are_radios():
    for vid in (0x10C4, 0x303A, 0x239A, 0x2886, 0x1915, 0x2E8A):
        assert looks_like_radio(FakePort(vid, 0x0001)), hex(vid)


def test_b1_matches_as_a_word_not_as_a_substring():
    assert looks_like_printer(FakePort(0x0000, 0x0000, description="Niimbot B1 Printer"))
    # "USB1"/"HUB1" are everywhere; a bare substring match would claim them all.
    assert not looks_like_printer(FakePort(0x0000, 0x0000, description="Generic USB1 Hub"))


def test_printer_names_are_matched_case_insensitively():
    for name in ("NIIMBOT D110", "niim", "Yichip Semiconductor"):
        assert looks_like_printer(FakePort(0x0000, 0x0000, description=name)), name


def test_an_unknown_device_is_neither():
    p = FakePort(0x0403, 0x6001, description="FT232R USB UART")
    assert not looks_like_printer(p)
    assert not looks_like_radio(p)
