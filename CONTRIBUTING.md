# Contributing

Thanks for helping improve labeltastic. See [AGENTS.md](AGENTS.md) for how the code is
laid out and why the layout code is shaped the way it is.

## Setup

```bash
uv sync --extra dev        # or: python -m venv .venv && .venv/bin/pip install -e '.[dev]'
```

You do not need a printer or a radio to work on this:

```bash
labeltastic --sample --dry-run                      # renders a canned node to a PNG
labeltastic --sample --dry-run --printer b1 --die-cut
```

`--sample --dry-run` writes the PNG *and* runs the real bitmap encoder over it, reporting
packet count and per-third pixel counts. Without hardware in front of you that is the only
check on the wire format there is, so read those numbers rather than just looking at the PNG.

## Gates (run before every PR)

```bash
ruff check .                  # lint
mypy                          # types
python scripts/check_spdx.py  # SPDX headers on every first-party source file
pytest                         # the whole suite — no printer, no radio
```

CI runs the same four, plus a render job that renders every layout and uploads the PNGs as
artifacts.

## Conventions

- **Formatting:** `ruff check` only — deliberately **not** `ruff format`. The layout and
  logo code is column-aligned geometry with the numbers lined up to be read as a table;
  the formatter collapses that alignment and the code gets harder to check against the
  printer's dot maths. Match the surrounding style by hand.
- **Vendored code:** everything under `src/labeltastic/_vendor/` keeps its upstream license
  and is excluded from lint, types, and the SPDX gate. Don't restyle it; keep local changes
  minimal and documented in [`_vendor/README.md`](src/labeltastic/_vendor/README.md).
- **Hardware claims:** if you can't test a change on the printer, say so in the PR. A dry
  run proves the layout and the packet framing, not that the label comes out readable.
- **Commits:** Conventional Commits, signed off with DCO (`git commit -s`).
- **License:** GPL-3.0-only. Every first-party source file carries the SPDX header (CI
  enforces it): `SPDX-FileCopyrightText: Meshtastic contributors` +
  `SPDX-License-Identifier: GPL-3.0-only`.

## Adding a printer

A new model is a `Profile` in `profiles.py` plus, if its stock needs a different shape of
badge, a renderer in `render.py`. `head_px` must be a multiple of 24 — the encoder silently
drops its per-third pixel counts otherwise and the printer cheerfully prints blank. The
`Profile.__post_init__` check exists so you find that out at import rather than on the roll.
