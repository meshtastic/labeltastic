# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The connect link is what goes on the box: if the address or PIN in it is wrong,
the QR opens the app on a node that isn't there, or pairs with the wrong PIN."""

from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from meshtastic.protobuf import mesh_pb2

from labeltastic.provision import (
    BLE_ADDRESS_FIELD,
    LINK_BASES,
    ble_address_from_macaddr,
    ble_address_from_metadata,
    connect_uri,
    node_ports,
    random_pin,
    raw_string_field,
)

ADDR = "AC:27:6E:CD:5A:99"


def metadata_with_ble_address(address):
    """DeviceMetadata as new firmware sends it, whether or not this meshtastic knows field 17."""
    md = mesh_pb2.DeviceMetadata(firmware_version="2.8.0", hasBluetooth=True, hw_model=43)
    raw = address.encode()
    key = bytes([(BLE_ADDRESS_FIELD << 3 | 2) & 0x7F | 0x80, (BLE_ADDRESS_FIELD << 3 | 2) >> 7])
    parsed = mesh_pb2.DeviceMetadata()
    parsed.ParseFromString(md.SerializeToString() + key + bytes([len(raw)]) + raw)
    return parsed


def test_ble_address_survives_a_meshtastic_without_the_field():
    # The field reaches older meshtastic packages as an unknown field; it must still be read.
    md = metadata_with_ble_address(ADDR.lower())
    assert md.firmware_version == "2.8.0"
    assert ble_address_from_metadata(md) == ADDR
    # Direct, so the raw path stays covered once meshtastic knows field 17.
    assert raw_string_field(md.SerializeToString(), BLE_ADDRESS_FIELD) == ADDR.lower()


def test_no_ble_address_reads_as_empty():
    assert ble_address_from_metadata(mesh_pb2.DeviceMetadata(firmware_version="2.7.0")) == ""
    assert ble_address_from_metadata(None) == ""


def test_raw_string_field_skips_other_wire_types():
    # varint (tag 2), fixed32 (tag 3), fixed64 (tag 4), then the string at tag 17
    msg = bytes([0x10, 0x96, 0x01, 0x1D, 1, 2, 3, 4, 0x21, *range(8), 0x8A, 0x01, 2]) + b"hi"
    assert raw_string_field(msg, 17) == "hi"
    assert raw_string_field(msg, 9) == ""


def test_macaddr_fallback_formats_six_bytes_only():
    assert ble_address_from_macaddr({"macaddr": "rCduzVqZ"}) == ADDR
    assert ble_address_from_macaddr({"macaddr": ""}) == ""
    assert ble_address_from_macaddr({}) == ""


def test_connect_uri_web_and_app_forms():
    web = connect_uri(LINK_BASES["web"], ADDR, "317338")
    app = connect_uri(LINK_BASES["app"], ADDR, "317338")
    assert web == f"https://meshtastic.org/connections?address=x{ADDR}&pin=317338"
    assert app == f"meshtastic://meshtastic/connections?address=x{ADDR}&pin=317338"


def test_connect_uri_parses_back_to_the_prefixed_address_and_pin():
    # The app reads address= with its transport prefix (x = BLE) and pin= as exactly 6 digits.
    q = parse_qs(urlsplit(connect_uri(LINK_BASES["web"], ADDR, "012345")).query)
    assert q == {"address": [f"x{ADDR}"], "pin": ["012345"]}


def test_random_pin_is_six_digits_and_never_zero():
    for _ in range(500):
        pin = random_pin()
        assert len(pin) == 6 and pin.isdigit() and int(pin) >= 100_000


@dataclass
class FakePort:
    vid: int | None
    pid: int = 0x0001
    description: str = ""
    product: str = ""
    device: str = "/dev/ttyACM0"


def test_node_ports_never_include_the_label_printer():
    ports = [
        FakePort(0x239A, device="/dev/ttyACM0"),  # nRF52 node
        FakePort(0x1A86, 0x55D4, device="/dev/ttyACM1"),  # CH9102 radio
        FakePort(0x1A86, 0x7523, device="/dev/ttyUSB0"),  # CH340 D110 printer
        FakePort(None, device="/dev/ttyS0"),  # on-board UART, not USB
    ]
    assert node_ports(ports) == {"/dev/ttyACM0", "/dev/ttyACM1"}
