from __future__ import annotations

import socket
import struct
import threading

import pytest

from lightfall_endstation_7011.observers.blackfly import gvcp
from lightfall_endstation_7011.observers.blackfly.gvcp_transport import GvcpClient, GvcpError


@pytest.fixture
def fake_server():
    """UDP server on 127.0.0.1 that replies to READREG, WRITEREG, READMEM based on the request cmd."""
    srv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
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
            # GVCP request header: key(1) flags(1) cmd(2) length(2) req_id(2)
            cmd = struct.unpack(">H", data[2:4])[0]
            req_id = struct.unpack(">H", data[6:8])[0]
            if cmd == gvcp.CMD_READREG:
                reg_addr = struct.unpack(">I", data[8:12])[0]
                value = {0x0A00: 0x1234, 0x0000: 0x00010002}.get(reg_addr, 0xDEADBEEF)
                ack = struct.pack(">HHHH", 0, 0x0081, 4, req_id) + struct.pack(">I", value)
            elif cmd == gvcp.CMD_WRITEREG:
                ack = struct.pack(">HHHH", 0, 0x0083, 0, req_id)
            elif cmd == gvcp.CMD_READMEM:
                mem_addr = struct.unpack(">I", data[8:12])[0]
                count = struct.unpack(">H", data[14:16])[0]
                # Echo the address back, return `count` bytes of deterministic filler
                payload = struct.pack(">I", mem_addr) + bytes((i % 256) for i in range(count))
                ack = struct.pack(">HHHH", 0, 0x0085, len(payload), req_id) + payload
            else:
                continue
            srv.sendto(ack, addr)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
    yield "127.0.0.1", port
    stop.set()
    t.join(timeout=1)
    srv.close()


def test_read_register_roundtrip(fake_server):
    host, port = fake_server
    with GvcpClient(bind_ip="127.0.0.1", device_ip=host, device_port=port, timeout=0.5) as client:
        assert client.read_register(0x0A00) == 0x1234
        assert client.read_register(0x0000) == 0x00010002


def test_write_register_roundtrip(fake_server):
    host, port = fake_server
    with GvcpClient(bind_ip="127.0.0.1", device_ip=host, device_port=port, timeout=0.5) as client:
        client.write_register(0x0A00, 0x00000002)  # takes CCP exclusive


def test_read_memory_roundtrip(fake_server):
    host, port = fake_server
    with GvcpClient(bind_ip="127.0.0.1", device_ip=host, device_port=port, timeout=0.5) as client:
        data = client.read_memory(0x0200, 16)
        # fake server returned bytes (0..15); transport must have stripped the 4-byte echoed addr
        assert data == bytes(range(16))
        assert len(data) == 16  # not 20 — the 4B echoed addr was stripped


def test_timeout_when_no_server():
    # 192.0.2.0/24 is TEST-NET-1 (RFC 5737): unroutable, no ICMP reply,
    # so recvfrom genuinely times out (not ConnectionResetError).
    with GvcpClient(bind_ip="0.0.0.0", device_ip="192.0.2.1",
                    device_port=3956, timeout=0.2, retries=0) as client:
        with pytest.raises(TimeoutError):
            client.read_register(0x0A00)


def test_connection_reset_falls_through_to_timeout(fake_server, monkeypatch):
    """Simulate Windows ICMP port-unreachable: recvfrom raises ConnectionResetError.
    The transport should treat it like a timeout, retry, then raise TimeoutError."""
    host, port = fake_server
    client = GvcpClient(bind_ip="127.0.0.1", device_ip=host, device_port=port,
                        timeout=0.2, retries=1)
    try:
        # Wrap the socket: built-in socket methods are read-only slots, so we
        # can't monkeypatch recvfrom directly on the instance. Replace the
        # whole socket attr with a proxy that forwards sendto/close but raises
        # ConnectionResetError on recvfrom.
        real_sock = client._sk

        class FakeSock:
            def sendto(self, data, addr):
                return real_sock.sendto(data, addr)
            def recvfrom(self, bufsize):
                raise ConnectionResetError("simulated ICMP port unreachable")
            def close(self):
                return real_sock.close()

        monkeypatch.setattr(client, "_sk", FakeSock())
        with pytest.raises(TimeoutError):
            client.read_register(0x0A00)
    finally:
        client.close()
