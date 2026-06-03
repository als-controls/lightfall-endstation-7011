# Reflection Alignment Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `reflection_alignment` AgentPlugin to `lightfall-endstation-7011` that drives reflection-geometry sample alignment (knife-edge lift + rocking-curve theta) by orchestrating existing Lightfall scan/data/move tools, with deterministic, unit-tested fitting and convergence logic.

**Architecture:** Approach C — numerical truth (edge/peak fitting, convergence) lives in pure, unit-tested modules; the AgentPlugin contributes a procedure prompt plus thin MCP tools (`check_beam`, `fit_lift_halfcut`, `fit_theta_peak`) that wrap those pure functions and the existing Tiled/DeviceCatalog access. Scans reuse the registry plan `rel_scan`.

**Tech Stack:** Python 3.11+, numpy, scipy (`curve_fit`, `erf`), bluesky/ophyd (via lightfall), pytest. Plugin base: `lightfall.plugins.agent_plugin.AgentPlugin`.

**Repo / branch:** `lightfall-endstation-7011`, branch `feature/reflection-alignment` (already created; spec committed there).

**Test interpreter:** the endstation package is installed editable into the ncs venv. Run all tests with:
```
C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest <args>
```
A bare `pytest` may resolve to a system interpreter lacking `lightfall`. In the commands below, `PY` = that interpreter path.

**Spec:** `docs/superpowers/specs/2026-05-26-reflection-alignment-design.md`

---

## File Structure

```
src/lightfall_endstation_7011/alignment/
  __init__.py          # package marker
  fitting.py           # EdgeFit, PeakFit, fit_falling_edge_halfcut, fit_peak  (pure)
  convergence.py       # ConvergenceTracker                                    (pure)
  skill.py             # ReflectionAlignmentAgent + module helpers + MCP tools
tests/alignment/
  __init__.py
  test_fitting.py
  test_convergence.py
  test_tools.py        # tests skill.py module-level helpers
  test_skill.py        # tests AgentPlugin surface
```
Plus modify: `pyproject.toml` (add scipy), `src/lightfall_endstation_7011/manifest.py` (register agent).

---

### Task 1: Add scipy dependency and create the alignment package skeleton

**Files:**
- Modify: `pyproject.toml:24-32` (dependencies list)
- Create: `src/lightfall_endstation_7011/alignment/__init__.py`
- Create: `tests/alignment/__init__.py`

- [ ] **Step 1: Add scipy to dependencies**

In `pyproject.toml`, add `"scipy>=1.10",` to the `dependencies` array (after `"numpy>=1.24",`):

```toml
dependencies = [
    "ophyd>=1.6.0",
    "bluesky>=1.8.0",
    "lightfall",  # For controller plugin base classes
    "lightfall-pipelines>=0.1.0",  # PipelinePlugin ABC + notebook helpers
    "numpy>=1.24",
    "scipy>=1.10",
    "scrapbook>=0.5",
    "tiled[client]>=0.2.3",
]
```

- [ ] **Step 2: Create the package markers**

`src/lightfall_endstation_7011/alignment/__init__.py`:
```python
"""Reflection-geometry sample alignment skill for the 7.0.1.1 endstation."""
```

`tests/alignment/__init__.py`:
```python
```
(empty file)

- [ ] **Step 3: Verify the package imports**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -c "import lightfall_endstation_7011.alignment; import scipy; print('ok', scipy.__version__)"`
Expected: prints `ok` and a scipy version >= 1.10.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml src/lightfall_endstation_7011/alignment/__init__.py tests/alignment/__init__.py
git commit -m "feat(alignment): add scipy dep and alignment package skeleton"
```

---

### Task 2: Falling-edge half-cut fit

**Files:**
- Create: `src/lightfall_endstation_7011/alignment/fitting.py`
- Test: `tests/alignment/test_fitting.py`

- [ ] **Step 1: Write the failing tests**

`tests/alignment/test_fitting.py`:
```python
"""Tests for the pure alignment fitting functions."""
from __future__ import annotations

import numpy as np
from scipy.special import erf

from lightfall_endstation_7011.alignment.fitting import fit_falling_edge_halfcut


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_fitting.py -v`
Expected: FAIL with `ImportError` / `cannot import name 'fit_falling_edge_halfcut'`.

