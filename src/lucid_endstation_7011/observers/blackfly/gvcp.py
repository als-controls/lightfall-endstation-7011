"""GVCP packet builders and parsers. Pure functions, no I/O.

Commands, ACK payload offsets, and byte layouts follow GigE Vision 1.2
(GVCP 1.2), cross-referenced with aravis' arvgvcpprivate.h.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

GVCP_PORT = 3956

# Commands
CMD_DISCOVERY = 0x0002
CMD_FORCEIP = 0x0004
CMD_READREG = 0x0080
CMD_WRITEREG = 0x0082
CMD_READMEM = 0x0084
CMD_WRITEMEM = 0x0086

# Flags
FLAG_ACK_REQUIRED = 0x01
FLAG_BROADCAST = 0x10


def _header(flags: int, cmd: int, length: int, req_id: int) -> bytes:
    """GVCP common header: key(0x42), flags, cmd(u16 BE), length(u16 BE), req_id(u16 BE)."""
    return struct.pack(">BBHHH", 0x42, flags, cmd, length, req_id)


def build_discovery_cmd(req_id: int) -> bytes:
    return _header(FLAG_ACK_REQUIRED | FLAG_BROADCAST, CMD_DISCOVERY, 0, req_id)


def build_readreg_cmd(addr: int, req_id: int) -> bytes:
    return _header(FLAG_ACK_REQUIRED, CMD_READREG, 4, req_id) + struct.pack(">I", addr)


def build_writereg_cmd(addr: int, value: int, req_id: int) -> bytes:
    if not -0x80000000 <= value <= 0xFFFFFFFF:
        raise ValueError(f"WRITEREG value out of i32/u32 range: {value}")
    return (
        _header(FLAG_ACK_REQUIRED, CMD_WRITEREG, 8, req_id)
        + struct.pack(">II", addr, value & 0xFFFFFFFF)
    )


def build_readmem_cmd(addr: int, count: int, req_id: int) -> bytes:
    if count % 4 != 0:
        raise ValueError(f"READMEM count must be multiple of 4, got {count}")
    return (
        _header(FLAG_ACK_REQUIRED, CMD_READMEM, 8, req_id)
        + struct.pack(">IHH", addr, 0, count)
    )


@dataclass(frozen=True)
class AckHeader:
    status: int
    ack_cmd: int
    length: int
    ack_id: int


@dataclass(frozen=True)
class DeviceInfo:
    ip: str
    manufacturer: str
    model: str
    version: str
    serial: str
    user_name: str


def parse_ack_header(raw: bytes) -> tuple[AckHeader, bytes]:
    """Parse the 8-byte GVCP ACK header; return (header, payload_bytes)."""
    if len(raw) < 8:
        raise ValueError(f"GVCP ACK too short: {len(raw)}B")
    status, ack_cmd, length, ack_id = struct.unpack(">HHHH", raw[:8])
    return AckHeader(status, ack_cmd, length, ack_id), raw[8:]


def _decode_ascii(b: bytes) -> str:
    """Decode a null-terminated C-string. GigE Vision bootstrap fields are ASCII
    or UTF-8 per the device's CHARACTER_SET register; UTF-8 is a strict superset
    of ASCII so decoding as UTF-8 works in both cases."""
    return b.split(b"\x00", 1)[0].decode("utf-8", "replace")


def parse_discovery_ack(payload: bytes) -> DeviceInfo:
    """Parse a GigE Vision DISCOVERY_ACK payload (>=0xF8 bytes) into DeviceInfo."""
    # GigE Vision 1.2 DISCOVERY_ACK payload layout (see aravis arvgvcpprivate.h):
    #   0x24..0x28  current IPv4
    #   0x48..0x68  MANUFACTURER_NAME (32B)
    #   0x68..0x88  MODEL_NAME         (32B)
    #   0x88..0xA8  DEVICE_VERSION     (32B)
    #   0xA8..0xD8  MANUFACTURER_INFO  (48B, not exposed)
    #   0xD8..0xE8  SERIAL_NUMBER      (16B)
    #   0xE8..0xF8  USER_DEFINED_NAME  (16B)
    if len(payload) < 0xF8:
        raise ValueError(f"discovery ack payload too short: {len(payload)}B (need >=0xF8)")
    ip_bytes = payload[0x24:0x28]
    return DeviceInfo(
        ip=".".join(str(b) for b in ip_bytes),
        manufacturer=_decode_ascii(payload[0x48:0x68]),
        model=_decode_ascii(payload[0x68:0x88]),
        version=_decode_ascii(payload[0x88:0xA8]),
        serial=_decode_ascii(payload[0xD8:0xE8]),
        user_name=_decode_ascii(payload[0xE8:0xF8]),
    )


def parse_readreg_ack(payload: bytes) -> int:
    """Parse a READREG_ACK payload (4B big-endian u32)."""
    if len(payload) < 4:
        raise ValueError(f"readreg ack payload too short: {len(payload)}B")
    return struct.unpack(">I", payload[:4])[0]


def parse_readmem_ack(payload: bytes) -> tuple[int, bytes]:
    """Parse a READMEM_ACK payload: returns (echoed_address, data)."""
    if len(payload) < 4:
        raise ValueError(f"readmem ack payload too short: {len(payload)}B (need >=4)")
    addr = struct.unpack(">I", payload[:4])[0]
    return addr, payload[4:]
