from __future__ import annotations

import os
import pytest
from lucid_endstation_7011.observers.blackfly.camera import BlackflyCamera


@pytest.mark.hw
def test_open_close_cycle(camera_ip):
    bind_ip = os.environ["BLACKFLY_BIND_IP"]
    cam = BlackflyCamera(camera_ip, bind_ip)
    cam.open()
    try:
        info = cam.read_device_info()
        assert info.model.startswith("Blackfly")
    finally:
        cam.close()


@pytest.mark.hw
def test_heartbeat_keeps_ccp(camera_ip):
    import time
    bind_ip = os.environ["BLACKFLY_BIND_IP"]
    with BlackflyCamera(camera_ip, bind_ip, heartbeat_timeout_ms=2000) as cam:
        time.sleep(5)  # > heartbeat_timeout; should still be alive
        # verify by reading a register
        from lucid_endstation_7011.observers.blackfly import registers
        ccp = cam._client.read_register(registers.REG_CCP)
        assert (ccp & registers.CCP_CONTROL) != 0, f"CCP lost after heartbeat window, got 0x{ccp:08x}"


@pytest.mark.hw
def test_read_geometry(camera_ip):
    bind_ip = os.environ["BLACKFLY_BIND_IP"]
    with BlackflyCamera(camera_ip, bind_ip) as cam:
        geom = cam.read_geometry()
        assert 64 <= geom.width <= 8192
        assert 64 <= geom.height <= 8192
        print(f"geometry: {geom.width}x{geom.height} pixfmt=0x{geom.pixel_format:08x}")


@pytest.mark.hw
def test_stream_receives_frame(camera_ip):
    import time
    bind_ip = os.environ["BLACKFLY_BIND_IP"]
    received = []

    with BlackflyCamera(camera_ip, bind_ip) as cam:
        cam.start_stream(on_frame=received.append)
        time.sleep(3)
        cam.stop_stream()

    assert len(received) > 0, "no frames received in 3s"
    img = received[0]
    print(f"got {len(received)} frames, first={img.shape} dtype={img.dtype}")
    assert img.ndim == 2
