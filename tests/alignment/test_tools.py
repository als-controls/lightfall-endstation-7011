"""Tests for skill.py module-level helpers (no SDK / no Tiled required)."""
from __future__ import annotations

import numpy as np
from scipy.special import erf

from lightfall_endstation_7011.alignment import skill


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


def test_select_xy_fields_defaults_to_diode():
    xf, yf = skill._select_xy_fields(["sample_lift", "DetectorDiodeCurrent"])
    assert yf == "DetectorDiodeCurrent"
    assert xf == "sample_lift"


def test_select_xy_fields_matches_iode_substring():
    xf, yf = skill._select_xy_fields(["sample_rotate_steppertheta", "diode_current"])
    assert yf == "diode_current"
    assert xf == "sample_rotate_steppertheta"


def test_select_xy_fields_explicit_fields():
    xf, yf = skill._select_xy_fields(["a", "b", "c"], x_field="b", y_field="c")
    assert (xf, yf) == ("b", "c")


def test_select_xy_fields_single_column_raises():
    import pytest

    with pytest.raises(RuntimeError):
        skill._select_xy_fields(["DetectorDiodeCurrent"])


def test_select_xy_fields_empty_raises():
    import pytest

    with pytest.raises(RuntimeError):
        skill._select_xy_fields([])


def test_convergence_status_not_converged():
    res = skill._convergence_status(
        [{"lift": 0.0, "theta": 0.0}, {"lift": 3.0, "theta": 0.1}]
    )
    assert res["converged"] is False
    assert res["num_cycles"] == 2


def test_convergence_status_converged_default():
    cycles = [
        {"lift": 0.0, "theta": 0.0},
        {"lift": 3.0, "theta": 0.1},
        {"lift": 5.0, "theta": 0.2},
    ]
    res = skill._convergence_status(cycles)
    assert res["converged"] is True


def test_convergence_status_accepts_pairs():
    res = skill._convergence_status([[0.0, 0.0], [2.0, 0.1], [3.0, 0.2]])
    assert res["converged"] is True


def test_convergence_status_oscillation_not_converged():
    cycles = [
        {"lift": 0.0, "theta": 0.0},
        {"lift": 50.0, "theta": 0.0},
        {"lift": 0.0, "theta": 0.0},
    ]
    assert skill._convergence_status(cycles)["converged"] is False


# ---------------------------------------------------------------------------
# Issue 4: _select_xy_fields must pick the scanned motor when multiple
# motors are present in a per-step plan. The original silent failure was
# picking sample_rotate_steppertheta (constant) over sample_lift (the
# actually-scanned axis) and reporting halfcut = -2.4° with R²≈0.
# ---------------------------------------------------------------------------


def test_select_xy_fields_picks_largest_variance_column():
    cols = ["sample_rotate_steppertheta", "sample_lift", "DetectorDiodeCurrent"]
    # sample_lift varies; theta is constant. Without an x_field, the picker
    # should choose sample_lift (largest variance) over theta.
    x_data = {
        "sample_rotate_steppertheta": np.zeros(21),
        "sample_lift": np.linspace(-100, 100, 21),
        "DetectorDiodeCurrent": np.linspace(15000, 500, 21),
    }
    xf, yf = skill._select_xy_fields(cols, x_data=x_data)
    assert yf == "DetectorDiodeCurrent"
    assert xf == "sample_lift", (
        f"variance heuristic picked {xf}; expected sample_lift"
    )


def test_select_xy_fields_substring_match_for_x_field():
    # When a per-step plan records a readback rather than the bare device
    # name, x_field='sample_lift' should still match 'sample_lift_user_setpoint'.
    cols = ["sample_lift_user_setpoint", "sample_rotate_steppertheta", "DetectorDiodeCurrent"]
    xf, yf = skill._select_xy_fields(cols, x_field="sample_lift")
    assert yf == "DetectorDiodeCurrent"
    assert xf == "sample_lift_user_setpoint"


def test_fit_lift_from_uid_defaults_x_field_to_lift_motor(monkeypatch):
    """Issue 4 in skill.py: when x_field is None, the wrapper must request
    the sample_lift column (not the first non-y column from a multi-motor
    per-step plan)."""
    captured: dict[str, str | None] = {"xf": None, "yf": None}

    def fake_read(uid, xf, yf):
        captured["xf"], captured["yf"] = xf, yf
        x = np.linspace(-100, 100, 21)
        y = _falling_edge(x, floor=500.0, baseline=15000.0, x0=10.0, w=20.0)
        return x, y

    monkeypatch.setattr(skill, "_read_scan_xy", fake_read)
    skill._fit_lift_from_uid("uid-x")
    assert captured["xf"] == "sample_lift"
    assert captured["yf"] == "DetectorDiodeCurrent"


def test_fit_theta_from_uid_exposes_method_and_boundary(monkeypatch):
    """Issue 5 + 6 (skill surface): the response must include method and
    peak_at_boundary so the agent can decide whether to widen the scan."""
    x = np.linspace(-5, 5, 41)
    y = _gaussian(x, bg=500.0, amp=12000.0, x0=0.8, sigma=1.0)
    monkeypatch.setattr(skill, "_read_scan_xy", lambda uid, xf, yf: (x, y))
    res = skill._fit_theta_from_uid("uid-3")
    assert res["detected"] is True
    assert "method" in res
    assert res["method"] in {
        "gaussian", "voigt", "asymmetric_gaussian", "centroid_topN", "argmax",
    }
    assert "peak_at_boundary" in res
    assert res["peak_at_boundary"] is False


