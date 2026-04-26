"""High-level Blackfly camera: owns CCP, heartbeat, UDP stream channel."""
from __future__ import annotations

import logging
import socket
import struct
import sys
import threading
from dataclasses import dataclass
from typing import Callable

import numpy as np

from lucid.ui.widgets.observers import CameraBase

from . import gvcp, gvsp, pixel_formats, registers
from .gvcp_transport import GvcpClient

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Geometry:
    width: int
    height: int
    pixel_format: int


class BlackflyCamera(CameraBase):
    """FLIR Blackfly S camera over GVCP/GVSP, no vendor SDK.

    Args:
        device_ip: the camera's IPv4 address.
        bind_ip:   the host NIC's IPv4 address the camera should send GVSP packets to.
                   These two are easy to swap; the camera_ip is the *target*, the bind_ip
                   is the *listener*.
        heartbeat_timeout_ms: device-side timeout if the host stops sending heartbeats.

    Lifecycle: ``open()`` → ``start_stream(on_frame=…)`` → … → ``stop_stream()`` → ``close()``.
    Or use as a context manager: ``with BlackflyCamera(...) as cam: cam.start_stream(...)``;
    ``close()`` will stop an active stream before releasing CCP.
    """

    def __init__(self, device_ip: str, bind_ip: str, heartbeat_timeout_ms: int = 3000):
        self._client = GvcpClient(bind_ip=bind_ip, device_ip=device_ip, timeout=1.0)
        self._device_ip = device_ip
        self._bind_ip = bind_ip
        self._heartbeat_timeout = heartbeat_timeout_ms
        self._heartbeat_thread: threading.Thread | None = None
        self._heartbeat_stop = threading.Event()
        self._opened = False
        self._stream_sk: socket.socket | None = None
        self._receiver_thread: threading.Thread | None = None
        self._receiver_stop = threading.Event()
        self._on_frame: Callable[[np.ndarray], None] | None = None
        self._latest_frame: np.ndarray | None = None
        self._latest_lock = threading.Lock()

    def open(self) -> None:
        if self._opened:
            return
        # CCP must be acquired before HEARTBEAT_TIMEOUT — the timeout register
        # is writable only by the active controller (matches aravis order).
        self._client.write_register(registers.REG_CCP, registers.CCP_CONTROL)
        ccp = self._client.read_register(registers.REG_CCP)
        if (ccp & (registers.CCP_CONTROL | registers.CCP_EXCLUSIVE)) == 0:
            raise RuntimeError(f"failed to acquire CCP, got 0x{ccp:08x}")
        self._client.write_register(registers.REG_HEARTBEAT_TIMEOUT, self._heartbeat_timeout)
        self._heartbeat_stop.clear()
        self._heartbeat_thread = threading.Thread(
            target=self._heartbeat_loop, daemon=True, name="blackfly-heartbeat",
        )
        self._heartbeat_thread.start()
        self._opened = True

    def close(self) -> None:
        if not self._opened:
            return
        # Stop the stream first if active — stop_acquisition needs CCP, which we're
        # about to release. Done before joining the heartbeat to keep the keepalive
        # alive while we send the AcquisitionStop command.
        if self._receiver_thread is not None and self._receiver_thread.is_alive():
            try:
                self.stop_stream()
            except Exception as e:
                _log.warning("stop_stream during close raised: %r", e)
        self._heartbeat_stop.set()
        if self._heartbeat_thread:
            self._heartbeat_thread.join(timeout=4.0)  # GvcpClient worst case is ~3s
            if self._heartbeat_thread.is_alive():
                _log.warning("heartbeat thread did not stop within 4s; releasing CCP anyway")
        try:
            self._client.write_register(registers.REG_CCP, registers.CCP_NONE)
        except Exception as e:
            _log.warning("CCP release failed: %r", e)
        self._client.close()
        self._opened = False

    def _heartbeat_loop(self) -> None:
        interval = 1.0  # aravis uses a fixed 1s period; see ARV_GV_DEVICE_HEARTBEAT_PERIOD_US
        while not self._heartbeat_stop.wait(interval):
            try:
                ccp = self._client.read_register(registers.REG_CCP)
            except Exception as e:
                _log.warning("heartbeat read failed: %r", e)
                continue
            if (ccp & (registers.CCP_CONTROL | registers.CCP_EXCLUSIVE)) == 0:
                _log.error("control lost: CCP register reads 0x%08x", ccp)
                return  # exit loop; no point keepaliving on a lost channel

    def read_device_info(self) -> gvcp.DeviceInfo:
        # Lazy import is cosmetic — discovery does not import camera, so no cycle to break.
        from .discovery import discover
        devs = [d for d in discover(self._bind_ip, [(self._device_ip, gvcp.GVCP_PORT)], timeout=1.0)
                if d.ip == self._device_ip]
        if not devs:
            raise RuntimeError(f"no discovery response from {self._device_ip}")
        return devs[0]

    def read_geometry(self) -> Geometry:
        return Geometry(
            width=self._client.read_register(registers.REG_WIDTH),
            height=self._client.read_register(registers.REG_HEIGHT),
            pixel_format=self._client.read_register(registers.REG_PIXEL_FORMAT),
        )

    def configure_stream(self, host_ip: str, host_port: int, packet_size: int = 1400) -> None:
        """Point SC0 at host_ip:host_port and set packet size."""
        host_ipv4 = struct.unpack(">I", socket.inet_aton(host_ip))[0]
        self._client.write_register(registers.REG_SC0_DEST_ADDR, host_ipv4)
        # Low 16 bits of SC0_PORT_HOST is the host port.
        self._client.write_register(registers.REG_SC0_PORT_HOST, host_port & 0xFFFF)
        # SCPS0_PacketSize is a MaskedIntReg (low 16b = size, bits 29/30/31 = BigEndian/DoNotFragment/FireTest).
        # Read-modify-write preserves the flags the camera firmware has latched (matches aravis arvgvdevice.c:1690-1708).
        cur_pkt_reg = self._client.read_register(registers.REG_SC0_PACKET_SIZE)
        new_pkt_reg = (cur_pkt_reg & 0xE0000000) | (packet_size & 0xFFFF)
        self._client.write_register(registers.REG_SC0_PACKET_SIZE, new_pkt_reg)

    def start_acquisition(self) -> None:
        self._client.write_register(registers.REG_ACQUISITION_MODE, registers.ACQUISITION_MODE_CONTINUOUS)
        self._client.write_register(registers.REG_ACQUISITION_START, 1)

    def stop_acquisition(self) -> None:
        self._client.write_register(registers.REG_ACQUISITION_STOP, 1)

    def start_stream(
        self,
        on_frame: Callable[[np.ndarray], None] | None = None,
        packet_size: int = 1400,
    ) -> None:
        """Opens a UDP listener, configures the camera stream, and starts acquisition."""
        self._stream_sk = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._stream_sk.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
        actual_rcvbuf = self._stream_sk.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        requested = 16 * 1024 * 1024
        # Linux reports 2× the requested size on getsockopt (skb-overhead bookkeeping);
        # other platforms report the actual buffer size.
        effective = actual_rcvbuf // 2 if sys.platform.startswith("linux") else actual_rcvbuf
        if effective < requested:
            _log.warning(
                "SO_RCVBUF clamped to %d bytes (requested %d); "
                "check /proc/sys/net/core/rmem_max — expect packet loss at high framerate",
                actual_rcvbuf, requested,
            )
        self._stream_sk.bind((self._bind_ip, 0))
        host_port = self._stream_sk.getsockname()[1]

        self.configure_stream(self._bind_ip, host_port, packet_size)
        self._receiver_stop = threading.Event()
        self._on_frame = on_frame
        self._latest_frame = None
        self._receiver_thread = threading.Thread(
            target=self._receiver_loop, daemon=True, name="blackfly-receiver",
        )
        self._receiver_thread.start()
        self.start_acquisition()

    def stop_stream(self) -> None:
        try:
            self.stop_acquisition()
        finally:
            self._receiver_stop.set()
            # Closing the socket from this thread surfaces as OSError in the receiver
            # loop's recvfrom(), which is the documented wakeup mechanism — see
            # _receiver_loop's `except OSError: break`.
            if self._stream_sk is not None:
                self._stream_sk.close()
            if self._receiver_thread is not None:
                self._receiver_thread.join(timeout=2.0)

    def get_latest_frame(self) -> np.ndarray | None:
        with self._latest_lock:
            return self._latest_frame

    def _receiver_loop(self) -> None:
        asm = gvsp.FrameAssembler()
        self._stream_sk.settimeout(0.5)
        while not self._receiver_stop.is_set():
            try:
                data, _ = self._stream_sk.recvfrom(65536)
            except socket.timeout:
                continue
            except OSError:
                break  # socket closed by stop_stream
            try:
                pkt = gvsp.parse_packet(data)
            except Exception:
                continue
            frame = asm.feed(pkt)
            if frame is None:
                continue
            try:
                img = pixel_formats.decode(
                    frame.data, frame.leader.width, frame.leader.height, frame.leader.pixel_format
                )
            except Exception as e:
                _log.warning("decode error: %r", e)
                continue
            with self._latest_lock:
                self._latest_frame = img
            if self._on_frame is not None:
                try:
                    self._on_frame(img)
                except Exception as e:
                    _log.warning("on_frame callback raised: %r", e)
