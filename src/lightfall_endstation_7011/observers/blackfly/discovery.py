"""Broadcast GVCP DISCOVERY_CMD and collect ACKs."""
from __future__ import annotations

import socket
import struct
import time
from typing import Iterable

from . import gvcp


def discover(
    bind_ip: str,
    broadcasts: Iterable[tuple[str, int]] = (("255.255.255.255", gvcp.GVCP_PORT),),
    timeout: float = 2.0,
) -> list[gvcp.DeviceInfo]:
    """Send GVCP DISCOVERY_CMD to each broadcast target; collect ACKs for `timeout` seconds.

    `bind_ip` should be the local NIC IP that's on the camera subnet (for a single-NIC
    host, "0.0.0.0" is fine; for multi-NIC hosts, pick the interface that reaches the
    cameras, e.g. "192.168.10.42").
    """
    sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sk.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sk.bind((bind_ip, 0))
    sk.settimeout(0.25)
    try:
        pkt = gvcp.build_discovery_cmd(req_id=1)
        for dest in broadcasts:
            sk.sendto(pkt, dest)

        # Same camera may answer multiple broadcasts (limited + directed). The ACK payload
        # is a deterministic snapshot of bootstrap registers, so last-write-wins is safe.
        seen: dict[str, gvcp.DeviceInfo] = {}
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data, _ = sk.recvfrom(65535)
            except socket.timeout:
                continue
            except ConnectionResetError:
                # Windows surfaces ICMP port-unreachable this way; ignore and keep listening.
                continue
            try:
                hdr, payload = gvcp.parse_ack_header(data)
            except (ValueError, struct.error):
                # Truncated/malformed header from random network chatter — ignore.
                continue
            if hdr.ack_cmd != 0x0003:  # DISCOVERY_ACK
                continue
            try:
                info = gvcp.parse_discovery_ack(payload)
            except (ValueError, struct.error) as e:
                # This packet claimed to be a discovery ack but the payload didn't parse.
                # Log it to stderr so the operator sees misbehaving devices instead of silently
                # dropping them to an empty result list.
                import warnings
                warnings.warn(f"dropping malformed DISCOVERY_ACK from network: {e!r}", RuntimeWarning)
                continue
            seen[info.ip] = info
        return list(seen.values())
    finally:
        sk.close()
