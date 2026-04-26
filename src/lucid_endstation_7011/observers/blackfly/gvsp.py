"""GVSP (GigE Vision Streaming Protocol) packet parsing. Pure functions.

GVSP common 8-byte header (big-endian):
    status (u16) | block_id (u16) | packet_format (u8) | packet_id (u24)

Packet formats: Leader=0x01, Trailer=0x02, Payload=0x03.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Optional

FMT_LEADER = 0x01
FMT_TRAILER = 0x02
FMT_PAYLOAD = 0x03

PAYLOAD_TYPE_IMAGE = 0x0001  # low 14 bits = type; bit 0x4000 = chunks present (not handled)


@dataclass(frozen=True)
class Leader:
    payload_type: int
    timestamp: int
    pixel_format: int
    width: int
    height: int
    offset_x: int
    offset_y: int
    padding_x: int
    padding_y: int


@dataclass(frozen=True)
class Trailer:
    payload_type: int
    data0: int  # unused in GEV 1.2, may be size_y in GEV 2.0+ variants


@dataclass(frozen=True)
class Packet:
    """Parsed GVSP packet. Exactly one of `leader`, `trailer`, or `data` is populated
    depending on `kind`.
    """
    kind: int
    block_id: int
    packet_id: int
    data: bytes = b""
    leader: Optional[Leader] = None
    trailer: Optional[Trailer] = None


def parse_packet(raw: bytes) -> Packet:
    """Parse one GVSP datagram. Raises ValueError on malformed input."""
    if len(raw) < 8:
        raise ValueError(f"GVSP packet too short: {len(raw)}B")
    status, block_id, fmt_id = struct.unpack(">HHI", raw[:8])
    if fmt_id & 0x80000000:
        raise ValueError("GVSP extended-ID packet header not supported")
    fmt = (fmt_id >> 24) & 0x7F
    pkt_id = fmt_id & 0xFFFFFF
    body = raw[8:]

    if fmt == FMT_LEADER:
        if len(body) < 36:
            raise ValueError(f"leader body too short: {len(body)}B (need 36)")
        (reserved, payload_type, timestamp, pixel_format,
         size_x, size_y, offset_x, offset_y, padding_x, padding_y) = struct.unpack(
            ">HHQIIIIIHH", body[:36]
        )
        return Packet(
            kind=fmt, block_id=block_id, packet_id=pkt_id,
            leader=Leader(payload_type, timestamp, pixel_format,
                          size_x, size_y, offset_x, offset_y, padding_x, padding_y),
        )

    if fmt == FMT_TRAILER:
        if len(body) < 8:
            raise ValueError(f"trailer body too short: {len(body)}B (need 8)")
        payload_type, data0 = struct.unpack(">II", body[:8])
        return Packet(
            kind=fmt, block_id=block_id, packet_id=pkt_id,
            trailer=Trailer(payload_type, data0),
        )

    if fmt == FMT_PAYLOAD:
        return Packet(kind=fmt, block_id=block_id, packet_id=pkt_id, data=body)

    raise ValueError(f"unknown GVSP packet format 0x{fmt:02x}")


@dataclass
class Frame:
    """A fully-assembled image block: metadata (leader) + concatenated pixel bytes."""
    block_id: int
    leader: Leader
    data: bytes


class FrameAssembler:
    """Reassembles GVSP packets of a single in-flight block into a Frame.

    Not thread-safe: call feed() from a single thread. Hand off returned
    Frame objects to other threads via a queue.

    Usage:
        asm = FrameAssembler()
        for pkt in stream:
            frame = asm.feed(pkt)
            if frame is not None:
                handle(frame)
    """

    def __init__(self) -> None:
        self._block_id: Optional[int] = None
        self._leader: Optional[Leader] = None
        self._payloads: dict[int, bytes] = {}

    def _reset(self, block_id: Optional[int] = None, leader: Optional[Leader] = None) -> None:
        self._block_id = block_id
        self._leader = leader
        self._payloads = {}

    def feed(self, pkt: Packet) -> Optional[Frame]:
        if pkt.kind == FMT_LEADER:
            self._reset(pkt.block_id, pkt.leader)
            return None
        # A packet from an unknown block or a stale block is discarded.
        if self._block_id is None or pkt.block_id != self._block_id:
            return None
        if pkt.kind == FMT_PAYLOAD:
            self._payloads[pkt.packet_id] = pkt.data
            return None
        if pkt.kind == FMT_TRAILER:
            # GVSP numbers packets within a block: leader=0, payloads=1..n-1, trailer=n.
            expected_ids = set(range(1, pkt.packet_id))
            if expected_ids != set(self._payloads.keys()):
                self._reset()
                return None
            data = b"".join(self._payloads[i] for i in sorted(self._payloads))
            assert self._leader is not None  # guaranteed by the leader-path above
            frame = Frame(self._block_id, self._leader, data)
            self._reset()
            return frame
        return None
