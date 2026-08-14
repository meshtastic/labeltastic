# AGENTS.md

Working notes for labeltastic — a Meshtastic contact-QR nametag kiosk that drives a Niimbot
label printer. Read [CONTRIBUTING.md](CONTRIBUTING.md) for setup and the gates; this file is
about the code.

## The shape of it

A mesh DM comes in, a badge goes out:

```
kiosk.py     mesh DM ──▶ trigger match ──▶ cooldown ──▶ job queue
                              │
                              └─ sender unknown? request nodeinfo, print when it answers
printing.py  job ──▶ open printer ──▶ ask the roll what it is ──▶ pick layout ──▶ print
profiles.py                             roll_label_type ──▶ pick_layout ──▶ to_printer_orientation
render.py                                                   render_banner / _compact / _card
contact.py                                                  contact_url ──▶ qr_image
```

| module | holds |
| --- | --- |
| `cli.py` | argparse, and the three modes: kiosk, `--test`, `--sample` |
| `kiosk.py` | mesh subscriptions, per-node cooldown, job queue, printer/radio watchdogs |
| `printing.py` | the print sequence, retries, and `dump_packets` for dry runs |
| `profiles.py` | the printer/stock table, layout selection, rotation onto the head |
| `render.py` | the three layouts |
| `logo.py` | the M-PWRD mark, drawn from SVG coordinates with PIL primitives |
| `contact.py` | SharedContact URL, the QR, and the three name lines |
| `fonts.py` | font lookup, and drawing text that mixes glyphs with emoji |
| `ports.py` | which serial port is the radio, which is the printer |
| `_vendor/niimprint` | the printer driver (MIT, upstream) — see its README |

## Invariants worth knowing before you change anything

- **`head_px` must be a multiple of 24.** The encoder splits per-third black-pixel counts
  over `ceil(width/8)` bytes and silently ships zeros unless that divides by three; widths
  off a byte boundary additionally shift every row. Both print blank or garbled *and the
  printer reports success*. `Profile.__post_init__` refuses the geometry instead.
- **Layouts are authored in reading orientation, then rotated.** `Layout.rotate_cw` says how
  far. The D110 layouts read along the feed (rotate 90); the B1 card reads across the head
  (rotate 0) because 50x30 stock is 50 mm across the roll. `to_printer_orientation` is the
  only place that rotation happens.
- **QR modules have a floor in printer dots** (2 on the D110, 3 on the B1's roomier card).
  Below it the thermal print stops scanning. Long names are truncated *inside the QR payload
  only* — the printed text keeps the full name.
- **The v3+ print path is forced per family, not per printer.** `min_protocol` exists because
  the B1 ACKs the legacy sequence and then prints blank.
- **Nothing may raise inside a pubsub subscriber.** A raising subscriber kills the meshtastic
  reader thread and the kiosk goes deaf with no error. Every `on_*` wraps its body.

## Working without hardware

Most of this repo can be developed and reviewed with nothing plugged in:

```bash
labeltastic --sample --dry-run [--printer b1] [--die-cut]
```

writes the PNG and then runs the **real** bitmap encoder over the printer-oriented image,
reporting packet count, max data length, inked rows, and how many inked rows lost their
per-third counts. That last number must be zero. `pytest` covers the same ground plus the
contact-URL round trip and the USB-id disambiguation.

What a dry run **cannot** tell you: whether the label is readable, whether the density is
right, whether the roll advances correctly, or whether a real printer accepts the sequence.
Say so explicitly when a change is untested on hardware.

## Gotchas

- **Auto-detect can grab the wrong port.** A CH9102 radio shares CH340's vendor id, so
  vendor-only matching would hand the radio to the printer driver. Pass `--printer-port`
  when in doubt; prefer the stable names in `/dev/serial/by-id/`.
- **Multi-line trailing comments don't survive `ruff format`.** The continuation lines get
  dedented to column 0 and then read as belonging to the next statement — which, in a file
  full of geometry constants, silently attaches the wrong reasoning to the wrong number.
  Write anything longer than one line as a block comment above the statement.
- **The emoji font ships in the package** (`assets/NotoEmoji-Regular.ttf`, OFL-1.1). The text
  font does not — it comes from the host, so exact pixels differ between a Pi, a CI runner
  and your laptop. Don't write pixel-golden tests.
