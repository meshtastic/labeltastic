# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The QR payload is the whole point of the badge: if it doesn't decode back to
a SharedContact, the label is a souvenir rather than a contact card."""

import base64

import pytest
from meshtastic.protobuf import admin_pb2

from labeltastic.contact import SAMPLE_NODE, contact_url, name_lines, qr_image

PREFIX = "https://meshtastic.org/v/#"


def decode(url):
    b64 = url.removeprefix(PREFIX)
    sc = admin_pb2.SharedContact()
    sc.ParseFromString(base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4)))
    return sc


def test_contact_url_round_trips():
    sc = decode(contact_url(SAMPLE_NODE))
    assert sc.node_num == SAMPLE_NODE["num"]
    assert sc.user.id == "!deadbeef"
    assert sc.user.long_name == "Wandering Packet Goblin"
    assert sc.user.short_name == "🐛 WPG"
    assert sc.user.public_key == bytes(range(32))


def test_contact_url_is_unpadded_urlsafe_base64():
    # The app parses the fragment verbatim; '+' and '/' would need escaping and
    # trailing '=' is what a QR encoder would otherwise spend modules on.
    b64 = contact_url(SAMPLE_NODE).removeprefix(PREFIX)
    assert not b64.endswith("=")
    assert "+" not in b64 and "/" not in b64


def test_long_name_truncation_only_touches_the_payload():
    full = decode(contact_url(SAMPLE_NODE))
    cut = decode(contact_url(SAMPLE_NODE, 8))
    assert full.user.long_name == "Wandering Packet Goblin"
    assert cut.user.long_name == "Wandering"[:8]
    assert cut.user.short_name == full.user.short_name  # only long_name is cut


def test_truncation_never_emits_a_split_codepoint():
    # Cutting on a byte boundary mid-emoji would make the protobuf field invalid
    # UTF-8, which the app rejects outright.
    node = {"num": 1, "user": {"id": "!1", "longName": "🐛🐛🐛🐛", "shortName": "x"}}
    for cut in range(1, 17):
        decode(contact_url(node, cut))  # ParseFromString raises on bad UTF-8


def test_bad_public_key_is_dropped_not_fatal():
    node = {"num": 1, "user": {"id": "!1", "longName": "a", "publicKey": "not base64!!"}}
    assert decode(contact_url(node)).user.public_key == b""


def test_unknown_hw_model_is_dropped_not_fatal():
    node = {"num": 1, "user": {"id": "!1", "longName": "a", "hwModel": "NOT_A_REAL_BOARD"}}
    assert decode(contact_url(node)).user.hw_model == 0


@pytest.mark.parametrize("side,floor", [(96, 2), (168, 3)])
def test_qr_modules_stay_above_the_scan_floor(side, floor):
    # Below the floor the thermal print stops scanning — the reason contact_url
    # takes max_name_bytes at all.
    long_node = dict(SAMPLE_NODE)
    long_node["user"] = dict(SAMPLE_NODE["user"], longName="X" * 200)
    img = qr_image(long_node, side, min_module_px=floor)
    assert img.size == (side, side)
    # Reconstruct the module count from the rendered box size.
    assert img.width // floor >= 1


def test_qr_image_is_square_and_padded_to_the_requested_side():
    img = qr_image(SAMPLE_NODE, 96)
    assert img.size == (96, 96)
    assert img.mode == "L"


def test_name_lines_falls_back_when_nothing_printable_survives():
    node = {"num": 0xDEADBEEF, "user": {"id": "!deadbeef", "shortName": "‍", "longName": ""}}
    short, long_, nid = name_lines(node)
    assert short == "BEEF"   # low 16 bits, uppercase hex
    assert long_ == "?"
    assert nid == "!deadbeef"


def test_name_lines_synthesises_a_node_id_when_absent():
    _, _, nid = name_lines({"num": 0x0000BEEF, "user": {"shortName": "a", "longName": "b"}})
    assert nid == "!0000beef"
