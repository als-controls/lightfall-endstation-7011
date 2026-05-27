"""Tests for skill.py module-level helpers (no SDK / no Tiled required)."""
from __future__ import annotations

import numpy as np
from scipy.special import erf

from lucid_endstation_7011.alignment import skill


class _FakeOphyd:
    def __init__(self, reading):
        self._reading = reading

    def read(self):
        return self._reading


class _FakeDeviceInfo:
    def __init__(self, ophyd):
        self.ophyd_device = ophyd


class _FakeCatalog:
    def __init__(self, devices, connected=True):
        self._devices = devices
        self.is_connected = connected

    def get_device_by_name(self, name):
        return self._devices.get(name)


def _diode(value):
    return _FakeDeviceInfo(_FakeOphyd({"DetectorDiodeCurrent": {"value": value, "timestamp": 0}}))


def test_beam_present_above_threshold():
    cat = _FakeCatalog({"DetectorDiodeCurrent": _diode(15000.0)})
    res = skill._beam_status(cat)
    assert res["success"] is True
    assert res["current_nA"] == 15000.0
    assert res["beam_present"] is True


def test_no_beam_below_threshold():
    cat = _FakeCatalog({"DetectorDiodeCurrent": _diode(200.0)})
    res = skill._beam_status(cat)
    assert res["beam_present"] is False


def test_beam_status_missing_device():
    cat = _FakeCatalog({})
    res = skill._beam_status(cat)
    assert res["success"] is False


def test_beam_status_catalog_disconnected():
    cat = _FakeCatalog({"DetectorDiodeCurrent": _diode(15000.0)}, connected=False)
    res = skill._beam_status(cat)
    assert res["success"] is False
