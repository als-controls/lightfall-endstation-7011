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


def _falling_edge(x, floor, baseline, x0, w):
    return floor + (baseline - floor) * 0.5 * (1.0 - erf((x - x0) / (np.sqrt(2.0) * w)))


def _gaussian(x, bg, amp, x0, sigma):
    return bg + amp * np.exp(-((x - x0) ** 2) / (2.0 * sigma ** 2))


def test_fit_lift_from_uid(monkeypatch):
    x = np.linspace(-500, 500, 21)
    y = _falling_edge(x, floor=500.0, baseline=15000.0, x0=110.0, w=40.0)
    monkeypatch.setattr(skill, "_read_scan_xy", lambda uid, xf, yf: (x, y))
    res = skill._fit_lift_from_uid("uid-1")
    assert res["detected"] is True
    assert abs(res["halfcut"] - 110.0) < 10.0


def test_fit_lift_from_uid_no_edge(monkeypatch):
    x = np.linspace(-500, 500, 21)
    y = np.full_like(x, 8000.0)
    monkeypatch.setattr(skill, "_read_scan_xy", lambda uid, xf, yf: (x, y))
    res = skill._fit_lift_from_uid("uid-2")
    assert res["detected"] is False
    assert res["reason"]


def test_fit_theta_from_uid(monkeypatch):
    x = np.linspace(-5, 5, 41)
    y = _gaussian(x, bg=500.0, amp=12000.0, x0=0.8, sigma=1.0)
    monkeypatch.setattr(skill, "_read_scan_xy", lambda uid, xf, yf: (x, y))
    res = skill._fit_theta_from_uid("uid-3")
    assert res["detected"] is True
    assert abs(res["peak"] - 0.8) < 0.25