- [ ] **Step 3: Implement fitting.py (edge portion)**

`src/lightfall_endstation_7011/alignment/fitting.py`:
```python
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

    if step <= k_noise * noise:
        return EdgeFit(False, x0, baseline, floor, abs(w), r2, "step height below noise threshold")
    if r2 < r2_min:
        return EdgeFit(False, x0, baseline, floor, abs(w), r2, f"poor fit (R2={r2:.3f})")
    if not (x.min() <= x0 <= x.max()):
        return EdgeFit(False, x0, baseline, floor, abs(w), r2, "edge outside scan range")
    return EdgeFit(True, x0, baseline, floor, abs(w), r2, "")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_fitting.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/alignment/fitting.py tests/alignment/test_fitting.py
git commit -m "feat(alignment): falling-edge half-cut fit"
```

---

### Task 3: Gaussian peak fit

**Files:**
- Modify: `src/lightfall_endstation_7011/alignment/fitting.py`
- Test: `tests/alignment/test_fitting.py` (append)

- [ ] **Step 1: Append the failing tests**

Add to `tests/alignment/test_fitting.py`:
```python
from lightfall_endstation_7011.alignment.fitting import fit_peak


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
```

- [ ] **Step 2: Run tests to verify the new ones fail**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_fitting.py -v`
Expected: the three peak tests FAIL with `cannot import name 'fit_peak'`.

- [ ] **Step 3: Append the peak implementation to fitting.py**

Add to `src/lightfall_endstation_7011/alignment/fitting.py`:
```python
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
    noise, R^2 >= ``r2_min``, and the center sits inside the scan range.
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

    if amp <= k_noise * noise:
        return PeakFit(False, x0, amp, bg, abs(sigma), r2, "amplitude below noise threshold")
    if r2 < r2_min:
        return PeakFit(False, x0, amp, bg, abs(sigma), r2, f"poor fit (R2={r2:.3f})")
    if not (x.min() <= x0 <= x.max()):
        return PeakFit(False, x0, amp, bg, abs(sigma), r2, "peak outside scan range")
    return PeakFit(True, x0, amp, bg, abs(sigma), r2, "")
```

- [ ] **Step 4: Run tests to verify all pass**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_fitting.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/alignment/fitting.py tests/alignment/test_fitting.py
git commit -m "feat(alignment): gaussian peak fit"
```

---

### Task 4: Convergence tracker

**Files:**
- Create: `src/lightfall_endstation_7011/alignment/convergence.py`
- Test: `tests/alignment/test_convergence.py`

- [ ] **Step 1: Write the failing tests**

`tests/alignment/test_convergence.py`:
```python
"""Tests for the convergence tracker."""
from __future__ import annotations

from lightfall_endstation_7011.alignment.convergence import ConvergenceTracker


def test_requires_three_cycles_to_converge_by_default():
    t = ConvergenceTracker()  # stable_required=2 => needs 3 cycles within tol
    t.record(0.0, 0.0)
    assert not t.converged
    t.record(3.0, 0.1)
    assert not t.converged  # only 2 cycles
    t.record(5.0, 0.2)
    assert t.converged  # 3 cycles; both pairwise comparisons within tol


def test_oscillation_in_lift_blocks_convergence():
    t = ConvergenceTracker()
    t.record(0.0, 0.0)
    t.record(50.0, 0.0)  # 50 um > 10 um tol
    t.record(0.0, 0.0)
    assert not t.converged


def test_theta_drift_blocks_convergence():
    t = ConvergenceTracker()
    t.record(0.0, 0.0)
    t.record(1.0, 1.0)   # 1.0 deg > 0.25 deg tol
    t.record(1.0, 1.0)
    assert not t.converged  # first pairwise comparison disagrees on theta


def test_stable_required_one_needs_two_cycles():
    t = ConvergenceTracker(stable_required=1)
    t.record(0.0, 0.0)
    assert not t.converged
    t.record(2.0, 0.1)
    assert t.converged


def test_history_is_exposed():
    t = ConvergenceTracker()
    t.record(1.0, 2.0)
    t.record(3.0, 4.0)
    assert t.history == [(1.0, 2.0), (3.0, 4.0)]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_convergence.py -v`
