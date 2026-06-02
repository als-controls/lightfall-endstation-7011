"""CLI: list GigE Vision cameras on a given NIC subnet.

Installed as the `bfly-discover` console script via pyproject.toml [project.scripts].
"""
from __future__ import annotations

import argparse
import socket
import sys

from lightfall_endstation_7011.observers.blackfly import gvcp
from lightfall_endstation_7011.observers.blackfly.discovery import discover


def _default_bind_ip() -> str:
    """Best-effort: outgoing-route IP toward 8.8.8.8. No packet sent (UDP connect is local)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def _local_ipv4_addresses() -> list[str]:
    """Return all non-loopback IPv4 addresses of this host."""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except socket.gaierror:
        return []
    seen: list[str] = []
    for info in infos:
        ip = info[4][0]
        if ip.startswith("127.") or ip in seen:
            continue
        seen.append(ip)
    return seen


def main() -> None:
    ap = argparse.ArgumentParser(description="Discover GigE Vision cameras on a subnet.")
    ap.add_argument("--bind-ip", default=None,
                    help="local NIC IP to broadcast from (default: auto-detect via default route)")
    ap.add_argument("--subnet-broadcast", default=None,
                    help="directed broadcast address (e.g. 192.168.10.255)")
    ap.add_argument("--timeout", type=float, default=2.0,
                    help="seconds to collect ACKs (default: 2.0)")
    args = ap.parse_args()

    if args.bind_ip:
        bind_ip = args.bind_ip
    else:
        bind_ip = _default_bind_ip()
        all_ips = _local_ipv4_addresses()
        if len(all_ips) > 1:
            print(
                f"warning: multiple local IPv4 addresses ({', '.join(all_ips)}); "
                f"auto-detected {bind_ip} via default route. If cameras are on a different "
                f"subnet, rerun with --bind-ip <nic-ip>.",
                file=sys.stderr,
            )

    targets: list[tuple[str, int]] = [("255.255.255.255", gvcp.GVCP_PORT)]
    if args.subnet_broadcast:
        targets.append((args.subnet_broadcast, gvcp.GVCP_PORT))

    print(f"discovering via {bind_ip} (broadcasts: {', '.join(t[0] for t in targets)}) ...",
          file=sys.stderr)
    devs = discover(bind_ip=bind_ip, broadcasts=targets, timeout=args.timeout)
    print(f"found {len(devs)} device(s)")
    for d in devs:
        print(f"  {d.ip:16s}  {d.manufacturer:20s} {d.model:28s} sn={d.serial:16s} name={d.user_name!r}")


if __name__ == "__main__":
    main()
