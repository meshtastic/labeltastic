# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Turning a nodedb entry into the things a badge shows: a shared-contact URL,
the QR that encodes it, and the three lines of name text."""

import base64

import qrcode
from meshtastic.protobuf import admin_pb2, mesh_pb2
from PIL import Image

from .fonts import clean


def contact_url(node, max_name_bytes=None):
    """Build https://meshtastic.org/v/#<base64(SharedContact)> from a nodedb entry."""
    user = node.get("user", {})
    u = mesh_pb2.User()
    u.id = user.get("id", "")
    long_name = user.get("longName", "")
    if max_name_bytes is not None:
        long_name = long_name.encode()[:max_name_bytes].decode(errors="ignore")
    u.long_name = long_name
    u.short_name = user.get("shortName", "")
    pk = user.get("publicKey")
    if pk:
        try:
            u.public_key = base64.b64decode(pk)
        except Exception:
            pass
    hw = user.get("hwModel")
    if hw:
        try:
            u.hw_model = mesh_pb2.HardwareModel.Value(hw)
        except ValueError:
            pass
    sc = admin_pb2.SharedContact(node_num=node["num"], user=u)
    b64 = base64.urlsafe_b64encode(sc.SerializeToString()).decode().rstrip("=")
    return f"https://meshtastic.org/v/#{b64}"


def qr_image(node, side, min_module_px=2):
    # Modules must stay >= min_module_px printer dots (2 dots = 0.25 mm) or
    # phones can't scan the thermal print. Long names push the QR past version 7
    # and modules below the floor, so progressively truncate the long name in the
    # QR payload only — the printed text keeps the full name. A bigger `side`
    # wants a bigger floor too, or a long name silently buys extra modules
    # instead of the extra dots per module the room was meant for.
    for cut in (None, 32, 24, 16):
        url = contact_url(node, cut)
        qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=1)
        qr.add_data(url)
        qr.make(fit=True)
        if (qr.modules_count + 2) * min_module_px <= side:
            break
    # Integer module size that fits the head; label margin supplies the quiet zone.
    qr.box_size = max(1, side // (qr.modules_count + 2))
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    canvas = Image.new("L", (side, side), 255)
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return canvas


# --sample: the public key is 2/3 of the QR payload, so a node without one
# renders a comfortably small QR and proves nothing about the real thing. The
# emoji and the long name exercise clean()/draw_mixed() and fit()'s ellipsizing.
SAMPLE_NODE = {
    "num": 0xDEADBEEF,
    "user": {
        "id": "!deadbeef",
        "longName": "Wandering Packet Goblin",
        "shortName": "🐛 WPG",
        "publicKey": base64.b64encode(bytes(range(32))).decode(),
        "hwModel": "HELTEC_V3",
    },
}


def name_lines(node):
    user = node.get("user", {})
    short = clean(user.get("shortName", ""))
    if not short:  # nothing printable survived — fall back to the hex suffix
        short = f"{node['num'] & 0xFFFF:04X}"
    long_ = clean(user.get("longName", "")) or "?"
    nid = user.get("id", f"!{node['num']:08x}")
    return short, long_, nid
