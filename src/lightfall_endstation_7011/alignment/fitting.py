"""Pure fitting functions for reflection-alignment scans.

No I/O: these operate on numpy-compatible arrays and return dataclasses
describing the fit. The ``detected`` flag is the objective "is there a
feature?" decision, so the orchestrating agent never eyeballs raw data.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit
from scipy.special import erf


@dataclass
class EdgeFit:
    """Result of a falling-edge (knife-edge) fit.

    position is the half-cut (50%-of-step) location. baseline is the high-side
    signal (small x), floor the low-side signal (large x).
    """

    detected: bool
    position: float | None
    baseline: float | None
    floor: float | None
    width: float | None
    r2: float | None
    reason: str = ""


def _falling_edge(x, floor, baseline, x0, w):
    return floor + (baseline - floor) * 0.5 * (1.0 - erf((x - x0) / (np.sqrt(2.0) * w)))


def _r_squared(y: np.ndarray, resid: np.ndarray) -> float:
    ss_res = float(np.sum(resid ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0


def fit_falling_edge_halfcut(x, y, *, k_noise: float = 5.0, r2_min: float = 0.9) -> EdgeFit:
    """Fit a single falling error-function edge; return the half-cut position.

    detected is True only when the step height clears ``k_noise`` * residual
    noise, the fit R^2 >= ``r2_min``, and the edge sits inside the scan range.
    A rising edge yields a negative step height and is reported not-detected.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 4:
        return EdgeFit(False, None, None, None, None, None, "too few points")

    quarter = max(1, x.size // 4)
    baseline0 = float(np.median(y[:quarter]))   # high side (small x)
    floor0 = float(np.median(y[-quarter:]))     # low side (large x)
    grad = np.gradient(y, x)
    x0_0 = float(x[int(np.argmin(grad))])
    span = float(x.max() - x.min())
    w0 = max(span / 10.0, 1e-6)

    try:
        popt, _ = curve_fit(
            _falling_edge, x, y, p0=[floor0, baseline0, x0_0, w0], maxfev=10000
        )
    except (RuntimeError, ValueError) as exc:
        return EdgeFit(False, None, None, None, None, None, f"fit did not converge: {exc}")

    floor, baseline, x0, w = (float(v) for v in popt)
    resid = y - _falling_edge(x, *popt)
    r2 = _r_squared(y, resid)
    noise = float(np.std(resid))
    step = baseline - floor

    if not (np.isfinite(r2) and np.isfinite(noise) and np.isfinite(x0) and np.isfinite(w)):
        return EdgeFit(False, x0, baseline, floor, abs(w), r2, "non-finite fit quality metrics")
    if step <= k_noise * noise:
        return EdgeFit(False, x0, baseline, floor, abs(w), r2, "step height below noise threshold (rising edge or no edge)")
    if abs(w) >= span:
        return EdgeFit(False, x0, baseline, floor, abs(w), r2, "fitted width exceeds scan span (no localized edge)")
    if r2 < r2_min:
        return EdgeFit(False, x0, baseline, floor, abs(w), r2, f"poor fit (R2={r2:.3f}); edge may be near/outside scan range or too noisy")
    if not (x.min() <= x0 <= x.max()):
        return EdgeFit(False, x0, baseline, floor, abs(w), r2, "edge outside scan range")
    return EdgeFit(True, x0, baseline, floor, abs(w), r2, "")


# ---------------------------------------------------------------------------
# Gaussian peak fit (rocking curve)
# ---------------------------------------------------------------------------


@dataclass
class PeakFit:
    """Result of a Gaussian peak fit (rocking curve)."""

    detected: bool
    position: float | None
    amplitude: float | None
    background: float | None
    sigma: float | None
    r2: float | None
    reason: str = ""


def _gaussian(x, bg, amp, x0, sigma):
    return bg + amp * np.exp(-((x - x0) ** 2) / (2.0 * sigma ** 2))


def fit_peak(x, y, *, k_noise: float = 5.0, r2_min: float = 0.9) -> PeakFit:
    """Fit a Gaussian peak; return the peak center.

    detected is True only when the amplitude clears ``k_noise`` * residual
    noise, the fitted width is smaller than the scan span (a localized peak),
    R^2 >= ``r2_min``, and the center sits inside the scan range.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 4:
        return PeakFit(False, None, None, None, None, None, "too few points")

    bg0 = float(np.median(y))
    imax = int(np.argmax(y))
    x0_0 = float(x[imax])
    amp0 = float(y[imax] - bg0)
    span = float(x.max() - x.min())
    sigma0 = max(span / 6.0, 1e-6)

    try:
        popt, _ = curve_fit(
            _gaussian, x, y, p0=[bg0, amp0, x0_0, sigma0], maxfev=10000
        )
    except (RuntimeError, ValueError) as exc:
        return PeakFit(False, None, None, None, None, None, f"fit did not converge: {exc}")

    bg, amp, x0, sigma = (float(v) for v in popt)
    resid = y - _gaussian(x, *popt)
    r2 = _r_squared(y, resid)
    noise = float(np.std(resid))

    if not (np.isfinite(r2) and np.isfinite(noise) and np.isfinite(x0) and np.isfinite(sigma)):
        return PeakFit(False, x0, amp, bg, abs(sigma), r2, "non-finite fit quality metrics")
    if amp <= k_noise * noise:
        return PeakFit(False, x0, amp, bg, abs(sigma), r2, "amplitude below noise threshold")
    if abs(sigma) >= span:
        return PeakFit(False, x0, amp, bg, abs(sigma), r2, "fitted width exceeds scan span (no localized peak)")
    if r2 < r2_min:
        return PeakFit(False, x0, amp, bg, abs(sigma), r2, f"poor fit (R2={r2:.3f}); peak may be near/outside scan range or too noisy")
    if not (x.min() <= x0 <= x.max()):
        return PeakFit(False, x0, amp, bg, abs(sigma), r2, "peak outside scan range")
    return PeakFit(True, x0, amp, bg, abs(sigma), r2, "")
