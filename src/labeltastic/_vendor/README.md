# Vendored third-party code

Everything under this directory keeps its own upstream license. It is **not**
covered by labeltastic's GPL-3.0-only, and it deliberately carries no Meshtastic
SPDX header — `scripts/check_spdx.py` skips the whole tree.

## niimprint

- Upstream: <https://github.com/AndBondStyle/niimprint>
- License: MIT — see [`niimprint/LICENSE`](niimprint/LICENSE), © 2023 kjy00302
  (AndBondStyle's repo is the maintained fork of kjy00302's original, which is
  why the copyright line names the latter).

Vendored rather than depended on because upstream's packaging pins
`python = ">=3.11,<3.12"`, and Raspberry Pi OS is well past that. The code
itself runs fine on 3.11 through 3.14.

Local modifications, kept to the minimum:

- `printer.py`: `from niimprint.packet import ...` → `from .packet import ...`,
  so the package works under `labeltastic._vendor` instead of claiming the
  top-level `niimprint` name.
- `__main__.py` deleted — it is upstream's standalone `click` CLI, which
  labeltastic does not use and which would add a dependency for nothing.
