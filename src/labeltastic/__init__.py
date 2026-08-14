# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Meshtastic contact-QR nametag kiosk for Niimbot label printers."""

try:
    from ._version import __version__
except ImportError:  # source tree that was never built — hatch-vcs writes it
    __version__ = "0.0.0+unknown"

__all__ = ["__version__"]