Expected: FAIL with `cannot import name 'ConvergenceTracker'`.

- [ ] **Step 3: Implement convergence.py**

`src/lightfall_endstation_7011/alignment/convergence.py`:
```python
"""Convergence tracking for the reflection-alignment refinement loop."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConvergenceTracker:
    """Track per-cycle (lift, theta) positions and report double convergence.

    Converged once the most recent ``stable_required`` consecutive
    cycle-to-cycle comparisons all agree within tolerance — i.e.
    ``stable_required + 1`` cycles whose lift and theta each stay within
    ``lift_tol`` / ``theta_tol`` of the previous cycle.
    """

    lift_tol: float = 10.0      # microns
    theta_tol: float = 0.25     # degrees
    stable_required: int = 2    # consecutive agreeing pairwise comparisons
    history: list[tuple[float, float]] = field(default_factory=list)

    def record(self, lift: float, theta: float) -> None:
        self.history.append((float(lift), float(theta)))

    def _agrees(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        return abs(a[0] - b[0]) <= self.lift_tol and abs(a[1] - b[1]) <= self.theta_tol

    @property
    def converged(self) -> bool:
        if len(self.history) < self.stable_required + 1:
            return False
        recent = self.history[-(self.stable_required + 1):]
        return all(self._agrees(recent[i], recent[i + 1]) for i in range(len(recent) - 1))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_convergence.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/alignment/convergence.py tests/alignment/test_convergence.py
git commit -m "feat(alignment): convergence tracker"
```

---

### Task 5: Beam-status helper

**Files:**
- Create: `src/lightfall_endstation_7011/alignment/skill.py`
- Test: `tests/alignment/test_tools.py`

- [ ] **Step 1: Write the failing tests**

`tests/alignment/test_tools.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_tools.py -v`
Expected: FAIL with `module 'lightfall_endstation_7011.alignment.skill' has no attribute '_beam_status'` (or ImportError if file absent).

- [ ] **Step 3: Create skill.py with constants and the beam helper**

`src/lightfall_endstation_7011/alignment/skill.py`:
```python
"""ReflectionAlignmentAgent: drive reflection-geometry sample alignment.

Numerical decisions live in lightfall_endstation_7011.alignment.fitting and
.convergence (pure, unit-tested). This module contributes the procedure
prompt and thin MCP tools that wrap those functions plus the existing
DeviceCatalog / Tiled access. Scans reuse the registry plan ``rel_scan``.
"""
from __future__ import annotations

from typing import Any

from lightfall.plugins.agent_plugin import AgentPlugin
from lightfall.utils.logging import logger

from lightfall_endstation_7011.alignment.fitting import fit_falling_edge_halfcut, fit_peak

DIODE_NAME = "DetectorDiodeCurrent"
LIFT_MOTOR = "sample_lift"
THETA_MOTOR = "sample_rotate_steppertheta"
BEAM_THRESHOLD_NA = 500.0


def _extract_scalar(reading: dict) -> float | None:
    """Pull the first numeric value out of an ophyd ``.read()`` mapping."""
    for _key, val in reading.items():
        if isinstance(val, dict) and "value" in val:
            v = val["value"]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    return None


def _beam_status(catalog, diode_name: str = DIODE_NAME, threshold_nA: float = BEAM_THRESHOLD_NA) -> dict:
    """Read the diode current (nA) and decide whether beam is present."""
    if not getattr(catalog, "is_connected", False):
        return {"success": False, "error": "device catalog not connected"}
    device = catalog.get_device_by_name(diode_name)
    if device is None or device.ophyd_device is None:
        return {"success": False, "error": f"diode '{diode_name}' not found or unconnected"}
    current = _extract_scalar(device.ophyd_device.read())
    if current is None:
        return {"success": False, "error": "could not read diode current"}
    return {
        "success": True,
        "current_nA": current,
        "beam_present": current >= threshold_nA,
        "threshold_nA": threshold_nA,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_tools.py -v`
