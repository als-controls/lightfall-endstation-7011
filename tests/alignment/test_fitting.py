"""Tests for the pure alignment fitting functions."""
from __future__ import annotations

import numpy as np
from scipy.special import erf

from lucid_endstation_7011.alignment.fitting import (
    POSITION_R2_FLOOR,
    fit_falling_edge_halfcut,
    fit_peak,
)


def _falling_edge(x, floor, baseline, x0, w):
    return floor + (baseline - floor) * 0.5 * (1.0 - erf((x - x0) / (np.sqrt(2.0) * w)))


# ---------------------------------------------------------------------------
# Falling-edge / halfcut
# ---------------------------------------------------------------------------


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


def test_low_r2_nulls_position():
    """Issue 4 fix: a converged-but-junk fit must not leak a position number.

    Construct a noisy ramp that the optimizer will "converge" on but whose
    R^2 is below POSITION_R2_FLOOR — the returned position must be None so
    the caller cannot move a motor there.
    """
    rng = np.random.default_rng(13)
    x = np.linspace(-500, 500, 21)
    # Almost-flat plus large noise: optimizer converges, R^2 is poor.
    y = 8000.0 + rng.normal(0, 8000.0, x.size)
    fit = fit_falling_edge_halfcut(x, y)
    assert not fit.detected
    # If R^2 ended up below the floor, the position must be None.
    if fit.r2 is not None and fit.r2 < POSITION_R2_FLOOR:
        assert fit.position is None, (
            f"low-R^2 fit leaked position={fit.position} with R^2={fit.r2}"
        )


# ---------------------------------------------------------------------------
# Gaussian peak fit (rocking curve) — happy paths
# ---------------------------------------------------------------------------


def _gaussian(x, bg, amp, x0, sigma):
    return bg + amp * np.exp(-((x - x0) ** 2) / (2.0 * sigma ** 2))


def test_peak_recovers_center():
    rng = np.random.default_rng(2)
    x = np.linspace(-5, 5, 41)
    y = _gaussian(x, bg=500.0, amp=12000.0, x0=1.3, sigma=1.0) + rng.normal(0, 50, x.size)
    fit = fit_peak(x, y)
    assert fit.detected
    assert abs(fit.position - 1.3) < 0.25
    assert fit.method == "gaussian"
    assert fit.peak_at_boundary is False


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
    # Issue 6: a monotonic ramp has no localized peak inside the scan; we
    # must NOT detect a peak, but should flag peak_at_boundary so the agent
    # widens the scan instead of moving. Which method "fits" the ramp is
    # incidental (Voigt can match one tail of a ramp); the contract is the
    # boundary flag + detected=False.
    x = np.linspace(-5, 5, 41)
    y = np.linspace(500.0, 12000.0, 41)
    fit = fit_peak(x, y)
    assert not fit.detected
    assert fit.peak_at_boundary is True


# ---------------------------------------------------------------------------
# Peak fit issues — asymmetric, flat-top, boundary
# ---------------------------------------------------------------------------


def test_asymmetric_peak_detected():
    """Issue 5: a slightly asymmetric peak (left tail wider than right) must
    still be detected via the asymmetric-Gaussian or Voigt tier.
    """
    rng = np.random.default_rng(11)
    x = np.linspace(-5, 5, 81)
    # Wider left tail, narrower right tail.
    s = np.where(x < -1.0, 1.8, 0.8)
    y = 500.0 + 12000.0 * np.exp(-((x - (-1.0)) ** 2) / (2.0 * s ** 2))
    y = y + rng.normal(0, 50, x.size)
    fit = fit_peak(x, y)
    assert fit.detected, f"asymmetric peak not detected: reason={fit.reason!r}, method={fit.method}"
    assert abs(fit.position - (-1.0)) < 0.6
    # We accept any of the parametric methods.
    assert fit.method in {"gaussian", "voigt", "asymmetric_gaussian"}


def test_flat_top_peak_detected_via_fallback():
    """Issue 5: a broad, flat-top peak that Gaussian cannot fit well must
    still produce a position via Voigt, asymmetric, or centroid-of-top-N.
    """
    rng = np.random.default_rng(17)
    x = np.linspace(-10, 4, 71)
    # Wide peak with flat top from -2.6 to -2.2.
    y = np.zeros_like(x)
    for c in (-2.6, -2.4, -2.2):
        y += 4000.0 * np.exp(-((x - c) ** 2) / (2.0 * 1.0 ** 2))
    y += 500.0
    y = y + rng.normal(0, 80, x.size)
    fit = fit_peak(x, y)
    assert fit.detected, f"flat-top peak not detected: reason={fit.reason!r}"
    # Center of flat top is around -2.4
    assert abs(fit.position - (-2.4)) < 0.6, (
        f"position {fit.position} too far from expected -2.4 (method={fit.method})"
    )
    assert fit.peak_at_boundary is False
    # method should never be "none" for a detected peak.
    assert fit.method != "none"


def test_peak_pinned_to_scan_edge_flagged():
    """Issue 6: when the scan range misses the true peak and the "fit" pegs
    to the boundary, detected must be False and peak_at_boundary True.
    """
    rng = np.random.default_rng(19)
    # True peak at -2.4, scan starts at -2 (peak just outside left edge).
    x = np.linspace(-2.0, 8.0, 41)
    y = 500.0 + 12000.0 * np.exp(-((x - (-2.4)) ** 2) / (2.0 * 1.0 ** 2))
    y = y + rng.normal(0, 50, x.size)
    fit = fit_peak(x, y)
    assert fit.peak_at_boundary is True, (
        f"peak at edge not flagged: position={fit.position}, method={fit.method}"
    )
    assert not fit.detected


def test_peak_method_is_gaussian_for_clean_gaussian():
    """Tier order: a clean symmetric Gaussian should be fit with the
    Gaussian tier (cheapest, most common). Voigt and asymmetric exist as
    fallbacks only.
    """
    x = np.linspace(-5, 5, 41)
    y = _gaussian(x, bg=500.0, amp=12000.0, x0=0.5, sigma=1.0)
    fit = fit_peak(x, y)
    assert fit.method == "gaussian"


def test_signal_below_noise_returns_no_method():
    """When the prominence (max-median) doesn't clear k_noise * std, no
    fallback should produce a position — return method='none'.
    """
    rng = np.random.default_rng(23)
    x = np.linspace(-5, 5, 41)
    y = 500.0 + rng.normal(0, 200, x.size)
    fit = fit_peak(x, y)
    assert not fit.detected
    assert fit.method == "none"
    assert fit.position is None
