from __future__ import annotations

import struct
import pytest

from lightfall_endstation_7011.observers.blackfly import gvsp


def _gvsp_header(status: int, block_id: int, fmt: int, pkt_id: int) -> bytes:
    """Build an 8-byte GVSP header: status(2) block_id(2) fmt|pkt_id(4 BE)."""
    fmt_id = ((fmt & 0xFF) << 24) | (pkt_id & 0xFFFFFF)
    return struct.pack(">HHI", status, block_id, fmt_id)


def test_parse_leader():
    hdr = _gvsp_header(0, 42, gvsp.FMT_LEADER, 0)
    # Leader payload: reserved(2) payload_type(2) timestamp(8) pixel_format(4)
    #                 size_x(4) size_y(4) offset_x(4) offset_y(4) padding_x(2) padding_y(2)
    leader = struct.pack(
        ">HHQIIIIIHH",
        0, 0x0001, 123456789,
        0x01080001, 4096, 3000, 0, 0, 0, 0,
    )
    pkt = gvsp.parse_packet(hdr + leader)
    assert pkt.kind == gvsp.FMT_LEADER
    assert pkt.block_id == 42
    assert pkt.packet_id == 0
    assert pkt.leader is not None
    assert pkt.leader.payload_type == gvsp.PAYLOAD_TYPE_IMAGE
    assert pkt.leader.timestamp == 123456789
    assert pkt.leader.pixel_format == 0x01080001
    assert pkt.leader.width == 4096
    assert pkt.leader.height == 3000
    assert pkt.leader.offset_x == 0
    assert pkt.leader.offset_y == 0
    assert pkt.leader.padding_x == 0
    assert pkt.leader.padding_y == 0
    assert pkt.data == b""


def test_parse_payload():
    hdr = _gvsp_header(0, 42, gvsp.FMT_PAYLOAD, 7)
    body = b"\xAA" * 1400
    pkt = gvsp.parse_packet(hdr + body)
    assert pkt.kind == gvsp.FMT_PAYLOAD
    assert pkt.block_id == 42
    assert pkt.packet_id == 7
    assert pkt.data == body
    assert pkt.leader is None
    assert pkt.trailer is None


def test_parse_trailer():
    hdr = _gvsp_header(0, 42, gvsp.FMT_TRAILER, 999)
    # Trailer payload: payload_type(u32) | data0(u32)
    trailer = struct.pack(">II", gvsp.PAYLOAD_TYPE_IMAGE, 12000000)
    pkt = gvsp.parse_packet(hdr + trailer)
    assert pkt.kind == gvsp.FMT_TRAILER
    assert pkt.block_id == 42
    assert pkt.packet_id == 999
    assert pkt.trailer is not None
    assert pkt.trailer.payload_type == gvsp.PAYLOAD_TYPE_IMAGE
    assert pkt.trailer.data0 == 12000000


def test_parse_header_too_short():
    with pytest.raises(ValueError, match="too short"):
        gvsp.parse_packet(b"\x00" * 7)


def test_parse_leader_body_too_short():
    hdr = _gvsp_header(0, 42, gvsp.FMT_LEADER, 0)
    # 35-byte leader body (need 36)
    short_leader = b"\x00" * 35
    with pytest.raises(ValueError, match="leader body too short"):
        gvsp.parse_packet(hdr + short_leader)


def test_parse_unknown_format():
    hdr = _gvsp_header(0, 42, 0x2A, 0)  # bogus format (high bit clear)
    with pytest.raises(ValueError, match="unknown GVSP packet format"):
        gvsp.parse_packet(hdr + b"\x00" * 20)


def test_packet_id_is_24_bit():
    """packet_id takes the low 24 bits of the format/id field."""
    hdr = _gvsp_header(0, 1, gvsp.FMT_PAYLOAD, 0xFFFFFF)
    pkt = gvsp.parse_packet(hdr + b"X")
    assert pkt.packet_id == 0xFFFFFF