def test_fit_theta_from_uid_flags_boundary_peak(monkeypatch):
    rng = np.random.default_rng(11)
    # Peak just outside the left edge — the scan should NOT result in a
    # confident "detected" with a numeric peak; peak_at_boundary must be True.
    x = np.linspace(-2.0, 8.0, 41)
    y = 500.0 + 12000.0 * np.exp(-((x - (-2.4)) ** 2) / (2.0 * 1.0 ** 2)) + rng.normal(0, 30, x.size)
    monkeypatch.setattr(skill, "_read_scan_xy", lambda uid, xf, yf: (x, y))
    res = skill._fit_theta_from_uid("uid-edge")
    assert res["peak_at_boundary"] is True
    assert res["detected"] is False


# ---------------------------------------------------------------------------
# Issue 1: _beam_status response carries the canonical detector name so the
# agent can route every alignment scan to the same device.
# ---------------------------------------------------------------------------


def test_beam_status_includes_canonical_detector_name():
    cat = _FakeCatalog({"DetectorDiodeCurrent": _diode(15000.0)})
    res = skill._beam_status(cat)
    assert res["detector"] == "DetectorDiodeCurrent"


def test_beam_status_error_still_reports_detector():
    cat = _FakeCatalog({})  # missing diode
    res = skill._beam_status(cat)
    assert res["success"] is False
    assert res["detector"] == "DetectorDiodeCurrent"


# ---------------------------------------------------------------------------
# Issue 10: plot tools must run headless (Agg backend) and return a fit
# summary. We don't assert on pixel content; just that the helper doesn't
# crash and that the summary dict has the keys the agent expects.
# ---------------------------------------------------------------------------


def test_plot_alignment_scan_lift_returns_summary(monkeypatch):
    import matplotlib

    matplotlib.use("Agg", force=True)

    x = np.linspace(-100, 100, 21)
    y = _falling_edge(x, floor=500.0, baseline=15000.0, x0=15.0, w=20.0)
    monkeypatch.setattr(skill, "_read_scan_xy", lambda uid, xf, yf: (x, y))
    summary = skill._plot_alignment_scan_impl("uid-lift", kind="lift")
    assert summary["kind"] == "lift"
    assert summary["motor"] == "sample_lift"
    assert summary["detected"] is True
    assert abs(summary["position"] - 15.0) < 10.0


def test_plot_alignment_scan_theta_returns_summary(monkeypatch):
    import matplotlib

    matplotlib.use("Agg", force=True)

    x = np.linspace(-5, 5, 41)
    y = _gaussian(x, bg=500.0, amp=12000.0, x0=0.5, sigma=1.0)
    monkeypatch.setattr(skill, "_read_scan_xy", lambda uid, xf, yf: (x, y))
    summary = skill._plot_alignment_scan_impl("uid-theta", kind="theta")
    assert summary["kind"] == "theta"
    assert summary["motor"] == "sample_rotate_steppertheta"
    assert summary["detected"] is True
    assert "method" in summary
    assert "peak_at_boundary" in summary


def test_select_xy_fields_skips_event_metadata_in_variance():
    """A Bluesky V3 event stream includes ``time`` (epoch seconds) and
    ``seq_num`` alongside device columns. The variance-based picker must
    skip these — otherwise a 40-second theta scan picks ``time`` (var
    ~140) over the motor (10° span, var ~8) and downstream fits operate
    on epoch values, producing peak positions like 1.7e9.
    """
    events = {
        "time": np.linspace(1.7e9, 1.7e9 + 40.0, 41),
        "seq_num": np.arange(1, 42, dtype=float),
        # The motor column under a name that won't match x_field via
        # exact or substring search — forces the variance fallback.
        "rotation_stage_angle": np.linspace(-5.0, 5.0, 41),
        "DetectorDiodeCurrent": 500.0
        + 12000.0 * np.exp(-((np.linspace(-5, 5, 41) - 0.5) ** 2) / 2.0),
    }
    cols = list(events.keys())
    xf, yf = skill._select_xy_fields(
        cols,
        x_field="sample_rotate_steppertheta",  # no exact / substring match
        y_field="DetectorDiodeCurrent",
        x_data=events,
    )
    assert xf == "rotation_stage_angle", (
        f"variance fallback picked {xf!r}; metadata column should have been skipped"
    )


def test_is_event_metadata_col_classifications():
    assert skill._is_event_metadata_col("time")
    assert skill._is_event_metadata_col("seq_num")
    assert skill._is_event_metadata_col("uid")
    assert skill._is_event_metadata_col("ts_sample_lift")
    assert not skill._is_event_metadata_col("sample_lift")
    assert not skill._is_event_metadata_col("DetectorDiodeCurrent")


def test_plot_convergence_returns_summary():
    import matplotlib

    matplotlib.use("Agg", force=True)

    cycles = [
        {"lift": 0.0, "theta": 0.0},
        {"lift": 8.0, "theta": 0.1},
        {"lift": 12.0, "theta": 0.18},
    ]
    res = skill._plot_convergence_impl(cycles, lift_tol=10.0, theta_tol=0.25)
    assert res["num_cycles"] == 3
    assert res["lift_tol"] == 10.0
    assert res["theta_tol"] == 0.25
    assert len(res["history"]) == 3
