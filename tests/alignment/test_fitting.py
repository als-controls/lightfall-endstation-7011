"""Tests for the pure alignment fitting functions."""
from __future__ import annotations

import numpy as np
from scipy.special import erf

from lucid_endstation_7011.alignment.fitting import fit_falling_edge_halfcut


def _falling_edge(x, floor, baseline, x0, w):
    return floor + (baseline - floor) * 0.5 * (1.0 - erf((x - x0) / (np.sqrt(2.0) * w)))


def test_falling_edge_recovers_halfcut():
    rng = np.random.default_rng(0)
    x = np.linspace(-500, 500, 21)
    y = _falling_edge(x, floor=500.0, baseline=15000.0, x0=120.0, w=40.0)
    y = y + rng.normal(0, 50, x.size)
    fit = fit_falling_edge_halfcut(x, y)
    assert fit.detected
    assert abs(fit.position - 120.0) < 10.0
    assert fit.baseline > fit.floor


def test_flat_signal_has_no_edge():
    rng = np.random.default_rng(1)
    x = np.linspace(-500, 500, 21)
    y = 8000.0 + rng.normal(0, 50, x.size)
    fit = fit_falling_edge_halfcut(x, y)
    assert not fit.detected
    assert fit.reason


def test_rising_edge_not_detected_as_falling():
    x = np.linspace(-500, 500, 21)
    # Low at small x, high at large x => a RISING edge; the falling-edge model
    # must report not-detected (negative step height).
    y = _falling_edge(x, floor=15000.0, baseline=500.0, x0=0.0, w=40.0)
    fit = fit_falling_edge_halfcut(x, y)
    assert not fit.detected


def test_too_few_points():
    fit = fit_falling_edge_halfcut([0.0, 1.0], [1.0, 0.0])
    assert not fit.detected


def test_linear_ramp_not_detected():
    # A gradual monotonic ramp with no real edge (e.g. sample in the wrong
    # z-range) must NOT be reported as an edge — a wide-w erf can mimic a line.
    x = np.linspace(-500, 500, 21)
    y = np.linspace(15000.0, 500.0, 21)
    fit = fit_falling_edge_halfcut(x, y)
    assert not fit.detected, f"ramp falsely detected at {fit.position}, reason={fit.reason!r}"


def test_noisy_ramp_not_detected():
    rng = np.random.default_rng(7)
    x = np.linspace(-500, 500, 21)
    y = np.linspace(15000.0, 500.0, 21) + rng.normal(0, 100, x.size)
    fit = fit_falling_edge_halfcut(x, y)
    assert not fit.detected, f"noisy ramp falsely detected at {fit.position}, reason={fit.reason!r}"


# ---------------------------------------------------------------------------
# Gaussian peak fit (rocking curve)
# ---------------------------------------------------------------------------
from lucid_endstation_7011.alignment.fitting import fit_peak


def _gaussian(x, bg, amp, x0, sigma):
    return bg + amp * np.exp(-((x - x0) ** 2) / (2.0 * sigma ** 2))


def test_peak_recovers_center():
    rng = np.random.default_rng(2)
    x = np.linspace(-5, 5, 41)
    y = _gaussian(x, bg=500.0, amp=12000.0, x0=1.3, sigma=1.0) + rng.normal(0, 50, x.size)
    fit = fit_peak(x, y)
    assert fit.detected
    assert abs(fit.position - 1.3) < 0.25


def test_no_peak_when_flat():
    rng = np.random.default_rng(3)
    x = np.linspace(-5, 5, 41)
    y = 500.0 + rng.normal(0, 50, x.size)
    fit = fit_peak(x, y)
    assert not fit.detected
    assert fit.reason


def test_peak_too_few_points():
    fit = fit_peak([0.0, 1.0, 2.0], [0.0, 1.0, 0.0])
    assert not fit.detected


def test_peak_ramp_not_detected():
    # A monotonic ramp has no localized peak and must NOT be detected.
    x = np.linspace(-5, 5, 41)
    y = np.linspace(500.0, 12000.0, 41)
    fit = fit_peak(x, y)
    assert not fit.detected, f"ramp falsely detected as peak at {fit.position}, reason={fit.reason!r}"