Expected: all 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/alignment/skill.py tests/alignment/test_tools.py
git commit -m "feat(alignment): beam-status helper"
```

---

### Task 6: Scan-read and fit-from-uid helpers

**Files:**
- Modify: `src/lightfall_endstation_7011/alignment/skill.py`
- Test: `tests/alignment/test_tools.py` (append)

- [ ] **Step 1: Append the failing tests**

Add to `tests/alignment/test_tools.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_tools.py -v`
Expected: the three new tests FAIL with `module ... has no attribute '_fit_lift_from_uid'`.

- [ ] **Step 3: Append the read + fit helpers to skill.py**

Add to `src/lightfall_endstation_7011/alignment/skill.py`:
```python
def _read_scan_xy(uid: str, x_field: str | None = None, y_field: str | None = None):
    """Read (x, y) numpy arrays from a Bluesky run's primary stream via Tiled.

    y defaults to the diode column; x to the scanned motor column (the first
    non-diode, non-timestamp column). Raises RuntimeError on missing data.
    """
    import numpy as np
    from lightfall.services.tiled_service import TiledService
    from lightfall.utils.tiled_helpers import read_events

    service = TiledService.get_instance()
    if not service.is_connected or service._client is None:
        raise RuntimeError("Tiled service not connected")
    run = service._client[uid]
    if "primary" not in run:
        raise RuntimeError("run has no 'primary' stream")
    events = read_events(run["primary"])
    if events is None:
        raise RuntimeError("no readable data in primary stream")

    cols = [c for c in events.keys() if not str(c).startswith("ts_")]
    if not cols:
        raise RuntimeError("no data columns in primary stream")

    yf = y_field if (y_field and y_field in cols) else None
    if yf is None:
        match = [c for c in cols if "iode" in str(c)]
        yf = match[0] if match else (DIODE_NAME if DIODE_NAME in cols else cols[-1])
    if x_field and x_field in cols:
        xf = x_field
    else:
        xf = next((c for c in cols if c != yf), cols[0])

    x = np.asarray(events[xf], dtype=float)
    y = np.asarray(events[yf], dtype=float)
    return x, y


def _fit_lift_from_uid(uid: str, x_field: str | None = None, y_field: str | None = None) -> dict:
    x, y = _read_scan_xy(uid, x_field, y_field)
    fit = fit_falling_edge_halfcut(x, y)
    return {
        "detected": fit.detected,
        "halfcut": fit.position,
        "baseline": fit.baseline,
        "floor": fit.floor,
        "r2": fit.r2,
        "reason": fit.reason,
    }


