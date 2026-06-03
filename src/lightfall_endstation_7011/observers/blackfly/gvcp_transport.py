"""UDP transport for GVCP: req/ack matching with timeout + retry."""
from __future__ import annotations

import itertools
import socket
import threading
from typing import Callable, TypeVar

from . import gvcp

T = TypeVar("T")


class GvcpError(RuntimeError):
    """Non-zero status in a GVCP ACK."""


class GvcpClient:
    """Single-device GVCP client. One UDP socket, serialized request/response."""

    def __init__(
        self,
        bind_ip: str,
        device_ip: str,
        device_port: int = gvcp.GVCP_PORT,
        timeout: float = 1.0,
        retries: int = 2,
    ):
        self._lock = threading.Lock()
        self._sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sk.bind((bind_ip, 0))
        self._sk.settimeout(timeout)
        self._device = (device_ip, device_port)
        self._retries = retries
        # itertools.cycle + next() is atomic under CPython's GIL; safe to call outside the lock.
        self._req_ids = itertools.cycle(range(1, 0x10000))

    def _next_req_id(self) -> int:
        return next(self._req_ids)

    def _call(self, pkt_builder: Callable[[int], bytes], ack_parser: Callable[[bytes], T]) -> T:
        # req_id is generated ONCE per call (not per retry): all retry attempts for
        # this logical call share the same req_id, so late ACKs from earlier retries
        # still match. Late ACKs from prior calls mismatch and are dropped by the
        # `ack_id != req_id` continue inside the recv loop.
        req_id = self._next_req_id()
        pkt = pkt_builder(req_id)
        with self._lock:
            last_err: BaseException | None = None
            for _ in range(self._retries + 1):
                self._sk.sendto(pkt, self._device)
                try:
                    while True:
                        data, _ = self._sk.recvfrom(65535)
                        hdr, payload = gvcp.parse_ack_header(data)
                        if hdr.ack_id != req_id:
                            # Stale ack from a previous (retried) request — ignore
                            continue
                        if hdr.status != 0:
                            raise GvcpError(f"GVCP status=0x{hdr.status:04x} ack_cmd=0x{hdr.ack_cmd:04x}")
                        return ack_parser(payload)
                except socket.timeout as e:
                    last_err = e
                except ConnectionResetError as e:
                    # Windows: ICMP "port unreachable" from a closed UDP port
                    # surfaces here. Treat as "no response" — same as a timeout.
                    last_err = e
            raise TimeoutError(f"GVCP call to {self._device} timed out") from last_err

    def read_register(self, addr: int) -> int:
        return self._call(
            lambda rid: gvcp.build_readreg_cmd(addr, rid),
            gvcp.parse_readreg_ack,
        )

    def write_register(self, addr: int, value: int) -> None:
        self._call(
            lambda rid: gvcp.build_writereg_cmd(addr, value, rid),
            lambda _payload: None,
        )

    def read_memory(self, addr: int, count: int) -> bytes:
        """Read `count` bytes starting at `addr`. count must be a multiple of 4."""
        def parse(payload: bytes) -> bytes:
            echoed_addr, data = gvcp.parse_readmem_ack(payload)
            if echoed_addr != addr:
                raise GvcpError(f"READMEM echoed addr 0x{echoed_addr:08x} != requested 0x{addr:08x}")
            return data

        return self._call(
            lambda rid: gvcp.build_readmem_cmd(addr, count, rid),
            parse,
        )

    def close(self) -> None:
        self._sk.close()

    def __enter__(self) -> "GvcpClient":
        return self

    def __exit__(self, *a) -> None:
        self.close()
