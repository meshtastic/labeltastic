# HELLO MY NODE IS

Meshtastic contact-QR nametag kiosk for a Raspberry Pi + Niimbot D110.
DM the kiosk node the word `print` over the mesh and it prints your nametag:
a black HELLO-MY-NODE-IS header, your short/long name (emoji included),
node ID, and a shared-contact QR (`https://meshtastic.org/v/#...`) that
anyone can scan in the Meshtastic app (>= 2.6) to add you as a contact,
PKC public key included.

Built at DEFCON 34. 🦞

## Hardware

- Raspberry Pi with a Meshtastic node attached (USB serial radio, or
  meshtasticd with a LoRa hat — use `--tcp localhost` for the latter)
- Niimbot D110 label printer, USB-C cable strongly recommended
  (2.4 GHz at DEFCON is a war zone)
- Continuous (endless) label roll by default; `--die-cut` for 30x15 mm
  gap-sensed labels

## Setup

```bash
sudo apt install -y python3-venv fonts-dejavu-core
python3 -m venv ~/badge
~/badge/bin/pip install meshtastic qrcode pillow pypubsub pyserial
```

[niimprint](https://github.com/AndBondStyle/niimprint) is vendored in
`niimprint/` (MIT) — upstream's packaging pins Python to 3.11.x, which
modern Pi OS is way past, and the code itself runs fine on 3.14.

The script sorts out which USB serial port is the radio and which is the
printer by USB vendor/product IDs (the D110 is a CH340; radios are
CP210x/CH9102/ESP32/nRF52/RP2040) and prints its port table at startup.
Just run it:

```bash
~/badge/bin/python badge_printer.py --test --dry-run     # render only, writes /tmp/badge-test.png
~/badge/bin/python badge_printer.py --test               # one real test print
~/badge/bin/python badge_printer.py                      # run the kiosk
```

If it can't tell the ports apart, it exits with the table and tells you
what to pass — use the stable names in `/dev/serial/by-id/` for
`--serial` / `--printer-port` so a replug can't shuffle them.

If labels come out flipped, add `--rotate 270`.

## Notes

- DM-triggered only, with a per-node cooldown — broadcast triggers would
  melt the label roll on a con mesh.
- Long names are truncated inside the QR payload (not on the printed label)
  to keep QR modules >= 2 printer dots, or phones can't scan the thermal print.
- `NotoEmoji-Regular.ttf` is Google's monochrome Noto Emoji, vendored from
  [google/fonts](https://github.com/google/fonts/tree/main/ofl/notoemoji)
  under the SIL Open Font License 1.1.