def _fit_theta_from_uid(uid: str, x_field: str | None = None, y_field: str | None = None) -> dict:
    x, y = _read_scan_xy(uid, x_field, y_field)
    fit = fit_peak(x, y)
    return {
        "detected": fit.detected,
        "peak": fit.position,
        "amplitude": fit.amplitude,
        "background": fit.background,
        "r2": fit.r2,
        "reason": fit.reason,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_tools.py -v`
Expected: all 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/alignment/skill.py tests/alignment/test_tools.py
git commit -m "feat(alignment): tiled scan-read and fit-from-uid helpers"
```

---

### Task 7: ReflectionAlignmentAgent (prompt + MCP tools)

**Files:**
- Modify: `src/lightfall_endstation_7011/alignment/skill.py`
- Test: `tests/alignment/test_skill.py`

- [ ] **Step 1: Write the failing tests**

`tests/alignment/test_skill.py`:
```python
"""Smoke tests for the ReflectionAlignmentAgent skill surface."""
from __future__ import annotations

from lightfall_endstation_7011.alignment.skill import ReflectionAlignmentAgent


def test_metadata():
    agent = ReflectionAlignmentAgent()
    assert agent.name == "reflection_alignment"
    assert agent.category == "operations"
    assert agent.description.strip()


def test_system_prompt_covers_devices_and_rules():
    body = ReflectionAlignmentAgent().get_system_prompt()
    assert body.strip(), "system prompt must not be empty"
    for token in (
        "sample_lift",
        "sample_rotate_steppertheta",
        "DetectorDiodeCurrent",
        "500",          # beam threshold (nA)
        "half-cut",
        "rel_scan",
        "check_beam",
        "fit_lift_halfcut",
        "fit_theta_peak",
    ):
        assert token in body, f"prompt missing {token!r}"


def test_exposes_three_mcp_tools():
    """create_tools returns check_beam, fit_lift_halfcut, fit_theta_peak.

    Skipped when claude_agent_sdk is not installed (create_tools returns []),
    matching the production runtime's missing-SDK behavior.
    """
    tools = ReflectionAlignmentAgent().create_tools()
    if not tools:
        import pytest

        pytest.skip("claude_agent_sdk not available")
    names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools}
    assert names == {"check_beam", "fit_lift_halfcut", "fit_theta_peak"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_skill.py -v`
Expected: FAIL with `cannot import name 'ReflectionAlignmentAgent'`.

- [ ] **Step 3: Append the AgentPlugin class to skill.py**

Add to `src/lightfall_endstation_7011/alignment/skill.py`:
```python
class ReflectionAlignmentAgent(AgentPlugin):
    """Skill that drives reflection-geometry sample alignment.

    Teaches the embedded agent the knife-edge (lift) + rocking-curve (theta)
    procedure and contributes three MCP tools: check_beam, fit_lift_halfcut,
    fit_theta_peak. Scans, run polling, run-data display, and motor moves all
    reuse existing Lightfall acquisition tools.
    """

    @property
    def name(self) -> str:
        return "reflection_alignment"

    @property
    def display_name(self) -> str:
        return "Reflection Alignment"

    @property
    def description(self) -> str:
        return "Knife-edge + rocking-curve alignment of a sample in reflection geometry"

    @property
    def category(self) -> str:
        return "operations"

    @property
    def priority(self) -> int:
        return 30

    def get_system_prompt(self) -> str:
        return """\
## Reflection Alignment Skill

Use this skill when the user asks to align a sample in reflection geometry on
the 7.0.1.1 endstation, or mentions a reflection alignment, a knife-edge +
rocking-curve alignment, or aligning sample lift and theta against the
detector diode.

### Devices (resolve by these catalog names)
- Lift motor:  `sample_lift` (microns)
- Theta motor: `sample_rotate_steppertheta` (degrees)
- Diode:       `DetectorDiodeCurrent` (reads in nA, instantaneous)
- Video:       Blackfly Chamber Cam live view (manual centering only)

If a name is missing from the catalog (`ncs_list_devices`), ask the operator
which device to use before proceeding.

### Tools this skill provides
- `check_beam()` -> {current_nA, beam_present}. Beam present at >= 500 nA;
  ~15000 nA is healthy; below 500 nA means no usable beam.
- `fit_lift_halfcut(uid)` -> fits a falling edge to a lift scan; returns
  {detected, halfcut, ...}. When detected, move `sample_lift` to `halfcut`.
- `fit_theta_peak(uid)` -> fits a peak to a theta scan; returns
  {detected, peak, ...}. When detected, move `sample_rotate_steppertheta`
  to `peak`.

### Procedure
1. PRE-FLIGHT (manual — ask the operator and WAIT for confirmation):
   a. Confirm the sample is roughly centered at the beam using the Blackfly
      Chamber Cam live view.
   b. Confirm the diode sensitivity is set to 5 microA/V.
   Then move `sample_rotate_steppertheta` to 0 via `ncs_move_motor`.
2. BEAM GATE: call `check_beam`. If beam_present is false, STOP, tell the
   operator, and call `ncs_get_beam_status` for ring/shutter context.
   Re-run this check before every scan.
3. COARSE LIFT (run ONCE): `ncs_run_plan` plan_name "rel_scan" with
   detectors=["DetectorDiodeCurrent"], the motor "sample_lift", start -500,
   stop 500, num 21. Wait for the engine to go idle (`ncs_get_run_status`),
   get the uid (`ncs_get_last_run`), call `fit_lift_halfcut(uid)`. If
   detected, move `sample_lift` to halfcut. If NOT detected, STOP and hand
   back to the operator (optionally `ncs_show_run` to display the scan).
4. FINE LIFT: rel_scan on `sample_lift`, start -100, stop 100, num 21. Fit
   with `fit_lift_halfcut`; move to halfcut, or STOP if not detected.
5. THETA: rel_scan on `sample_rotate_steppertheta`, start -5, stop 5, num 41.
   Fit with `fit_theta_peak`; move theta to peak, or STOP if not detected.
6. Record this cycle's (lift, theta) positions. Repeat steps 4 then 5, but on
   every pass after the first fine lift tighten the lift scan to start -50,
   stop 50, num 21. Stop when both lift and theta change by no more than
   10 microns / 0.25 degrees across two consecutive cycles (three cycles all
   within tolerance). Cap the loop at 6 refinement cycles.
7. Report the final `sample_lift` and `sample_rotate_steppertheta` positions
   and the per-cycle history.

### Rules
- NEVER guess a half-cut or peak by eyeballing data — always use the fit
  tools; their `detected` flag is the decision.
- On any failed fit (detected=false) or failed beam gate, STOP and return
  control to the operator. Do not auto-widen the range or silently continue.
"""

    def create_tools(self) -> list[Any]:
        try:
            from claude_agent_sdk import tool
        except ImportError:
            logger.warning("claude_agent_sdk not available, reflection_alignment tools disabled")
            return []

        @tool(
            name="check_beam",
            description=(
                "Read the detector diode current (nA) and report whether beam is "
                "present (>= 500 nA). Call before each alignment scan."
            ),
            input_schema={"type": "object", "properties": {}},
        )
        async def check_beam(args: dict) -> dict[str, Any]:
            from lightfall.claude._internal.threading import run_on_main_thread
            from lightfall.devices import DeviceCatalog
            from lightfall.plugins.agents._mcp_helpers import mcp_result

            def _run():
                return mcp_result(_beam_status(DeviceCatalog.get_instance()))

            return run_on_main_thread(_run)

        @tool(
            name="fit_lift_halfcut",
            description=(
                "Fit a falling edge to a completed lift scan and return the half-cut "
                "position. Pass the run uid (from ncs_get_last_run). Returns "
                "{detected, halfcut, ...}; move sample_lift to halfcut only when detected."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Run uid of the lift scan."},
                    "x_field": {"type": "string", "description": "Motor column name (optional)."},
                    "y_field": {"type": "string", "description": "Diode column name (optional)."},
                },
                "required": ["uid"],
            },
        )
        async def fit_lift_halfcut(args: dict) -> dict[str, Any]:
            from lightfall.claude._internal.threading import run_on_main_thread
            from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result

            uid = args.get("uid")
            if not uid:
                return mcp_error("uid is required")

            def _run():
                try:
                    return mcp_result(
                        _fit_lift_from_uid(uid, args.get("x_field"), args.get("y_field"))
                    )
                except Exception as exc:  # noqa: BLE001 - surface any read/fit failure to the agent
                    return mcp_error(f"lift fit failed: {exc}")

            return run_on_main_thread(_run)

        @tool(
            name="fit_theta_peak",
            description=(
                "Fit a peak to a completed theta (rocking-curve) scan and return the peak "
                "position. Pass the run uid. Returns {detected, peak, ...}; move "
                "sample_rotate_steppertheta to peak only when detected."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Run uid of the theta scan."},
                    "x_field": {"type": "string", "description": "Motor column name (optional)."},
                    "y_field": {"type": "string", "description": "Diode column name (optional)."},
                },
                "required": ["uid"],
            },
        )
        async def fit_theta_peak(args: dict) -> dict[str, Any]:
            from lightfall.claude._internal.threading import run_on_main_thread
            from lightfall.plugins.agents._mcp_helpers import mcp_error, mcp_result

            uid = args.get("uid")
            if not uid:
                return mcp_error("uid is required")

            def _run():
                try:
                    return mcp_result(
                        _fit_theta_from_uid(uid, args.get("x_field"), args.get("y_field"))
                    )
                except Exception as exc:  # noqa: BLE001 - surface any read/fit failure to the agent
                    return mcp_error(f"theta fit failed: {exc}")

            return run_on_main_thread(_run)

        return [check_beam, fit_lift_halfcut, fit_theta_peak]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_skill.py -v`
Expected: `test_metadata` and `test_system_prompt_covers_devices_and_rules` PASS; `test_exposes_three_mcp_tools` PASSES if `claude_agent_sdk` is installed, otherwise SKIPS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/alignment/skill.py tests/alignment/test_skill.py
git commit -m "feat(alignment): ReflectionAlignmentAgent prompt and MCP tools"
```

---

### Task 8: Register the agent in the manifest

**Files:**
- Modify: `src/lightfall_endstation_7011/manifest.py:36-43`
- Test: `tests/alignment/test_skill.py` (append)

- [ ] **Step 1: Append the failing test**

Add to `tests/alignment/test_skill.py`:
```python
def test_manifest_registers_reflection_alignment():
    from lightfall_endstation_7011.manifest import manifest

    entry = next((p for p in manifest.plugins if p.name == "reflection_alignment"), None)
    assert entry is not None, "reflection_alignment not registered in manifest"
    assert entry.type_name == "agent"
    assert entry.import_path == (
        "lightfall_endstation_7011.alignment.skill:ReflectionAlignmentAgent"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/test_skill.py::test_manifest_registers_reflection_alignment -v`
Expected: FAIL with `AssertionError: reflection_alignment not registered in manifest`.

- [ ] **Step 3: Add the manifest entry**

In `src/lightfall_endstation_7011/manifest.py`, add a new `PluginEntry` to the `plugins=[...]` list (after the Blackfly entry, before the closing `]`):
```python
        # Agent plugin: reflection-geometry sample alignment skill
        PluginEntry(
            type_name="agent",
            name="reflection_alignment",
            import_path="lightfall_endstation_7011.alignment.skill:ReflectionAlignmentAgent",
            metadata={"priority": 30},
        ),
```
Also update the module docstring's plugin list to mention the alignment agent.

- [ ] **Step 4: Run the full alignment test suite**

Run: `C:/Users/rp/PycharmProjects/ncs/ncs/.venv/Scripts/python.exe -m pytest tests/alignment/ -v`
Expected: all tests PASS (the SDK tool test may SKIP).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/manifest.py tests/alignment/test_skill.py
git commit -m "feat(alignment): register reflection_alignment agent in manifest"
```

---

## Self-Review

- **Spec coverage:** §2 components → Tasks 2–8; §3 control flow → encoded in the Task 7 system prompt; §4 fitting math → Tasks 2–3; §5 convergence → Task 4; §6 beam gate → Task 5 + `check_beam` (Task 7), ring status is the separate core plan; §7 checkpoints + §8 error handling → Task 7 prompt rules + tool `mcp_error` paths; §9 testing → tests in each task; §Dependencies (scipy) → Task 1. **Deviation:** spec listed `references/procedure.md`; the procedure is inlined in `get_system_prompt` instead (DRY — single source, no file I/O). `get_references_dir` inherits the base `None`.
- **Placeholder scan:** none — every step has concrete code/commands.
- **Type consistency:** `EdgeFit.position`/`PeakFit.position` map to `halfcut`/`peak` keys in the fit-from-uid dicts; tool names (`check_beam`, `fit_lift_halfcut`, `fit_theta_peak`) match between Task 7 implementation and the Task 7 surface test; device-name constants (`sample_lift`, `sample_rotate_steppertheta`, `DetectorDiodeCurrent`) are consistent across prompt, helpers, and tests.

## Notes for the executor

- The convergence loop, scan sequencing, and human checkpoints live in the **agent prompt**, not in Python — there is no orchestration function to test. The testable surface is the pure fitting/convergence code and the tool helpers.
- `_read_scan_xy` does live Tiled I/O and is not unit-tested directly; it is covered indirectly by monkeypatching it in the fit-from-uid tests. Validate it against a real run during the manual UI check.
- Final manual check (no automated coverage): in a running Lightfall session with the 7.0.1.1 profile, enable the skill, confirm the three tools register under `mcp__reflection_alignment__*`, and dry-run the procedure against a sample.
