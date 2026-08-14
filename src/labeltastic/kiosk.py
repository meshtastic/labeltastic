# SPDX-FileCopyrightText: Meshtastic contributors
# SPDX-License-Identifier: GPL-3.0-only
"""The kiosk itself: watch the mesh for DMs, queue badges, keep an eye on both
the printer and the radio link."""

import os
import queue
import re
import threading
import time

from meshtastic.protobuf import mesh_pb2, portnums_pb2
from pubsub import pub

from .printing import print_label, probe_printer

TRIGGER = re.compile(r"\b(print|badge|tag)\b", re.I)


class Kiosk:
    def __init__(self, interface, args, profile):
        self.interface = interface
        self.args = args
        self.profile = profile
        self.jobs = queue.Queue()
        self.last_print = {}  # nodenum -> monotonic time
        self.my_num = interface.myInfo.my_node_num
        self.printer_lock = threading.Lock()
        threading.Thread(target=self.worker, daemon=True).start()
        if not args.dry_run:
            threading.Thread(target=self.keepalive, daemon=True).start()
        self.last_rx = time.monotonic()
        self.pending = {}  # nodenum -> monotonic time; waiting for their nodeinfo
        pub.subscribe(self.on_text, "meshtastic.receive.text")
        pub.subscribe(self.on_user, "meshtastic.receive.user")
        pub.subscribe(self.on_any, "meshtastic.receive")
        pub.subscribe(self.on_conn_lost, "meshtastic.connection.lost")
        print(f"kiosk node num {self.my_num:#x}")

    def on_conn_lost(self, interface=None):
        # the meshtastic lib won't reconnect on its own — die loudly so a
        # wrapper loop can restart us
        print("RADIO CONNECTION LOST — exiting so a restart loop can revive us")
        os._exit(70)

    def on_any(self, packet, interface):
        try:
            self.last_rx = time.monotonic()
            if not self.args.debug:
                return  # per-packet firehose — a con mesh never shuts up
            d = packet.get("decoded", {})
            frm, to = packet.get("from"), packet.get("to")
            if isinstance(frm, int) and isinstance(to, int):
                print(f"rx {d.get('portnum', '?'):24} from={frm:#010x} to={to:#010x}")
            else:
                print(f"rx {d.get('portnum', '?')} {packet.get('fromId')} -> {packet.get('toId')}")
        except Exception as e:
            # a raising subscriber kills the meshtastic reader thread — never do
            print(f"on_any error (ignored): {e}")

    def keepalive(self):
        # These printers auto-power-off when idle. A heartbeat every 45 s resets that
        # timer, and "printer went missing" shows up at the console before the
        # next guest finds out the hard way. Also watches the mesh rx stream:
        # a busy mesh gone silent means the serial reader wedged.
        alive = True
        while True:
            with self.printer_lock:
                err = probe_printer(self.args)
            if err and alive:
                print(f"printer went missing: {err}")
            elif not err and not alive:
                print("printer is back")
            alive = not err
            quiet = time.monotonic() - self.last_rx
            if quiet > 900:
                print(
                    f"no mesh packets for {int(quiet)}s — assuming wedged radio link, exiting for restart"
                )
                os._exit(71)
            if quiet > 300:
                print(f"WARNING: no mesh packets for {int(quiet)}s")
            time.sleep(45)

    def reply(self, dest, msg):
        if dest is None:
            return
        try:
            self.interface.sendText(msg, destinationId=dest)
        except Exception as e:
            print(f"reply to {dest} failed: {e}")

    def on_text(self, packet, interface):
        try:
            self._on_text(packet)
        except Exception as e:
            # a raising subscriber kills the meshtastic reader thread — never do
            print(f"on_text error (ignored): {e}")

    def _on_text(self, packet):
        if packet.get("to") != self.my_num:
            return  # DMs only; broadcast triggers would melt the label roll at DEFCON
        sender = packet.get("from")
        text = packet.get("decoded", {}).get("text", "")
        if not sender or not TRIGGER.search(text):
            self.reply(sender, 'DM me "print" and I\'ll print your contact-QR nametag.')
            return
        wait = self.args.cooldown - (time.monotonic() - self.last_print.get(sender, -1e9))
        if wait > 0:
            self.reply(
                sender,
                f"Easy there — one badge per {self.args.cooldown // 60} min. {int(wait)}s left.",
            )
            return
        node = self.interface.nodesByNum.get(sender)
        if not node or not node.get("user", {}).get("id"):
            # Common on a huge mesh: small nodedbs evict constantly. Ask them
            # for their nodeinfo; on_user prints the badge when it arrives.
            self.pending[sender] = time.monotonic()
            self.request_nodeinfo(sender)
            self.reply(
                sender,
                "Don't know you yet — asking your node for its info. Badge prints when it answers.",
            )
            return
        self.last_print[sender] = time.monotonic()
        self.jobs.put((sender, node))
        self.reply(sender, "Printing your nametag... come grab it!")

    def request_nodeinfo(self, dest):
        """Send our User with wantResponse — same exchange the phone apps use."""
        try:
            me = self.interface.nodesByNum.get(self.my_num, {}).get("user", {})
            u = mesh_pb2.User()
            u.id = me.get("id", "")
            u.long_name = me.get("longName", "")
            u.short_name = me.get("shortName", "")
            self.interface.sendData(
                u, destinationId=dest, portNum=portnums_pb2.PortNum.NODEINFO_APP, wantResponse=True
            )
        except Exception as e:
            print(f"nodeinfo request to {dest:#x} failed: {e}")

    def on_user(self, packet, interface):
        try:
            sender = packet.get("from")
            asked = self.pending.pop(sender, None)
            if asked is None:
                return
            if time.monotonic() - asked > 300:
                return  # they asked ages ago; don't surprise-print
            # build from the packet itself — nodedb update order isn't guaranteed
            user = packet.get("decoded", {}).get("user", {})
            node = self.interface.nodesByNum.get(sender)
            if not node or not node.get("user", {}).get("id"):
                node = {"num": sender, "user": user}
            if not node.get("user", {}).get("id"):
                return
            self.last_print[sender] = time.monotonic()
            self.jobs.put((sender, node))
            self.reply(sender, "Got your info — printing your nametag!")
        except Exception as e:
            # a raising subscriber kills the meshtastic reader thread — never do
            print(f"on_user error (ignored): {e}")

    def worker(self):
        while True:
            sender, node = self.jobs.get()
            name = node.get("user", {}).get("longName", hex(sender))
            print(f"printing badge for {name} ({sender:#x})")
            with self.printer_lock:
                ok = print_label(node, self.args, self.profile)
            if ok:
                print("  done")
            else:
                self.reply(sender, "Printer jammed/unhappy. Poke the humans at the table.")
