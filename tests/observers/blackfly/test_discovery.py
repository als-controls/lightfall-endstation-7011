from __future__ import annotations

import socket
import struct
import threading

import pytest

from lucid_endstation_7011.observers.blackfly.discovery import discover


@pytest.fixture
def fake_camera():
    """UDP 'camera' on 127.0.0.1 that answers DISCOVERY_CMD with one canned ACK."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    port = srv.getsockname()[1]
    stop = threading.Event()

    def loop():
        srv.settimeout(0.2)
        while not stop.is_set():
            try:
                data, addr = srv.recvfrom(2048)
            except socket.timeout:
                continue
            req_id = struct.unpack(">H", data[6:8])[0]
            p = bytearray(0xF8)
            p[0x24:0x28] = bytes([127, 0, 0, 1])
            p[0x48:0x48+4] = b"FLIR"
            p[0x68:0x68+14] = b"BFS-PGE-122S6C"
            p[0x88:0x88+10] = b"1707.3.1.0"
            p[0xD8:0xD8+8] = b"18434287"
            p[0xE8:0xE8+11] = b"gantry-left"
            ack = struct.pack(">HHHH", 0, 0x0003, len(p), req_id) + bytes(p)
            srv.sendto(ack, addr)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield port
    stop.set()
    t.join(1)
    srv.close()


def test_discover_finds_fake(fake_camera):
    port = fake_camera
    devs = discover(bind_ip="127.0.0.1", broadcasts=[("127.0.0.1", port)], timeout=1.0)
    assert len(devs) == 1
    assert devs[0].ip == "127.0.0.1"
    assert devs[0].manufacturer == "FLIR"
    assert devs[0].model == "BFS-PGE-122S6C"
    assert devs[0].version == "1707.3.1.0"
    assert devs[0].serial == "18434287"
    assert devs[0].user_name == "gantry-left"


def test_discover_empty_when_no_response():
    # port 59999 is nothing; discover should return empty list, no exception
    devs = discover(bind_ip="127.0.0.1", broadcasts=[("127.0.0.1", 59999)], timeout=0.3)
    assert devs == []
