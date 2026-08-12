# HELLO MY NODE IS

Meshtastic contact-QR nametag kiosk for a Raspberry Pi + a Niimbot label printer.
DM the kiosk node the word `print` over the mesh and it prints your nametag:
a black HELLO-MY-NODE-IS header, your short/long name (emoji included),
node ID, a shared-contact QR (`https://meshtastic.org/v/#...`) that
anyone can scan in the Meshtastic app (>= 2.6) to add you as a contact,
PKC public key included, and the Meshtastic M-PWRD mark.

Built at DEFCON 34. 🦞

## Hardware

- Raspberry Pi with a Meshtastic node attached (USB serial radio, or
  meshtasticd with a LoRa hat — use `--tcp localhost` for the latter)
- A Niimbot label printer, USB-C cable strongly recommended
  (2.4 GHz at DEFCON is a war zone). Pick yours with `--printer`:

  | `--printer` | head | labels | layout |
  |---|---|---|---|
  | `d110` (default) | 12 mm / 96 dots | continuous roll, or `--die-cut` 30x15 mm | dynamic-length banner, or a compact QR-left card |
  | `b1` | 48 mm / 384 dots | 50x30 mm die-cut | landscape card: header band, name block, QR right |

  The 50x30 mm stock is 50 mm across the roll and 30 mm along the feed, so
  the B1 card is laid out across the head instead of along it. Its QR gets
  3 printer dots per module against the D110's 2, which means full long
  names survive into the QR payload instead of being truncated.

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
~/badge/bin/python badge_printer.py --test --dry-run --sample   # render only, no radio needed
~/badge/bin/python badge_printer.py --test                      # one real test print
~/badge/bin/python badge_printer.py                             # run the kiosk
~/badge/bin/python badge_printer.py --printer b1 --die-cut      # kiosk on a B1
```

`--sample` renders a canned node, so `--test --dry-run --sample` works with
nothing plugged in at all. It writes the PNG (`--out` to redirect) and then
runs the real bitmap encoder over it, reporting packet count and per-third
pixel counts — the only check on the wire format you can make without the
printer in front of you.

If it can't tell the ports apart, it exits with the table and tells you
what to pass — use the stable names in `/dev/serial/by-id/` for
`--serial` / `--printer-port` so a replug can't shuffle them. The B1's
USB IDs aren't known here, so pass `--printer-port` explicitly for one.

If labels come out flipped, add `--flip` (the old `--rotate 270` still works).

## Notes

- DM-triggered only, with a per-node cooldown — broadcast triggers would
  melt the label roll on a con mesh.
- Long names are truncated inside the QR payload (not on the printed label)
  to keep QR modules >= 2 printer dots (3 on the B1's roomier card), or
  phones can't scan the thermal print.
- Print density is capped per model (`--density`): 3 on the D110, 5 on the B1.
- `NotoEmoji-Regular.ttf` is Google's monochrome Noto Emoji, vendored from
  [google/fonts](https://github.com/google/fonts/tree/main/ofl/notoemoji)
  under the SIL Open Font License 1.1.
- The M-PWRD mark is redrawn with PIL primitives from `M-PWRD_BW_Border.svg`
  in [meshtastic/design](https://github.com/meshtastic/design) — the geometry
  is a dozen coordinates, which beats putting an SVG rasteriser (and native
  cairo) on the Pi for one 40-dot glyph. It is supersampled and hard
  thresholded rather than left anti-aliased, because niimprint dithers any
  grey it is handed and that turns a mark this small into noise. The banner
  gives it a column of its own and the B1 card drops it into the header band;
  the compact 30x15 layout prints the bare Ms, no border and no wordmark,
  since a full logo small enough to fit beside the node ID would set PWRD at
  about 0.75 mm.
