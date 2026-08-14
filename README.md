# HELLO MY NODE IS

Meshtastic contact-QR nametag kiosk for a Raspberry Pi + a Niimbot label printer.
DM the kiosk node the word `print` over the mesh and it prints your nametag:
a black HELLO-MY-NODE-IS header, your short/long name (emoji included),
node ID, a shared-contact QR (`https://meshtastic.org/v/#...`) that
anyone can scan in the Meshtastic app (>= 2.6) to add you as a contact,
PKC public key included, and the Meshtastic M-PWRD mark.

Built at DEFCON 34. 🦞

![Two printed nametags resting on a Niimbot label printer: the landscape card
layout on 50x30 mm stock above, showing the M-PWRD mark, a caterpillar emoji,
the short name WPG, the long name Wandering Packet Goblin, the node ID
!deadbeef and a contact QR; the compact layout below, with its QR on the left
and the mark bottom-right](docs/printed-badges.jpg)

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

![Close-up of the card layout as printed, showing the header band, emoji,
names, node ID and QR at full detail](docs/badge-closeup.jpg)

## Setup

On the Pi:

```bash
sudo apt install -y python3-venv fonts-dejavu-core
python3 -m venv ~/badge
~/badge/bin/pip install labeltastic
```

That puts a `labeltastic` command in `~/badge/bin/`. The emoji font ships
with the package; `fonts-dejavu-core` is the text font and comes from the
system. Python 3.11 or newer.

`labeltastic` sorts out which USB serial port is the radio and which is the
printer by USB vendor/product IDs (the D110 is a CH340; radios are
CP210x/CH9102/ESP32/nRF52/RP2040) and prints its port table at startup.
Just run it:

```bash
~/badge/bin/labeltastic --sample --dry-run          # render only, no radio needed
~/badge/bin/labeltastic --test                      # one real test print
~/badge/bin/labeltastic                             # run the kiosk
~/badge/bin/labeltastic --printer b1 --die-cut      # kiosk on a B1
```

`--sample` renders a canned node, so `--sample --dry-run` works with
nothing plugged in at all. It writes the PNG (`--out` to redirect) and then
runs the real bitmap encoder over it, reporting packet count and per-third
pixel counts — the only check on the wire format you can make without the
printer in front of you.

If it can't tell the ports apart, it exits with the table and tells you
what to pass — use the stable names in `/dev/serial/by-id/` for
`--serial` / `--printer-port` so a replug can't shuffle them. The B1's
USB IDs aren't known here, so pass `--printer-port` explicitly for one.

If labels come out flipped, add `--flip` (the old `--rotate 270` still works).

## Development

```bash
uv sync --extra dev
ruff check . && mypy && python scripts/check_spdx.py && pytest
```

None of it needs a printer or a radio. [CONTRIBUTING.md](CONTRIBUTING.md) has
the details, [AGENTS.md](AGENTS.md) the module map and the invariants that
will bite you (starting with: `head_px` must be a multiple of 24, or the
printer prints blank and reports success).

## Notes

- DM-triggered only, with a per-node cooldown — broadcast triggers would
  melt the label roll on a con mesh.
- Long names are truncated inside the QR payload (not on the printed label)
  to keep QR modules >= 2 printer dots (3 on the B1's roomier card), or
  phones can't scan the thermal print.
- Print density is capped per model (`--density`): 3 on the D110, 5 on the B1.
- `src/labeltastic/assets/NotoEmoji-Regular.ttf` is Google's monochrome Noto
  Emoji, vendored from
  [google/fonts](https://github.com/google/fonts/tree/main/ofl/notoemoji)
  under the SIL Open Font License 1.1 (`assets/OFL.txt`).
- [niimprint](https://github.com/AndBondStyle/niimprint) is vendored under
  `src/labeltastic/_vendor/` (MIT, © kjy00302 — AndBondStyle's repo is the
  maintained fork of that original) because upstream's packaging pins Python
  to 3.11.x, which modern Pi OS is way past, while the code itself runs fine
  on 3.14. See its [README](src/labeltastic/_vendor/README.md) for the two
  local changes.
- The M-PWRD mark is redrawn with PIL primitives from `M-PWRD_BW_Border.svg`
  in [meshtastic/design](https://github.com/meshtastic/design) — the geometry
  is a dozen coordinates, which beats putting an SVG rasteriser (and native
  cairo) on the Pi for one 40-dot glyph. It is supersampled and hard
  thresholded rather than left anti-aliased, because niimprint dithers any
  grey it is handed and that turns a mark this small into noise. Only the
  banner prints the whole logo, in a column of its own — it can just grow.
  The B1 card and the compact 30x15 layout print the bare Ms, no border and
  no wordmark: PWRD is a fifth of the frame's height, so a logo sized to fit
  the space those two had spare sets it under a millimetre.
