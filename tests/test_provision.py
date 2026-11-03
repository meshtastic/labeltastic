# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The connect link is what goes on the box: if the address or PIN in it is wrong,
the QR opens the app on a node that isn't there, or pairs with the wrong PIN."""

import csv
import time
from dataclasses import dataclass
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from meshtastic import serial_interface
from meshtastic.protobuf import localonly_pb2, mesh_pb2

from labeltastic.provision import (
    BLE_ADDRESS_FIELD,
    LINK_BASES,
    ble_address_from_macaddr,
    ble_address_from_metadata,
    connect_uri,
    node_ports,
    provision,
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


class FakeRadio:
    """One node, as every SerialInterface opened on its port sees it."""

    def __init__(self, md, wifi_on=False, accepts=True, write_error=None, comes_back=True):
        self.md = md
        self.config = localonly_pb2.LocalConfig()
        self.config.network.wifi_enabled = wifi_on
        self.accepts = accepts
        self.write_error = write_error
        self.comes_back = comes_back
        self.opened = 0

    def open(self, devPath, timeout):
        if self.opened and not self.comes_back:
            raise OSError("no such port")
        self.opened += 1
        config = localonly_pb2.LocalConfig()
        config.CopyFrom(self.config)

        def write(name):
            if self.write_error:
                raise self.write_error
            if self.accepts:
                self.config.bluetooth.CopyFrom(config.bluetooth)

        return SimpleNamespace(
            metadata=self.md,
            localNode=SimpleNamespace(localConfig=config, writeConfig=write),
            getMyUser=lambda: {"id": "!cd5a99"},
            close=lambda: None,
        )


@pytest.fixture
def run(monkeypatch, tmp_path):
    monkeypatch.setattr(time, "sleep", lambda s: None)

    def run(radio, macaddr_fallback=False):
        monkeypatch.setattr(serial_interface, "SerialInterface", radio.open)
        args = SimpleNamespace(
            timeout=0.2, out=tmp_path, link="web", macaddr_fallback=macaddr_fallback
        )
        return provision("/dev/ttyACM0", args, set())

    return run


def csv_rows(tmp_path):
    path = tmp_path / "provisioned.csv"
    return list(csv.DictReader(path.open())) if path.exists() else []


def test_a_pin_the_node_took_is_verified_and_matches_the_csv(run, tmp_path):
    radio = FakeRadio(metadata_with_ble_address(ADDR))
    assert run(radio) == "verified"
    [row] = csv_rows(tmp_path)
    assert row["status"] == "verified"
    assert int(row["pin"]) == radio.config.bluetooth.fixed_pin


def test_a_pin_the_node_refused_is_a_mismatch(run, tmp_path):
    assert run(FakeRadio(metadata_with_ble_address(ADDR), accepts=False)) == "mismatch"
    assert csv_rows(tmp_path)[0]["status"] == "mismatch"


def test_a_node_that_never_comes_back_is_unverified(run, tmp_path):
    assert run(FakeRadio(metadata_with_ble_address(ADDR), comes_back=False)) == "unverified"
    assert csv_rows(tmp_path)[0]["status"] == "unverified"


def test_a_failed_write_marks_the_row_and_raises(run, tmp_path):
    with pytest.raises(OSError):
        run(FakeRadio(metadata_with_ble_address(ADDR), write_error=OSError("serial dropped")))
    assert csv_rows(tmp_path)[0]["status"] == "write failed"


def test_wifi_on_skips_the_node_before_any_pin(run, tmp_path):
    md = metadata_with_ble_address(ADDR)
    md.hasWifi = True
    radio = FakeRadio(md, wifi_on=True)
    assert run(radio) is None
    assert csv_rows(tmp_path) == []
    assert not radio.config.bluetooth.fixed_pin


def test_macaddr_fallback_refuses_a_node_with_wifi(run, tmp_path):
    md = mesh_pb2.DeviceMetadata(firmware_version="2.7.0", hasWifi=True)
    assert run(FakeRadio(md), macaddr_fallback=True) is None
    assert csv_rows(tmp_path) == []
