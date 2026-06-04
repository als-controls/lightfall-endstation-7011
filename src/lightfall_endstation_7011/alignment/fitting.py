"""Pure fitting functions for reflection-alignment scans.

No I/O: these operate on numpy arrays and return dataclasses describing the
fit. The ``detected`` flag is the objective "is there a feature?" decision,
so the orchestrating agent never eyeballs raw data.

Two design rules guard against silent garbage:

  * When a parametric fit converges but its R^2 is below
    ``POSITION_R2_FLOOR``, the returned ``position`` is forced to ``None``
    even though the optimizer returned a numeric x0. Callers cannot
    accidentally move a motor to a meaningless coordinate.
  * Peak fits flag ``peak_at_boundary`` whenever the chosen position sits
    within one scan step of either endpoint or the response is monotonic
    across the scan. Both situations mean "widen the scan", not "move".
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import curve_fit
from scipy.special import erf, voigt_profile

# R^2 below which a converged fit's x0 is unreliable; wrappers null it out.
POSITION_R2_FLOOR = 0.5


# ---------------------------------------------------------------------------
# Falling-edge (knife-edge) fit — used for lift scans
# ---------------------------------------------------------------------------


@dataclass
class EdgeFit:
    """Result of a falling-edge (knife-edge) fit.

    position is the half-cut (50%-of-step) location. baseline is the high-side
    signal (small x), floor the low-side signal (large x). When the fit is
    unreliable (R^2 < POSITION_R2_FLOOR) ``position`` is None even if the
    optimizer converged.
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


def _safe_position(x0: float, r2: float) -> float | None:
    """Return x0 when the fit is trustworthy, else None.

    Used so a numerically-converged-but-low-R^2 fit cannot leak a number into
    the caller's "move motor here" path.
    """
    if not (np.isfinite(x0) and np.isfinite(r2)):
        return None
    if r2 < POSITION_R2_FLOOR:
        return None
    return float(x0)


