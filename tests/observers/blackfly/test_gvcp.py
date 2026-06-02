import struct

import pytest

from lightfall_endstation_7011.observers.blackfly import gvcp


def test_build_discovery_cmd():
    pkt = gvcp.build_discovery_cmd(req_id=1)
    # key=0x42, flags=0x11 (ack+broadcast), cmd=0x0002, len=0x0000, req_id=1
    assert pkt == bytes.fromhex("4211000200000001")


def test_build_readreg_cmd():
    pkt = gvcp.build_readreg_cmd(addr=0x0A00, req_id=42)
    # header(8) + addr(4) = 12 bytes; length field = 0x0004; flags = 0x01 (ack only, no broadcast)
    assert len(pkt) == 12
    assert pkt[:8] == bytes.fromhex("420100800004002A")
    assert pkt[8:12] == bytes.fromhex("00000A00")


def test_build_writereg_cmd():
    pkt = gvcp.build_writereg_cmd(addr=0x0A00, value=2, req_id=7)
    # header(8) + addr(4) + value(4) = 16 bytes; length field = 0x0008
    assert len(pkt) == 16
    assert pkt[:8] == bytes.fromhex("4201008200080007")
    assert pkt[8:12] == bytes.fromhex("00000A00")
    assert pkt[12:16] == bytes.fromhex("00000002")


def test_build_readmem_cmd():
    pkt = gvcp.build_readmem_cmd(addr=0x0200, count=512, req_id=3)
    # header(8) + addr(4) + reserved(2) + count(2) = 16 bytes; length field = 0x0008
    assert len(pkt) == 16
    assert pkt[:8] == bytes.fromhex("4201008400080003")
    assert pkt[8:12] == bytes.fromhex("00000200")
    assert pkt[12:16] == bytes.fromhex("00000200")  # reserved=0x0000, count=0x0200


def test_build_readmem_cmd_rejects_unaligned_count():
    with pytest.raises(ValueError, match="multiple of 4"):
        gvcp.build_readmem_cmd(addr=0x0200, count=3, req_id=3)


def test_build_readreg_cmd_large_addr_and_req_id():
    pkt = gvcp.build_readreg_cmd(addr=0x60000000, req_id=0xFFFF)
    assert len(pkt) == 12
    assert pkt[:8] == bytes.fromhex("420100800004FFFF")
    assert pkt[8:12] == bytes.fromhex("60000000")


def test_build_writereg_cmd_rejects_out_of_range_value():
    with pytest.raises(ValueError, match="out of i32/u32 range"):
        gvcp.build_writereg_cmd(addr=0x0A00, value=0x100000000, req_id=1)


def test_build_writereg_cmd_accepts_negative_int():
    pkt = gvcp.build_writereg_cmd(addr=0x0A00, value=-1, req_id=1)
    assert pkt[12:16] == bytes.fromhex("FFFFFFFF")


def test_parse_ack_header_ok():
    raw = bytes.fromhex("0000008100040001") + struct.pack(">I", 0xDEADBEEF)
    hdr, payload = gvcp.parse_ack_header(raw)
    assert hdr.status == 0
    assert hdr.ack_cmd == 0x0081  # READREG_ACK
    assert hdr.length == 4
    assert hdr.ack_id == 1
    assert payload == struct.pack(">I", 0xDEADBEEF)


def test_parse_ack_header_error_status():
    raw = bytes.fromhex("8001008100000001")
    hdr, payload = gvcp.parse_ack_header(raw)
    assert hdr.status == 0x8001
    assert hdr.ack_cmd == 0x0081
    assert hdr.length == 0
    assert hdr.ack_id == 1
    assert payload == b""


def test_parse_ack_header_too_short():
    with pytest.raises(ValueError, match="too short"):
        gvcp.parse_ack_header(b"\x00" * 7)


def test_parse_discovery_ack():
    # Per GigE Vision 1.2: MANUFACTURER_NAME @ 0x48 (32B), MODEL_NAME @ 0x68 (32B),
    # DEVICE_VERSION @ 0x88 (32B), SERIAL_NUMBER @ 0xD8 (16B), USER_DEFINED_NAME @ 0xE8 (16B).
    p = bytearray(0xF8)
    p[0x48:0x48+32] = b"FLIR" + b"\x00" * 28
    p[0x68:0x68+32] = b"BFS-PGE-122S6C" + b"\x00" * 18
    p[0x88:0x88+32] = b"1707.3.1.0" + b"\x00" * 22
    p[0xD8:0xD8+16] = b"18434287" + b"\x00" * 8
    p[0xE8:0xE8+16] = b"gantry-left" + b"\x00" * 5
    p[0x24:0x28] = bytes([192, 168, 10, 81])
    info = gvcp.parse_discovery_ack(bytes(p))
    assert info.ip == "192.168.10.81"
    assert info.manufacturer == "FLIR"
    assert info.model == "BFS-PGE-122S6C"
    assert info.version == "1707.3.1.0"
    assert info.serial == "18434287"
    assert info.user_name == "gantry-left"


def test_parse_discovery_ack_too_short():
    with pytest.raises(ValueError, match="too short"):
        gvcp.parse_discovery_ack(b"\x00" * (0xF8 - 1))


def test_parse_readreg_ack():
    assert gvcp.parse_readreg_ack(struct.pack(">I", 0xCAFEBABE)) == 0xCAFEBABE


def test_parse_readreg_ack_too_short():
    with pytest.raises(ValueError, match="too short"):
        gvcp.parse_readreg_ack(b"\x00\x00")


def test_parse_readmem_ack():
    # READMEM_ACK payload = 4B echoed address + N data bytes
    payload = struct.pack(">I", 0x1000) + b"hello world\x00"
    addr, data = gvcp.parse_readmem_ack(payload)
    assert addr == 0x1000
    assert data == b"hello world\x00"


def test_parse_readmem_ack_too_short():
    with pytest.raises(ValueError, match="too short"):
        gvcp.parse_readmem_ack(b"\x00\x00\x00")
