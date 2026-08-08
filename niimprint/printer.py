import abc
import enum
import logging
import math
import socket
import struct
import time

import serial
from PIL import Image, ImageOps
from serial.tools.list_ports import comports as list_comports

from niimprint.packet import NiimbotPacket


class InfoEnum(enum.IntEnum):
    DENSITY = 1
    PRINTSPEED = 2
    LABELTYPE = 3
    LANGUAGETYPE = 6
    AUTOSHUTDOWNTIME = 7
    DEVICETYPE = 8
    SOFTVERSION = 9
    BATTERY = 10
    DEVICESERIAL = 11
    HARDVERSION = 12


class RequestCodeEnum(enum.IntEnum):
    GET_INFO = 64  # 0x40
    GET_RFID = 26  # 0x1A
    HEARTBEAT = 220  # 0xDC
    CONNECT = 193  # 0xC1 (local addition: session handshake)
    PRINTER_STATUS_DATA = 165  # 0xA5 (local addition: protocol version)
    SET_LABEL_TYPE = 35  # 0x23
    SET_LABEL_DENSITY = 33  # 0x21
    START_PRINT = 1  # 0x01
    END_PRINT = 243  # 0xF3
    START_PAGE_PRINT = 3  # 0x03
    END_PAGE_PRINT = 227  # 0xE3
    ALLOW_PRINT_CLEAR = 32  # 0x20
    SET_DIMENSION = 19  # 0x13
    SET_QUANTITY = 21  # 0x15
    GET_PRINT_STATUS = 163  # 0xA3


def _packet_to_int(x):
    return int.from_bytes(x.data, "big")


# Local addition: packet type 219 error codes, per MultiMote/niimbluelib.
PRINT_ERRORS = {
    0x01: "cover open",
    0x02: "no paper detected (gap sensor found nothing — continuous roll in gap mode?)",
    0x03: "low battery",
    0x04: "battery exception",
    0x05: "user cancel",
    0x06: "data error",
    0x07: "print head overheated",
    0x08: "paper out exception (no paper past the sensor, or lid not latched)",
    0x09: "printer busy",
    0x0A: "no print head",
    0x0B: "temperature too low",
    0x0C: "print head loose",
    0x10: "wrong paper (RFID mismatch — printer rejects this roll)",
    0x11: "set paper type failed",
    0x12: "set print mode failed",
    0x13: "set density failed",
    0x14: "write RFID failed",
    0x16: "communication exception",
}