def test_parse_leader_preserves_large_timestamp_and_nonzero_padding():
    """High-bit timestamp and non-zero padding fields round-trip correctly."""
    hdr = _gvsp_header(0, 1, gvsp.FMT_LEADER, 0)
    big_ts = 0x8000_0000_0000_0001
    leader = struct.pack(
        ">HHQIIIIIHH",
        0, 0x0001, big_ts,
        0x01080001, 1920, 1080, 64, 32, 4, 2,
    )
    pkt = gvsp.parse_packet(hdr + leader)
    assert pkt.leader.timestamp == big_ts
    assert pkt.leader.padding_x == 4
    assert pkt.leader.padding_y == 2


def test_parse_block_id_wrap():
    """block_id is 16 bits — 0xFFFF must round-trip."""
    hdr = _gvsp_header(0, 0xFFFF, gvsp.FMT_PAYLOAD, 1)
    pkt = gvsp.parse_packet(hdr + b"X")
    assert pkt.block_id == 0xFFFF


def test_parse_trailer_body_too_short():
    hdr = _gvsp_header(0, 1, gvsp.FMT_TRAILER, 1)
    with pytest.raises(ValueError, match="trailer body too short"):
        gvsp.parse_packet(hdr + b"\x00" * 7)


def test_parse_rejects_extended_id_mode():
    """Extended-ID packets (bit 0x80000000 of the format word set) must be rejected."""
    # fmt byte 0x81 = 0x80 | FMT_LEADER
    hdr = _gvsp_header(0, 1, 0x81, 0)
    with pytest.raises(ValueError, match="extended-ID"):
        gvsp.parse_packet(hdr + b"\x00" * 36)


def test_parse_trailer_with_large_payload_type():
    """Trailer payload_type is a full u32 — values >= 0x10000 must round-trip."""
    hdr = _gvsp_header(0, 1, gvsp.FMT_TRAILER, 1)
    trailer = struct.pack(">II", 0x0001_4001, 0xDEADBEEF)
    pkt = gvsp.parse_packet(hdr + trailer)
    assert pkt.trailer.payload_type == 0x0001_4001
    assert pkt.trailer.data0 == 0xDEADBEEF


def _leader_pkt(block_id: int, width: int, height: int, pixel_format: int = 0x01080001) -> gvsp.Packet:
    return gvsp.Packet(
        kind=gvsp.FMT_LEADER, block_id=block_id, packet_id=0,
        leader=gvsp.Leader(
            payload_type=gvsp.PAYLOAD_TYPE_IMAGE, timestamp=0,
            pixel_format=pixel_format, width=width, height=height,
            offset_x=0, offset_y=0, padding_x=0, padding_y=0,
        ),
    )


def _trailer_pkt(block_id: int, packet_id: int) -> gvsp.Packet:
    return gvsp.Packet(
        kind=gvsp.FMT_TRAILER, block_id=block_id, packet_id=packet_id,
        trailer=gvsp.Trailer(payload_type=gvsp.PAYLOAD_TYPE_IMAGE, data0=0),
    )


def _payload_pkt(block_id: int, packet_id: int, data: bytes) -> gvsp.Packet:
    return gvsp.Packet(kind=gvsp.FMT_PAYLOAD, block_id=block_id, packet_id=packet_id, data=data)


def test_assembler_complete_frame():
    asm = gvsp.FrameAssembler()
    assert asm.feed(_leader_pkt(1, 4, 2)) is None
    assert asm.feed(_payload_pkt(1, 1, b"\x01\x02\x03\x04")) is None
    assert asm.feed(_payload_pkt(1, 2, b"\x05\x06\x07\x08")) is None
    frame = asm.feed(_trailer_pkt(1, 3))
    assert frame is not None
    assert frame.block_id == 1
    assert frame.leader.width == 4
    assert frame.leader.height == 2
    assert frame.data == b"\x01\x02\x03\x04\x05\x06\x07\x08"