def fit_falling_edge_halfcut(x, y, *, k_noise: float = 5.0, r2_min: float = 0.9) -> EdgeFit:
    """Fit a single falling error-function edge; return the half-cut position.

    detected is True only when the step height clears ``k_noise`` * residual
    noise, the fit R^2 >= ``r2_min``, and the edge sits inside the scan range.
    A rising edge yields a negative step height and is reported not-detected.
    When R^2 < POSITION_R2_FLOOR the returned ``position`` is None even though
    the optimizer converged.
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
    pos = _safe_position(x0, r2)

    if not (np.isfinite(r2) and np.isfinite(noise) and np.isfinite(x0) and np.isfinite(w)):
        return EdgeFit(False, pos, baseline, floor, abs(w), r2, "non-finite fit quality metrics")
    if step <= k_noise * noise:
        return EdgeFit(False, pos, baseline, floor, abs(w), r2, "step height below noise threshold (rising edge or no edge)")
    if abs(w) >= span:
        return EdgeFit(False, pos, baseline, floor, abs(w), r2, "fitted width exceeds scan span (no localized edge)")
    if r2 < r2_min:
        return EdgeFit(False, pos, baseline, floor, abs(w), r2, f"poor fit (R2={r2:.3f}); edge may be near/outside scan range or too noisy")
    if not (x.min() <= x0 <= x.max()):
        return EdgeFit(False, pos, baseline, floor, abs(w), r2, "edge outside scan range")
    return EdgeFit(True, float(x0), baseline, floor, abs(w), r2, "")


# ---------------------------------------------------------------------------
# Peak fit (rocking curve) — tiered: Gaussian -> Voigt -> asymmetric ->
#                          centroid-of-top-N -> argmax. The non-parametric
#                          tiers always produce a position; the caller checks
#                          ``method`` and ``peak_at_boundary`` to decide.
# ---------------------------------------------------------------------------


@dataclass
class PeakFit:
    """Result of a peak fit (rocking curve).

    ``method`` names the algorithm that produced ``position``:
      * ``"gaussian"``            — symmetric Gaussian, R^2 >= r2_min
      * ``"voigt"``               — Voigt (Gaussian + Lorentzian)
      * ``"asymmetric_gaussian"`` — split-sigma Gaussian (different sigma each side)
      * ``"centroid_topN"``       — centroid of the top 20% of points
      * ``"argmax"``              — last-resort fallback
      * ``"none"``                — no method produced a usable position

    ``peak_at_boundary`` is True if the chosen position sits within one scan
    step of either endpoint, or if the response is monotonic across the scan
    (no peak inside the range — widen and rescan instead). When this flag is
    True, ``detected`` is forced to False.
    """

    detected: bool
    position: float | None
    amplitude: float | None
    background: float | None
    sigma: float | None
    r2: float | None
    reason: str = ""
    method: str = "none"
    peak_at_boundary: bool = False


# --- shape models --------------------------------------------------------


def _gaussian(x, bg, amp, x0, sigma):
    return bg + amp * np.exp(-((x - x0) ** 2) / (2.0 * sigma ** 2))


def _asymmetric_gaussian(x, bg, amp, x0, sigma_l, sigma_r):
    """Split-sigma Gaussian: sigma_l for x<x0, sigma_r for x>=x0.

    Captures slightly skewed rocking curves where one side falls faster than
    the other (common with imperfect alignment of upstream optics).
    """
    s = np.where(x < x0, sigma_l, sigma_r)
    return bg + amp * np.exp(-((x - x0) ** 2) / (2.0 * s ** 2))


def _voigt(x, bg, amp, x0, sigma, gamma):
    """Voigt-shaped peak. ``amp`` is the height above background at x0.

    voigt_profile is normalized to unit area, so we divide by its peak height
    (value at zero) so ``amp`` maps directly to the visible peak amplitude.
    """
    peak = float(voigt_profile(0.0, sigma, gamma))
    if peak <= 0 or not np.isfinite(peak):
        return np.full_like(x, np.nan)
    return bg + amp * voigt_profile(x - x0, sigma, gamma) / peak


# --- support helpers -----------------------------------------------------


def _is_monotonic(y: np.ndarray, *, tol: float = 0.05) -> bool:
    """True if y is (nearly) monotonic across the scan — no peak inside."""
    if y.size < 3:
        return False
    diffs = np.diff(y)
    if diffs.size == 0:
        return False
    up = float(np.sum(diffs > 0)) / diffs.size
    return up >= (1.0 - tol) or up <= tol


def _at_boundary(position: float | None, x: np.ndarray) -> bool:
    """True if ``position`` sits within one scan step of either x endpoint."""
    if position is None or x.size < 2:
        return False
    step = (float(x.max()) - float(x.min())) / max(1, x.size - 1)
    return (
        (float(position) - float(x.min())) <= step
        or (float(x.max()) - float(position)) <= step
    )


def _centroid_top_n(x: np.ndarray, y: np.ndarray, *, n_frac: float = 0.2) -> float | None:
    """Centroid of the top-N points (default top 20%), weighted by y-baseline."""
    if x.size == 0:
        return None
    n = max(3, int(round(n_frac * x.size)))
    n = min(n, x.size)
    idx = np.argsort(y)[-n:]
    w = y[idx] - float(np.min(y))
    total = float(np.sum(w))
    if total <= 0.0 or not np.isfinite(total):
        return None
    return float(np.sum(x[idx] * w) / total)


# --- parametric tiers ----------------------------------------------------


def _try_gaussian(x, y, span, *, k_noise, r2_min):
    bg0 = float(np.median(y))
    imax = int(np.argmax(y))
    amp0 = float(y[imax] - bg0)
    x0_0 = float(x[imax])
    sigma0 = max(span / 6.0, 1e-6)
    try:
        popt, _ = curve_fit(_gaussian, x, y, p0=[bg0, amp0, x0_0, sigma0], maxfev=10000)
    except (RuntimeError, ValueError):
        return None
    bg, amp, x0, sigma = (float(v) for v in popt)
    resid = y - _gaussian(x, *popt)
    r2 = _r_squared(y, resid)
    noise = float(np.std(resid))
    if not all(np.isfinite(v) for v in (bg, amp, x0, sigma, r2, noise)):
        return None
    if amp <= k_noise * noise:
        return None
    if abs(sigma) >= span:
        return None
    if r2 < r2_min:
        return None
    if not (x.min() <= x0 <= x.max()):
        return None
    return {"method": "gaussian", "position": x0, "amplitude": amp,
            "background": bg, "sigma": abs(sigma), "r2": r2}


def _try_voigt(x, y, span, *, k_noise, r2_min):
    bg0 = float(np.median(y))
    imax = int(np.argmax(y))
    amp0 = max(float(y[imax] - bg0), 1e-9)
    x0_0 = float(x[imax])
    sigma0 = max(span / 6.0, 1e-6)
    gamma0 = max(span / 6.0, 1e-6)
    try:
        popt, _ = curve_fit(
            _voigt, x, y, p0=[bg0, amp0, x0_0, sigma0, gamma0],
            bounds=(
                [-np.inf, 0.0, float(x.min()), 1e-9, 1e-9],
                [np.inf, np.inf, float(x.max()), span, span],
            ),
            maxfev=10000,
        )
    except (RuntimeError, ValueError):
        return None
    bg, amp, x0, sigma, gamma = (float(v) for v in popt)
    resid = y - _voigt(x, *popt)
    r2 = _r_squared(y, resid)
    noise = float(np.std(resid))
    if not all(np.isfinite(v) for v in (bg, amp, x0, sigma, gamma, r2, noise)):
        return None
    width_proxy = max(abs(sigma), abs(gamma))
    if amp <= k_noise * noise:
        return None
    if width_proxy >= span:
        return None
    if r2 < r2_min:
        return None
    return {"method": "voigt", "position": x0, "amplitude": amp,
            "background": bg, "sigma": width_proxy, "r2": r2}


def _try_asymmetric_gaussian(x, y, span, *, k_noise, r2_min):
    bg0 = float(np.median(y))
    imax = int(np.argmax(y))
    amp0 = max(float(y[imax] - bg0), 1e-9)
    x0_0 = float(x[imax])
    sigma0 = max(span / 6.0, 1e-6)
    try:
        popt, _ = curve_fit(
            _asymmetric_gaussian, x, y,
            p0=[bg0, amp0, x0_0, sigma0, sigma0],
            bounds=(
                [-np.inf, 0.0, float(x.min()), 1e-9, 1e-9],
                [np.inf, np.inf, float(x.max()), span, span],
            ),
            maxfev=10000,
        )
    except (RuntimeError, ValueError):
        return None
    bg, amp, x0, sigma_l, sigma_r = (float(v) for v in popt)
    resid = y - _asymmetric_gaussian(x, *popt)
    r2 = _r_squared(y, resid)
    noise = float(np.std(resid))
    if not all(np.isfinite(v) for v in (bg, amp, x0, sigma_l, sigma_r, r2, noise)):
        return None
    width_proxy = 0.5 * (abs(sigma_l) + abs(sigma_r))
    if amp <= k_noise * noise:
        return None
    if width_proxy >= span:
        return None
    if r2 < r2_min:
        return None
    return {"method": "asymmetric_gaussian", "position": x0, "amplitude": amp,
            "background": bg, "sigma": width_proxy, "r2": r2}


# --- top-level entry point ----------------------------------------------


def fit_peak(x, y, *, k_noise: float = 5.0, r2_min: float = 0.9) -> PeakFit:
    """Fit a peak; return its center plus the method used.

    Tries in order: Gaussian, Voigt, asymmetric Gaussian, centroid-of-top-N,
    argmax. Each parametric tier must clear amplitude > ``k_noise`` * residual
    noise, fitted width < scan span, R^2 >= ``r2_min``, and center inside the
    scan; the first one that passes wins.

    Non-parametric fallbacks (centroid_topN, argmax) ALWAYS yield a position
    when the prominence (max - median) clears ``k_noise`` * y noise. The
    caller inspects ``method`` to know which produced the answer.

    ``detected`` is False whenever the chosen position sits within one scan
    step of either endpoint or the response is monotonic — that means "widen
    the scan, do not move".
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if x.size < 4:
        return PeakFit(False, None, None, None, None, None, "too few points", method="none")

    span = float(x.max() - x.min())
    if span <= 0:
        return PeakFit(False, None, None, None, None, None, "zero scan span", method="none")

    monotonic = _is_monotonic(y)
    y_noise = float(np.std(y))
    y_prominence = float(np.max(y) - np.median(y))
    has_signal_above_noise = y_noise == 0.0 or y_prominence > k_noise * y_noise

    # 1) Parametric tiers — first success (passes R^2 + width + noise gates) wins.
    for tryfn in (_try_gaussian, _try_voigt, _try_asymmetric_gaussian):
        r = tryfn(x, y, span, k_noise=k_noise, r2_min=r2_min)
        if r is None:
            continue
        boundary = _at_boundary(r["position"], x) or monotonic
        reason = (
            "peak at scan boundary or response is monotonic — widen scan"
            if boundary else ""
        )
        return PeakFit(
            detected=not boundary,
            position=r["position"],
            amplitude=r["amplitude"],
            background=r["background"],
            sigma=r["sigma"],
            r2=r["r2"],
            reason=reason,
            method=r["method"],
            peak_at_boundary=boundary,
        )

    # 2) If signal isn't above noise, no fallback can save us.
    if not has_signal_above_noise:
        return PeakFit(
            False, None, None, None, None, None,
            "amplitude below noise threshold (no peak)",
            method="none",
        )

    # 3) Monotonic with real signal — peak is OUTSIDE the scanned range.
    #    Report a sensible position so the operator sees the direction to
    #    widen, but detected=False and peak_at_boundary=True.
    if monotonic:
        argmax_pos = float(x[int(np.argmax(y))])
        return PeakFit(
            detected=False,
            position=argmax_pos,
            amplitude=y_prominence,
            background=float(np.median(y)),
            sigma=None,
            r2=None,
            reason="monotonic response — peak outside scan; widen the range",
            method="argmax",
            peak_at_boundary=True,
        )

    # 4) Centroid of top-N (good for flat-top peaks where parametric fails).
    cent = _centroid_top_n(x, y)
    if cent is not None and np.isfinite(cent):
        boundary = _at_boundary(cent, x)
        reason = (
            "parametric fits failed; centroid at scan boundary — widen scan"
            if boundary else "parametric fits failed; using centroid of top-N"
        )
        return PeakFit(
            detected=not boundary,
            position=cent,
            amplitude=y_prominence,
            background=float(np.median(y)),
            sigma=None,
            r2=None,
            reason=reason,
            method="centroid_topN",
            peak_at_boundary=boundary,
        )

    # 5) Last resort.
    pos = float(x[int(np.argmax(y))])
    boundary = _at_boundary(pos, x)
    return PeakFit(
        detected=not boundary,
        position=pos,
        amplitude=y_prominence,
        background=float(np.median(y)),
        sigma=None,
        r2=None,
        reason="all fits failed; using argmax (least trustworthy)",
        method="argmax",
        peak_at_boundary=boundary,
    )