class BaseTransport(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def read(self, length: int) -> bytes:
        raise NotImplementedError

    @abc.abstractmethod
    def write(self, data: bytes):
        raise NotImplementedError


class BluetoothTransport(BaseTransport):
    def __init__(self, address: str):
        self._sock = socket.socket(
            socket.AF_BLUETOOTH,
            socket.SOCK_STREAM,
            socket.BTPROTO_RFCOMM,
        )
        self._sock.connect((address, 1))

    def read(self, length: int) -> bytes:
        return self._sock.recv(length)

    def write(self, data: bytes):
        return self._sock.send(data)


class SerialTransport(BaseTransport):
    def __init__(self, port: str = "auto"):
        port = port if port != "auto" else self._detect_port()
        self._serial = serial.Serial(port=port, baudrate=115200, timeout=0.5)

    def _detect_port(self):
        all_ports = list(list_comports())
        if len(all_ports) == 0:
            raise RuntimeError("No serial ports detected")
        if len(all_ports) > 1:
            msg = "Too many serial ports, please select specific one:"
            for port, desc, hwid in all_ports:
                msg += f"\n- {port} : {desc} [{hwid}]"
            raise RuntimeError(msg)
        return all_ports[0][0]

    def read(self, length: int) -> bytes:
        return self._serial.read(length)

    def write(self, data: bytes):
        return self._serial.write(data)


class PrinterClient:
    def __init__(self, transport):
        self._transport = transport
        self._packetbuf = bytearray()

    def print_image(self, image: Image, density: int = 3):
        self.set_label_density(density)
        self.set_label_type(1)
        self.start_print()
        # self.allow_print_clear()  # Something unsupported in protocol decoding (B21)
        self.start_page_print()
        self.set_dimension(image.height, image.width)
        # self.set_quantity(1)  # Same thing (B21)
        for pkt in self._encode_image(image):
            self._send(pkt)
        self.end_page_print()
        time.sleep(0.3)  # FIXME: Check get_print_status()
        for _ in range(600):  # local change: cap at ~60 s instead of hanging forever
            if self.end_print():
                return
            time.sleep(0.1)
        raise RuntimeError("printer never confirmed end of print")

    def connect(self):
        """Local addition: session handshake newer firmwares expect."""
        return self._require(
            self._transceive(RequestCodeEnum.CONNECT, b"\x01"), "CONNECT")

    def get_protocol_version(self):
        """Local addition: 0 for legacy printers, 3/4/5 for newer families
        (decoding per MultiMote/niimbluelib getPrinterStatusData)."""
        packet = self._transceive(RequestCodeEnum.PRINTER_STATUS_DATA, b"\x01", 16)
        if packet and len(packet.data) > 12:
            n = packet.data[11] * 100 + packet.data[12]
            if 204 <= n < 300:
                return 3
            if 300 <= n < 302:
                return 4
            if n >= 302:
                return 5
        return 0

    def print_image_new(self, image: Image, density: int = 3, label_type: int = 1,
                        quantity: int = 1, v4: bool = False):
        """Local addition: print sequence for newer firmwares (protocol v3+,
        e.g. D110_M devicetype 2320), per MultiMote/niimbluelib. These accept
        the legacy packets with ACKs but print blank labels. v3 ('B1 task'):
        7-byte START_PRINT, 6-byte SET_DIMENSION, real black-pixel counts,
        status-poll completion. v4 additionally: 9-byte START_PRINT, 13-byte
        SET_DIMENSION, no START_PAGE_PRINT, fire-and-forget status query."""
        self.connect()
        self.set_label_density(density)
        self.set_label_type(label_type)
        start = (struct.pack(">H7B", quantity, 0, 0, 0, 0, 0, 0, 0) if v4
                 else struct.pack(">H5B", quantity, 0, 0, 0, 0, 0))
        self._require(
            self._transceive(RequestCodeEnum.START_PRINT, start), "START_PRINT")
        if v4:
            # sacrificial fire-and-forget status query (niimblue does this);
            # its late response is ignored by _transceive's type filtering
            self._send(NiimbotPacket(RequestCodeEnum.GET_PRINT_STATUS, b"\x01"))
            dim = struct.pack(">HHHHBBBH",
                              image.height, image.width, quantity, 0, 0, 0, 0, 0)
        else:
            self.start_page_print()
            dim = struct.pack(">HHH", image.height, image.width, quantity)
        self._require(
            self._transceive(RequestCodeEnum.SET_DIMENSION, dim), "SET_DIMENSION")
        for pkt in self._encode_image_counted(image):
            self._send(pkt)
            time.sleep(0.01)  # niimblue paces packets; native-USB units drop floods
        self.end_page_print()
        for _ in range(200):  # ~60 s: poll until the page reports done
            time.sleep(0.3)
            try:
                if self.get_print_status()["page"] >= quantity:
                    break
            except RuntimeError:
                pass  # a busy printer may skip a poll
        else:
            raise RuntimeError("printer never reported print completion")
        try:
            self.end_print()
            if v4:  # sacrificial heartbeat: some models drop the next packet
                self._send(NiimbotPacket(RequestCodeEnum.HEARTBEAT, b"\x01"))
        except RuntimeError:
            pass

    def _encode_image_counted(self, image: Image):
        img = ImageOps.invert(image.convert("L")).convert("1")
        width_bytes = math.ceil(img.width / 8)
        chunk = width_bytes // 3  # pixel counts are split over thirds of the head
        rows = []
        for y in range(img.height):
            line = [img.getpixel((x, y)) for x in range(img.width)]
            bits = "".join("0" if p == 0 else "1" for p in line)
            rows.append(int(bits, 2).to_bytes(width_bytes, "big"))
        y = 0
        while y < img.height:
            data = rows[y]
            repeat = 1  # identical consecutive rows compress into one packet
            while y + repeat < img.height and repeat < 255 and rows[y + repeat] == data:
                repeat += 1
            if not any(data):
                yield NiimbotPacket(0x84, struct.pack(">HB", y, repeat))
            else:
                parts = [0, 0, 0]
                if chunk and width_bytes <= chunk * 3:
                    for i, b in enumerate(data):
                        parts[min(i // chunk, 2)] += b.bit_count()
                yield NiimbotPacket(0x85, struct.pack(">H3BB", y, *parts, repeat) + data)
            y += repeat

    def _encode_image(self, image: Image):
        img = ImageOps.invert(image.convert("L")).convert("1")
        for y in range(img.height):
            line_data = [img.getpixel((x, y)) for x in range(img.width)]
            line_data = "".join("0" if pix == 0 else "1" for pix in line_data)
            line_data = int(line_data, 2).to_bytes(math.ceil(img.width / 8), "big")
            counts = (0, 0, 0)  # It seems like you can always send zeros
            header = struct.pack(">H3BB", y, *counts, 1)
            pkt = NiimbotPacket(0x85, header + line_data)
            yield pkt

    def _recv(self):
        packets = []
        self._packetbuf.extend(self._transport.read(1024))
        while len(self._packetbuf) > 4:
            pkt_len = self._packetbuf[3] + 7
            if len(self._packetbuf) >= pkt_len:
                packet = NiimbotPacket.from_bytes(self._packetbuf[:pkt_len])
                self._log_buffer("recv", packet.to_bytes())
                packets.append(packet)
                del self._packetbuf[:pkt_len]
        return packets

    def _send(self, packet):
        self._transport.write(packet.to_bytes())

    def _log_buffer(self, prefix: str, buff: bytes):
        msg = ":".join(f"{i:#04x}"[-2:] for i in buff)
        # local change: named logger so --debug can single it out
        logging.getLogger("niimprint").debug(f"{prefix}: {msg}")

    def _transceive(self, reqcode, data, respoffset=1):
        respcode = respoffset + reqcode
        packet = NiimbotPacket(reqcode, data)
        self._log_buffer("send", packet.to_bytes())
        self._send(packet)
        resp = None
        for _ in range(6):
            for packet in self._recv():
                if packet.type == 219:
                    # Local change: was a bare `raise ValueError` — say why.
                    code = packet.data[0] if packet.data else -1
                    raise RuntimeError(
                        f"printer error {code:#04x}: "
                        f"{PRINT_ERRORS.get(code, 'unknown error')} "
                        f"(while sending {RequestCodeEnum(reqcode).name})")
                elif packet.type == 0:
                    raise RuntimeError(
                        f"printer says {RequestCodeEnum(reqcode).name} "
                        "is not supported")
                elif packet.type == respcode:
                    resp = packet
            if resp:
                return resp
            time.sleep(0.1)
        return resp

    def _require(self, packet, what):
        # Local change: upstream dies with "'NoneType' object has no attribute
        # 'data'" when the printer stays silent. Say what actually happened.
        if packet is None:
            raise RuntimeError(
                f"no response from printer to {what} — check it's powered ON "
                "(USB only charges it; press the power button) and that this "
                "is really the printer's port"
            )
        return packet

    def get_info(self, key):
        if packet := self._transceive(RequestCodeEnum.GET_INFO, bytes((key,)), key):
            match key:
                case InfoEnum.DEVICESERIAL:
                    return packet.data.hex()
                case InfoEnum.SOFTVERSION:
                    return _packet_to_int(packet) / 100
                case InfoEnum.HARDVERSION:
                    return _packet_to_int(packet) / 100
                case _:
                    return _packet_to_int(packet)
        else:
            return None

    def get_rfid(self):
        packet = self._transceive(RequestCodeEnum.GET_RFID, b"\x01")
        data = packet.data

        if data[0] == 0:
            return None
        uuid = data[0:8].hex()
        idx = 8

        barcode_len = data[idx]
        idx += 1
        barcode = data[idx : idx + barcode_len].decode()

        idx += barcode_len
        serial_len = data[idx]
        idx += 1
        serial = data[idx : idx + serial_len].decode()

        idx += serial_len
        total_len, used_len, type_ = struct.unpack(">HHB", data[idx:])
        return {
            "uuid": uuid,
            "barcode": barcode,
            "serial": serial,
            "used_len": used_len,
            "total_len": total_len,
            "type": type_,
        }

    def heartbeat(self):
        packet = self._require(
            self._transceive(RequestCodeEnum.HEARTBEAT, b"\x01"), "HEARTBEAT")
        closingstate = None
        powerlevel = None
        paperstate = None
        rfidreadstate = None

        match len(packet.data):
            case 20:
                paperstate = packet.data[18]
                rfidreadstate = packet.data[19]
            case 13:
                closingstate = packet.data[9]
                powerlevel = packet.data[10]
                paperstate = packet.data[11]
                rfidreadstate = packet.data[12]
            case 19:
                closingstate = packet.data[15]
                powerlevel = packet.data[16]
                paperstate = packet.data[17]
                rfidreadstate = packet.data[18]
            case 10:
                closingstate = packet.data[8]
                powerlevel = packet.data[9]
                rfidreadstate = packet.data[8]
            case 9:
                closingstate = packet.data[8]

        return {
            "closingstate": closingstate,
            "powerlevel": powerlevel,
            "paperstate": paperstate,
            "rfidreadstate": rfidreadstate,
        }

    def set_label_type(self, n):
        assert 1 <= n <= 3
        packet = self._require(
            self._transceive(RequestCodeEnum.SET_LABEL_TYPE, bytes((n,)), 16),
            "SET_LABEL_TYPE")
        return bool(packet.data[0])

    def set_label_density(self, n):
        assert 1 <= n <= 5  # B21 has 5 levels, not sure for D11
        packet = self._require(
            self._transceive(RequestCodeEnum.SET_LABEL_DENSITY, bytes((n,)), 16),
            "SET_LABEL_DENSITY")
        return bool(packet.data[0])

    def start_print(self):
        packet = self._require(
            self._transceive(RequestCodeEnum.START_PRINT, b"\x01"), "START_PRINT")
        return bool(packet.data[0])

    def end_print(self):
        packet = self._transceive(RequestCodeEnum.END_PRINT, b"\x01")
        if packet is None:  # local change: still feeding — caller polls again
            return False
        return bool(packet.data[0])

    def start_page_print(self):
        packet = self._require(
            self._transceive(RequestCodeEnum.START_PAGE_PRINT, b"\x01"),
            "START_PAGE_PRINT")
        return bool(packet.data[0])

    def end_page_print(self):
        packet = self._require(
            self._transceive(RequestCodeEnum.END_PAGE_PRINT, b"\x01"),
            "END_PAGE_PRINT")
        return bool(packet.data[0])

    def allow_print_clear(self):
        packet = self._require(
            self._transceive(RequestCodeEnum.ALLOW_PRINT_CLEAR, b"\x01", 16),
            "ALLOW_PRINT_CLEAR")
        return bool(packet.data[0])

    def set_dimension(self, w, h):
        packet = self._require(
            self._transceive(RequestCodeEnum.SET_DIMENSION, struct.pack(">HH", w, h)),
            "SET_DIMENSION")
        return bool(packet.data[0])

    def set_quantity(self, n):
        packet = self._require(
            self._transceive(RequestCodeEnum.SET_QUANTITY, struct.pack(">H", n)),
            "SET_QUANTITY")
        return bool(packet.data[0])

    def get_print_status(self):
        packet = self._require(
            self._transceive(RequestCodeEnum.GET_PRINT_STATUS, b"\x01", 16),
            "GET_PRINT_STATUS")
        # local change: newer firmwares append extra bytes — read the first 4
        page, progress1, progress2 = struct.unpack(">HBB", packet.data[:4])
        return {"page": page, "progress1": progress1, "progress2": progress2}
