# Security Policy

## Reporting a vulnerability

Please report security issues privately via GitHub Security Advisories
(<https://github.com/meshtastic/labeltastic/security/advisories/new>) rather than a public
issue. We aim to acknowledge within a few days.

## Threat model

labeltastic is a kiosk: it takes input from **anyone within radio range**, renders it, and
burns it onto a label. Treat every field of an incoming nodeinfo as hostile text.

- **The trigger is unauthenticated by design.** Any node that can DM the kiosk can spend a
  label. Mitigations in place: DM-only (broadcasts are ignored — a broadcast trigger on a
  busy mesh would empty the roll in minutes), and a per-node cooldown (`--cooldown`,
  default 600 s). Neither survives an attacker willing to rotate node IDs; the roll is
  consumable and the real backstop is a human at the table.
- **Names are attacker-controlled strings.** They reach PIL as text only — `clean()` drops
  non-printable and unrenderable codepoints, and `fit()` bounds the drawn width. They are
  never passed to a shell, a filesystem path, or an eval. A name that renders as a
  confusing or offensive badge is a moderation problem, not a memory-safety one.
- **The QR encodes what the sender sent.** It is that node's own public key and names,
  re-emitted. labeltastic does not sign, vouch for, or verify the identity behind it —
  scanning a printed badge adds the contact exactly as sharing it over the mesh would.
- **Badges are PII on paper.** A printed nametag carries a long name and a node ID, and a
  discarded one is a durable record of who was in the room. Consider that when running a
  kiosk at an event.
- **Serial ports are opened by path.** `--serial` / `--printer-port` are handed to
  pyserial and the vendored niimprint verbatim. Running the kiosk as a user with access to
  a device node is enough; it needs no elevated privileges.

## Out of scope

Physical access to the printer or the Pi, and mesh-layer attacks against the radio itself
(see [meshtastic/firmware](https://github.com/meshtastic/firmware) for those).