def test_assembler_rejects_incomplete_frame():
    asm = gvsp.FrameAssembler()
    asm.feed(_leader_pkt(1, 4, 2))
    asm.feed(_payload_pkt(1, 1, b"\x01\x02\x03\x04"))
    # skip packet_id=2
    assert asm.feed(_trailer_pkt(1, 3)) is None


def test_assembler_handles_out_of_order_payloads():
    """Packets may arrive in any order; assembler sorts by packet_id before joining."""
    asm = gvsp.FrameAssembler()
    asm.feed(_leader_pkt(2, 6, 1))
    asm.feed(_payload_pkt(2, 2, b"\x03\x04"))
    asm.feed(_payload_pkt(2, 1, b"\x01\x02"))
    asm.feed(_payload_pkt(2, 3, b"\x05\x06"))
    frame = asm.feed(_trailer_pkt(2, 4))
    assert frame is not None
    assert frame.data == b"\x01\x02\x03\x04\x05\x06"


def test_assembler_drops_packets_from_stale_block():
    """A payload with a mismatched block_id is ignored (stale from previous frame)."""
    asm = gvsp.FrameAssembler()
    asm.feed(_leader_pkt(2, 2, 1))
    asm.feed(_payload_pkt(1, 1, b"\xAA\xBB"))  # stale: old block_id=1
    asm.feed(_payload_pkt(2, 1, b"\x11\x22"))
    frame = asm.feed(_trailer_pkt(2, 2))
    assert frame is not None
    assert frame.data == b"\x11\x22"


def test_assembler_ignores_packets_before_leader():
    asm = gvsp.FrameAssembler()
    # no leader yet — payload and trailer should be discarded
    assert asm.feed(_payload_pkt(1, 1, b"\x01\x02")) is None
    assert asm.feed(_trailer_pkt(1, 2)) is None


def test_assembler_second_leader_resets_state():
    """A new Leader for a different block_id resets the assembler mid-frame."""
    asm = gvsp.FrameAssembler()
    asm.feed(_leader_pkt(1, 4, 1))
    asm.feed(_payload_pkt(1, 1, b"\x01\x02\x03\x04"))
    # new frame starts before previous finished
    asm.feed(_leader_pkt(2, 2, 1))
    asm.feed(_payload_pkt(2, 1, b"\x11\x22"))
    frame = asm.feed(_trailer_pkt(2, 2))
    assert frame is not None
    assert frame.block_id == 2
    assert frame.data == b"\x11\x22"


def test_assembler_duplicate_payload_overwrites():
    """Duplicate payload with same packet_id: last write wins (UDP retransmit)."""
    asm = gvsp.FrameAssembler()
    asm.feed(_leader_pkt(1, 2, 1))
    asm.feed(_payload_pkt(1, 1, b"\xAA\xBB"))
    asm.feed(_payload_pkt(1, 1, b"\x11\x22"))  # duplicate id, different bytes
    frame = asm.feed(_trailer_pkt(1, 2))
    assert frame is not None
    assert frame.data == b"\x11\x22"


def test_assembler_late_payload_after_drop_does_not_poison_next_block():
    """A payload arriving AFTER its block was dropped must not leak into the next block."""
    asm = gvsp.FrameAssembler()
    asm.feed(_leader_pkt(1, 4, 1))
    asm.feed(_payload_pkt(1, 1, b"\x01\x02\x03\x04"))
    # trailer declares pid=3 but pid=2 never arrived -> drop
    assert asm.feed(_trailer_pkt(1, 3)) is None
    # late payload for the dropped block: must be ignored
    asm.feed(_payload_pkt(1, 2, b"\xDE\xAD\xBE\xEF"))
    # next block starts clean
    asm.feed(_leader_pkt(2, 2, 1))
    asm.feed(_payload_pkt(2, 1, b"\x11\x22"))
    frame = asm.feed(_trailer_pkt(2, 2))
    assert frame is not None
    assert frame.block_id == 2
    assert frame.data == b"\x11\x22"
