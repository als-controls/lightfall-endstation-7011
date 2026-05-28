"""ReflectionAlignmentAgent: drive reflection-geometry sample alignment.

Numerical decisions live in lucid_endstation_7011.alignment.fitting and
.convergence (pure, unit-tested). This module contributes the procedure
prompt and thin MCP tools that wrap those functions plus the existing
DeviceCatalog / Tiled access. Scans reuse the registry plan ``rel_scan_1d``
augmented with a per-step plan template that re-asserts coupled motors and
enforces a from-below approach with settle.
"""
from __future__ import annotations

from typing import Any

from lucid.plugins.agent_plugin import AgentPlugin
from lucid.utils.logging import logger

from lucid_endstation_7011.alignment.convergence import ConvergenceTracker
from lucid_endstation_7011.alignment.fitting import fit_falling_edge_halfcut, fit_peak

# Canonical device names. Every alignment scan MUST use DIODE_NAME as the
# only detector; the skill prompt enforces this and check_beam echoes it back.
DIODE_NAME = "DetectorDiodeCurrent"
LIFT_MOTOR = "sample_lift"
THETA_MOTOR = "sample_rotate_steppertheta"
# sample_lift is mechanically coupled to sample_translate — the per-step
# plan template re-asserts COUPLED_HOLD_MOTOR to its current value at every
# step of any lift scan. Keep this list in sync with the prompt.
COUPLED_HOLD_MOTOR = "sample_translate"
BEAM_THRESHOLD_NA = 500.0

# Columns that come from Bluesky's event document framing rather than a
# device's .read() value. Excluding them keeps the variance-based x-field
# picker from latching onto `time` (epoch seconds, var ~ scan_duration^2)
# when the actual motor column has a name that doesn't match the catalog
# name we asked for. See the comment on _select_xy_fields below.
_EVENT_METADATA_COLS = frozenset({"time", "seq_num", "uid"})


def _is_event_metadata_col(name: str) -> bool:
    """True if `name` is a Bluesky-framing column, not a device data key."""
    s = str(name)
    return s.startswith("ts_") or s in _EVENT_METADATA_COLS


def _extract_scalar(reading: dict) -> float | None:
    """Pull the first numeric value out of an ophyd ``.read()`` mapping."""
    for _key, val in reading.items():
        if isinstance(val, dict) and "value" in val:
            v = val["value"]
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
    return None


def _beam_status(catalog, diode_name: str = DIODE_NAME, threshold_nA: float = BEAM_THRESHOLD_NA) -> dict:
    """Read the diode current (nA) and decide whether beam is present.

    Returns the canonical detector name so callers configure their scans
    against the right device — see issue 1: in the original session a
    different diode was used for the scan than for the beam check.
    """
    if not getattr(catalog, "is_connected", False):
        return {
            "success": False,
            "error": "device catalog not connected",
            "detector": diode_name,
        }
    device = catalog.get_device_by_name(diode_name)
    if device is None or device.ophyd_device is None:
        return {
            "success": False,
            "error": f"diode '{diode_name}' not found or unconnected",
            "detector": diode_name,
        }
    current = _extract_scalar(device.ophyd_device.read())
    if current is None:
        return {
            "success": False,
            "error": "could not read diode current",
            "detector": diode_name,
        }
    return {
        "success": True,
        "detector": diode_name,
        "current_nA": current,
        "beam_present": current >= threshold_nA,
        "threshold_nA": threshold_nA,
    }


def _select_xy_fields(
    cols: list[str],
    x_field: str | None = None,
    y_field: str | None = None,
    x_data: dict | None = None,
) -> tuple[str, str]:
    """Choose the (x, y) column names for a scan.

    y defaults to the diode column (a column containing "iode", else
    ``DIODE_NAME``, else the last column). x prefers, in order:
      1. ``x_field`` if it is an exact match in ``cols``;
      2. a column in ``cols`` whose name contains ``x_field`` (shortest match
         wins — picks ``sample_lift`` over ``sample_lift_user_setpoint``);
      3. when ``x_data`` is provided, the non-y column with the largest
         variance (issue 4: a per-step plan that records both lift and theta
         must not silently pick theta when the scan moved lift);
      4. the first non-y column.

    Raises RuntimeError if there are no columns or if x and y cannot be
    resolved to two distinct columns.
    """
    if not cols:
        raise RuntimeError("no data columns in primary stream")
    yf = y_field if (y_field and y_field in cols) else None
    if yf is None:
        match = [c for c in cols if "iode" in str(c)]
        yf = match[0] if match else (DIODE_NAME if DIODE_NAME in cols else cols[-1])

    xf: str | None = None
    if x_field:
        if x_field in cols:
            xf = x_field
        else:
            subs = [c for c in cols if x_field in str(c) and c != yf]
            if subs:
                xf = sorted(subs, key=len)[0]

    if xf is None and x_data is not None:
        import numpy as _np

        # Exclude Bluesky event-framing columns from the variance pick. For
        # a 40-second theta scan `time` has var ~ 140 sec² while the motor
        # (10° span) has var ~ 8 deg² — pure variance would silently pick
        # `time` and the downstream fit would operate on epoch values.
        ranked: list[tuple[float, str]] = []
        for c in cols:
            if c == yf or _is_event_metadata_col(c):
                continue
            try:
                arr = _np.asarray(x_data[c], dtype=float)
            except (KeyError, TypeError, ValueError):
                continue
            if arr.size >= 2 and _np.all(_np.isfinite(arr)):
                ranked.append((float(_np.var(arr)), c))
        if ranked:
            ranked.sort(reverse=True)
            xf = ranked[0][1]

    if xf is None:
        # Last-resort: first non-y column that is NOT event framing. We
        # would rather raise than return `time` as the x axis.
        xf = next((c for c in cols if c != yf and not _is_event_metadata_col(c)), None)

    if xf is None or xf == yf:
        raise RuntimeError(
            "could not infer distinct x and y columns from the scan; "
            "pass x_field and y_field explicitly"
        )
    return xf, yf


def _read_scan_xy(uid: str, x_field: str | None = None, y_field: str | None = None):
    """Read (x, y) numpy arrays from a Bluesky run's primary stream via Tiled.

    y defaults to the diode column; x to the scanned motor column (the
    largest-variance non-diode column — see ``_select_xy_fields``). Raises
    RuntimeError on missing data.
    """
    import numpy as np
    from lucid.services.tiled_service import TiledService
    from lucid.utils.tiled_helpers import read_events

    service = TiledService.get_instance()
    if not service.is_connected or service._client is None:
        raise RuntimeError("Tiled service not connected")
    run = service._client[uid]
    if "primary" not in run:
        raise RuntimeError("run has no 'primary' stream")
    events = read_events(run["primary"])
    if events is None:
        raise RuntimeError("no readable data in primary stream")

    # Drop event-framing columns (ts_*, time, seq_num, uid) before column
    # selection. They were the source of the "peak at 1.7e9" plot bug:
    # `time` has higher variance than a 10° theta motor over a 40-s scan.
    cols = [c for c in events.keys() if not _is_event_metadata_col(c)]
    xf, yf = _select_xy_fields(cols, x_field, y_field, x_data=events)

    x = np.asarray(events[xf], dtype=float)
    y = np.asarray(events[yf], dtype=float)
    if x.size == 0 or y.size == 0:
        raise RuntimeError("primary stream has no data points")
    return x, y


def _fit_lift_from_uid(uid: str, x_field: str | None = None, y_field: str | None = None) -> dict:
    # Default the x column to the canonical lift motor. This protects against
    # per-step plans that record several motors (issue 4) — the auto-picker
    # would otherwise have to guess.
    x, y = _read_scan_xy(uid, x_field or LIFT_MOTOR, y_field or DIODE_NAME)
    fit = fit_falling_edge_halfcut(x, y)
    return {
        "detected": fit.detected,
        "halfcut": fit.position,
        "baseline": fit.baseline,
        "floor": fit.floor,
        "width": fit.width,
        "r2": fit.r2,
        "reason": fit.reason,
    }


def _fit_theta_from_uid(uid: str, x_field: str | None = None, y_field: str | None = None) -> dict:
    x, y = _read_scan_xy(uid, x_field or THETA_MOTOR, y_field or DIODE_NAME)
    fit = fit_peak(x, y)
    return {
        "detected": fit.detected,
        "peak": fit.position,
        "amplitude": fit.amplitude,
        "background": fit.background,
        "sigma": fit.sigma,
        "r2": fit.r2,
        "method": fit.method,
        "peak_at_boundary": fit.peak_at_boundary,
        "reason": fit.reason,
    }


def _convergence_status(
    cycles: list,
    lift_tol: float = 10.0,
    theta_tol: float = 0.25,
    stable_required: int = 2,
) -> dict:
    """Decide whether the alignment loop has converged given the per-cycle
    (lift, theta) history. Pure wrapper over ConvergenceTracker.

    Each cycle may be a mapping with "lift"/"theta" keys or a [lift, theta] pair.
    """
    tracker = ConvergenceTracker(
        lift_tol=lift_tol, theta_tol=theta_tol, stable_required=stable_required
    )
    for c in cycles:
        if isinstance(c, dict):
            tracker.record(c["lift"], c["theta"])
        else:
            tracker.record(c[0], c[1])
    return {
        "converged": tracker.converged,
        "num_cycles": len(tracker.history),
        "lift_tol": lift_tol,
        "theta_tol": theta_tol,
        "stable_required": stable_required,
        "history": [{"lift": lift, "theta": theta} for (lift, theta) in tracker.history],
    }


# ---------------------------------------------------------------------------
# Visualization helpers — simple matplotlib .show() plots for now.
# These create a figure, draw the artifacts, and call plt.show(block=False)
# so the tool returns immediately while the window stays open. Refactor to
# the VisualizationPanel later (see issue 10).
# ---------------------------------------------------------------------------


def _plot_alignment_scan_impl(
    uid: str,
    *,
    x_field: str | None = None,
    y_field: str | None = None,
    kind: str = "auto",
) -> dict:
    """Draw the diode-vs-motor scan with fit overlay and chosen marker.

    ``kind`` is "lift", "theta", or "auto" (infer from the motor column
    name). Returns a small dict with the fit summary; the matplotlib window
    is opened via ``plt.show(block=False)``.

    Backend handling: we deliberately don't call ``matplotlib.use(...)``
    here. LUCID sets the interactive backend (QtAgg via PySide6) at
    startup; tests pin Agg via ``matplotlib.use("Agg", force=True)`` before
    invoking this helper. Forcing a switch at call time would override
    the test setup and crash pyplot when no display is available.
    """
    import matplotlib.pyplot as plt
    import numpy as np

    x, y = _read_scan_xy(uid, x_field, y_field)
    motor_name = x_field
    if motor_name is None:
        # Heuristic: pick based on x range — lift scans cover hundreds of
        # microns, theta scans cover at most ~20 degrees.
        if kind == "lift" or (kind == "auto" and (np.ptp(x) > 50)):
            motor_name = LIFT_MOTOR
        else:
            motor_name = THETA_MOTOR

    if kind == "auto":
        kind = "lift" if motor_name == LIFT_MOTOR else "theta"

    fig, ax = plt.subplots(figsize=(7, 5))
    ax.plot(x, y, "o", color="#1f77b4", label="data")

    summary: dict[str, Any] = {"uid": uid, "kind": kind, "motor": motor_name}

    if kind == "lift":
        fit = fit_falling_edge_halfcut(x, y)
        summary.update(
            detected=fit.detected, position=fit.position, r2=fit.r2,
            reason=fit.reason,
        )
        if fit.detected or (fit.r2 is not None and fit.r2 >= 0.5):
            # Overlay the fit curve.
            from scipy.special import erf as _erf

            xs = np.linspace(float(x.min()), float(x.max()), 400)
            ys = fit.floor + (fit.baseline - fit.floor) * 0.5 * (
                1.0 - _erf((xs - fit.position) / (np.sqrt(2.0) * fit.width))
            )
            ax.plot(xs, ys, "-", color="#d62728", label="falling-edge fit")
        if fit.position is not None:
            ax.axvline(fit.position, color="#2ca02c", linestyle="--",
                       label=f"halfcut = {fit.position:.2f}")
        ax.set_xlabel(f"{motor_name} (μm)")
        ax.set_ylabel("DetectorDiodeCurrent (nA)")
        ax.set_title(f"Lift scan ({uid[:8]}…) — detected={fit.detected}")
    else:
        fit = fit_peak(x, y)
        summary.update(
            detected=fit.detected, position=fit.position, r2=fit.r2,
            method=fit.method, peak_at_boundary=fit.peak_at_boundary,
            reason=fit.reason,
        )
        # Overlay whichever shape the fitter used (if parametric).
        xs = np.linspace(float(x.min()), float(x.max()), 400)
        if fit.method == "gaussian" and fit.position is not None:
            from lucid_endstation_7011.alignment.fitting import _gaussian as _g

            ax.plot(xs, _g(xs, fit.background, fit.amplitude, fit.position, fit.sigma),
                    "-", color="#d62728", label="gaussian fit")
        elif fit.method == "voigt" and fit.position is not None:
            from lucid_endstation_7011.alignment.fitting import _voigt as _v

            # voigt has 5 params; we don't expose gamma in PeakFit. Skip the
            # overlay for voigt rather than re-fitting — the marker + raw
            # data are enough for visual confirmation.
            pass
        if fit.position is not None:
            color = "#2ca02c" if not fit.peak_at_boundary else "#ff7f0e"
            ax.axvline(fit.position, color=color, linestyle="--",
                       label=f"peak = {fit.position:.3f} ({fit.method})")
        ax.set_xlabel(f"{motor_name} (deg)")
        ax.set_ylabel("DetectorDiodeCurrent (nA)")
        title = f"Theta scan ({uid[:8]}…) — detected={fit.detected}"
        if fit.peak_at_boundary:
            title += " · BOUNDARY"
        ax.set_title(title)

    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    try:
        plt.show(block=False)
        plt.pause(0.001)  # let the GUI event loop pump once
    except Exception as exc:  # noqa: BLE001 - headless / no display
        logger.warning("plt.show failed (likely headless): {}", exc)

    return summary


def _plot_convergence_impl(cycles: list, *, lift_tol: float = 10.0, theta_tol: float = 0.25) -> dict:
    """Plot lift and theta vs cycle index with tolerance bands.

    See ``_plot_alignment_scan_impl`` for why we don't ``matplotlib.use``
    here — the backend is chosen by the host (LUCID or pytest).
    """
    import matplotlib.pyplot as plt

    pairs = []
    for c in cycles:
        if isinstance(c, dict):
            pairs.append((float(c["lift"]), float(c["theta"])))
        else:
            pairs.append((float(c[0]), float(c[1])))
    if not pairs:
        raise RuntimeError("cycles is empty — nothing to plot")

    idx = list(range(1, len(pairs) + 1))
    lifts = [p[0] for p in pairs]
    thetas = [p[1] for p in pairs]

    fig, (ax_l, ax_t) = plt.subplots(2, 1, figsize=(7, 6), sharex=True)
    ax_l.plot(idx, lifts, "o-", color="#1f77b4")
    ax_l.set_ylabel("sample_lift (μm)")
    ax_l.grid(True, alpha=0.3)
    ax_l.set_title(f"Alignment convergence ({len(pairs)} cycles)")
    if len(lifts) >= 2:
        last = lifts[-1]
        ax_l.axhspan(last - lift_tol, last + lift_tol, color="#2ca02c", alpha=0.15,
                     label=f"±{lift_tol:g} μm tolerance")
        ax_l.legend(loc="best")

    ax_t.plot(idx, thetas, "o-", color="#d62728")
    ax_t.set_ylabel("sample_rotate_steppertheta (deg)")
    ax_t.set_xlabel("cycle")
    ax_t.grid(True, alpha=0.3)
    if len(thetas) >= 2:
        last = thetas[-1]
        ax_t.axhspan(last - theta_tol, last + theta_tol, color="#2ca02c", alpha=0.15,
                     label=f"±{theta_tol:g} deg tolerance")
        ax_t.legend(loc="best")

    fig.tight_layout()
    try:
        plt.show(block=False)
        plt.pause(0.001)
    except Exception as exc:  # noqa: BLE001
        logger.warning("plt.show failed (likely headless): {}", exc)

    return {
        "num_cycles": len(pairs),
        "lift_tol": lift_tol,
        "theta_tol": theta_tol,
        "history": [{"lift": l, "theta": t} for (l, t) in pairs],
    }


class ReflectionAlignmentAgent(AgentPlugin):
    """Skill that drives reflection-geometry sample alignment.

    Teaches the embedded agent the knife-edge (lift) + rocking-curve (theta)
    procedure and contributes MCP tools for beam check, fits, convergence,
    quick reads, and plotting. Scans, run polling, run-data display, and
    motor moves all reuse existing LUCID acquisition tools.
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

### Canonical devices (use these names EXACTLY — no substitutes)
- Lift motor:    `sample_lift` (microns)
- Theta motor:   `sample_rotate_steppertheta` (degrees)
- Held axis:     `sample_translate` (microns) — mechanically coupled to
                 `sample_lift`; see "Coupled motors" below.
- Detector:      `DetectorDiodeCurrent` (nA, instantaneous). This is the
                 ONLY detector for every alignment scan. `check_beam` reads
                 this exact device and echoes its name in the response.
                 Never use a different diode (e.g. `DIAG112_Diode`) for the
                 scan even if it appears in the catalog.
- Video:         Blackfly Chamber Cam live view (manual centering only)

If a name is missing from the catalog (`ncs_list_devices`), ask the operator
which device to use before proceeding.

### Coupled motors — REQUIRED reading
`sample_lift` is mechanically coupled to `sample_translate`: large lift
moves drift translate by tens to hundreds of microns. A plain `bp.scan` or
`bp.rel_scan` over `sample_lift` will silently let `sample_translate` drift,
which corrupts every halfcut fit downstream (this happened in the original
run — see issue 2).

Use the per-step plan template under "Running scans" for every lift scan.
Theta scans use plain `rel_scan_1d` (no coupling).

### Tools this skill provides
- `check_beam()` -> {detector, current_nA, beam_present, threshold_nA}.
  Beam present at >= 500 nA; ~15000 nA is healthy; below 500 nA means no
  usable beam. Call before every alignment scan.
- `fit_lift_halfcut(uid)` -> fits a falling edge and returns the half-cut
  (50%-of-step) position. Returns {detected, halfcut, r2, reason, ...}.
  When detected=true AND r2 >= 0.9, move `sample_lift` to halfcut. When
  detected=false OR r2 < 0.5 the halfcut field is null — never move.
- `fit_theta_peak(uid)` -> {detected, peak, r2, method, peak_at_boundary,
  reason, ...}. method is "gaussian" | "voigt" | "asymmetric_gaussian" |
  "centroid_topN" | "argmax" | "none" — the algorithm that produced the
  position. If `peak_at_boundary` is true, the response is monotonic or the
  peak pegged to the scan edge; WIDEN the scan, do not move.
- `check_convergence(cycles)` -> {converged, ...}. Pass the full ordered
  list of per-cycle {lift, theta}; returns whether the loop has converged.
- `quick_read()` -> ad-hoc trigger-and-read of `DetectorDiodeCurrent`
  wrapped in a run. Use for sanity checks between scans (no need to compose
  a Bluesky plan yourself).
- `plot_alignment_scan(uid, kind="auto")` -> draw the diode-vs-motor curve
  with fit overlay and halfcut/peak marker. Call after every fit.
- `plot_convergence(cycles)` -> draw lift and theta vs cycle with
  tolerance bands. Call after each cycle to give the operator a visual
  status.

### Running scans

#### Theta scans (no coupling)
Use the registered relative 1D scan plan `rel_scan_1d` via `ncs_run_plan`.
Before the first scan call `ncs_list_plans` (category "scan") once to
confirm the exact plan name and its parameter names (`num` vs
`num_points`). Pass detectors=["DetectorDiodeCurrent"] explicitly.

#### Lift scans (REQUIRES per-step plan with held translate + settle)
Use `ncs_run_plan_code` with the template below. It explicitly:

1. Reads `sample_translate`'s current value at the start of the scan and
   re-asserts it at every step (item 2).
2. Approaches each lift setpoint from below (item 3 — combats backlash).
3. Waits `settle_s` after each move before reading the detector.

```python
import bluesky.plans as bp
import bluesky.plan_stubs as bps

settle_s = 1.5
start, stop, num = -100.0, 100.0, 21   # relative range
det = DetectorDiodeCurrent
lift = sample_lift
hold = sample_translate

# Snapshot the current positions before relative offsets are applied.
lift0 = (yield from bps.rd(lift))
hold0 = (yield from bps.rd(hold))

steps = [lift0 + start + i * (stop - start) / (num - 1) for i in range(num)]
# Sort ascending so each step approaches from below — eliminates backlash.
steps.sort()

@bpp.run_decorator(md={"plan_name": "rel_lift_scan_held",
                        "motors": ["sample_lift"],
                        "detectors": ["DetectorDiodeCurrent"],
                        "cycle_tag": "fine_lift_cycle1"})
def _inner():
    for target in steps:
        # Always go LOW first to clear backlash, then up to the target.
        yield from bps.mv(lift, target - 5.0)
        yield from bps.mv(lift, target, hold, hold0)
        yield from bps.sleep(settle_s)
        yield from bps.trigger_and_read([det, lift, hold])

yield from _inner()
```

NOTE on `md`: Bluesky reserves `scan_id` (int), `plan_name` (str), `time`,
and `uid` — don't pass `scan_id` as a string. Use non-reserved keys like
`cycle_tag` or `note` for human-readable labels.

After submitting, call `ncs_wait_for_idle(include_last_run=true)`. If it
returns `last_run=null` or `status="plan_never_started"`, the plan failed
before opening a run document — inspect the engine logs, do NOT proceed to
fit.

### Procedure

1. PRE-FLIGHT (manual — ask the operator and WAIT for confirmation):
   a. Confirm the sample is roughly centered at the beam using the Blackfly
      Chamber Cam live view.
   b. Confirm the diode sensitivity is set to 5 microA/V.
   Then move `sample_rotate_steppertheta` to 0 via `ncs_move_motor`.

2. BEAM GATE: call `check_beam`. If `beam_present` is false, STOP, tell the
   operator, and call `ncs_get_beam_status` for ring/shutter context.
   Re-run this check before every scan. The `detector` field in the
   response is the canonical detector for every scan that follows.

3. COARSE LIFT (run ONCE): per-step lift plan with start=-500, stop=500,
   num=21, settle_s=1.5. Get uid (`ncs_wait_for_idle` → last_run.uid).
   Call `fit_lift_halfcut(uid)` then `plot_alignment_scan(uid, "lift")`.
   If detected, move `sample_lift` to halfcut. If NOT detected, STOP and
   hand back to the operator.

4. FINE LIFT: per-step lift plan with start=-100, stop=100, num=21,
   settle_s=1.5. Fit, plot, then move to halfcut, or STOP if not detected.

5. THETA: `rel_scan_1d` on `sample_rotate_steppertheta`, start=-5, stop=5,
   num=41, detectors=["DetectorDiodeCurrent"]. Fit with `fit_theta_peak`,
   plot. If `peak_at_boundary` is true OR detected is false, WIDEN the scan
   (e.g. start=-10, stop=10) and retry once — do not auto-widen more than
   once. Otherwise move theta to peak.

6. RECORD this cycle's (lift, theta) positions. After each cycle:
     ```
     cycles.append({"lift": lift_now, "theta": theta_now})
     status = check_convergence(cycles)
     plot_convergence(cycles)
     if status["converged"]:
         break
     ```
   Otherwise repeat steps 4 then 5, but on every pass after the first fine
   lift tighten the lift scan to start=-50, stop=50, num=21. Cap the loop
   at 6 refinement cycles.

7. Report the final `sample_lift` and `sample_rotate_steppertheta`
   positions and the per-cycle history (plus the convergence plot).

### Rules
- NEVER guess a half-cut or peak by eyeballing data — always use the fit
  tools; their `detected` flag is the decision, and `peak_at_boundary` /
  `r2` are veto flags.
- A null `halfcut` or null `peak` field means the fit was unreliable
  (R² below 0.5). Never move when the field is null, no matter what
  `position` looked like in earlier logs.
- On a failed beam gate or a failed fit, STOP and return control to the
  operator. Do not auto-widen the range or silently continue.
- Every lift scan MUST use the per-step plan with `sample_translate`
  held — plain `rel_scan_1d` on `sample_lift` is forbidden.
- Use `check_convergence` for the stop decision; do not judge convergence
  by eye.
- Use `plot_alignment_scan` and `plot_convergence` after every fit and
  every cycle so the operator can see what is happening.
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
                "Read the canonical detector diode current (nA) and report whether beam "
                "is present (>= 500 nA). Returns {detector, current_nA, beam_present, "
                "threshold_nA}; the `detector` field is the EXACT name of the device "
                "every alignment scan must use (DetectorDiodeCurrent). Call before each "
                "alignment scan."
            ),
            input_schema={"type": "object", "properties": {}},
        )
        async def check_beam(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread
            from lucid.devices import DeviceCatalog
            from lucid.plugins.agents._mcp_helpers import mcp_result

            def _run():
                from lucid.plugins.agents._mcp_helpers import mcp_error

                try:
                    return mcp_result(_beam_status(DeviceCatalog.get_instance()))
                except Exception as exc:  # noqa: BLE001 - surface catalog/read failure to the agent
                    return mcp_error(f"beam check failed: {exc}")

            return run_on_main_thread(_run)

        @tool(
            name="fit_lift_halfcut",
            description=(
                "Fit a falling edge to a completed lift scan and return the half-cut "
                "position. Pass the run uid (from ncs_get_last_run / ncs_wait_for_idle). "
                "Returns {detected, halfcut, baseline, floor, width, r2, reason}. If "
                "x_field is omitted the tool defaults to the sample_lift column (auto-"
                "picks among multiple recorded motors). halfcut is null when R² < 0.5 — "
                "do NOT move the motor in that case even if a number was previously logged."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Run uid of the lift scan."},
                    "x_field": {"type": "string", "description": "Motor column name (defaults to sample_lift)."},
                    "y_field": {"type": "string", "description": "Diode column name (defaults to DetectorDiodeCurrent)."},
                },
                "required": ["uid"],
            },
        )
        async def fit_lift_halfcut(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread
            from lucid.plugins.agents._mcp_helpers import mcp_error, mcp_result

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
                "position. Pass the run uid. Returns {detected, peak, amplitude, "
                "background, sigma, r2, method, peak_at_boundary, reason}. method is one "
                "of gaussian | voigt | asymmetric_gaussian | centroid_topN | argmax | "
                "none. peak_at_boundary=true means the response is monotonic or the peak "
                "is pinned to the scan edge — WIDEN the scan, do not move."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Run uid of the theta scan."},
                    "x_field": {"type": "string", "description": "Motor column name (defaults to sample_rotate_steppertheta)."},
                    "y_field": {"type": "string", "description": "Diode column name (defaults to DetectorDiodeCurrent)."},
                },
                "required": ["uid"],
            },
        )
        async def fit_theta_peak(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread
            from lucid.plugins.agents._mcp_helpers import mcp_error, mcp_result

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

        @tool(
            name="check_convergence",
            description=(
                "Decide whether the alignment loop has converged. Pass `cycles` = the "
                "ordered list of per-cycle results, each {\"lift\": microns, \"theta\": "
                "degrees}. Returns {converged, num_cycles, ...}. Converged means lift and "
                "theta each changed by <= 10 microns / 0.25 degrees across two consecutive "
                "cycles (three cycles within tolerance). Use this for the stop decision; do "
                "not judge convergence by eye."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "cycles": {
                        "type": "array",
                        "description": "Ordered per-cycle results, each {lift, theta}.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "lift": {"type": "number"},
                                "theta": {"type": "number"},
                            },
                            "required": ["lift", "theta"],
                        },
                    },
                    "lift_tol": {"type": "number", "description": "Lift tolerance, microns.", "default": 10.0},
                    "theta_tol": {"type": "number", "description": "Theta tolerance, degrees.", "default": 0.25},
                    "stable_required": {"type": "integer", "description": "Consecutive agreeing comparisons.", "default": 2},
                },
                "required": ["cycles"],
            },
        )
        async def check_convergence(args: dict) -> dict[str, Any]:
            from lucid.plugins.agents._mcp_helpers import mcp_error, mcp_result

            cycles = args.get("cycles")
            if not cycles:
                return mcp_error("cycles is required: the per-cycle (lift, theta) history")
            try:
                return mcp_result(
                    _convergence_status(
                        cycles,
                        float(args.get("lift_tol", 10.0)),
                        float(args.get("theta_tol", 0.25)),
                        int(args.get("stable_required", 2)),
                    )
                )
            except (KeyError, TypeError, ValueError, IndexError) as exc:
                return mcp_error(f"convergence check failed: {exc}")

        @tool(
            name="quick_read",
            description=(
                "Ad-hoc single trigger-and-read of DetectorDiodeCurrent (and optionally "
                "other devices), wrapped in a proper Bluesky run. Use this for sanity "
                "checks between scans — e.g. after a motor move, verify the diode "
                "signal — instead of composing a bps.trigger_and_read plan yourself "
                "(which would raise IllegalMessageSequence outside a run, issue 8). "
                "Returns the run uid; read the data via ncs_get_run_data or "
                "ncs_show_run."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "devices": {
                        "type": "array",
                        "description": "Extra device names to read alongside the diode (motors, etc).",
                        "items": {"type": "string"},
                        "default": [],
                    },
                    "settle_s": {
                        "type": "number",
                        "description": "Sleep before the read (seconds). Default 0.5.",
                        "default": 0.5,
                    },
                    "note": {
                        "type": "string",
                        "description": "Free-text label written to run metadata as `note`.",
                    },
                },
            },
        )
        async def quick_read(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread
            from lucid.plugins.agents._mcp_helpers import mcp_error, mcp_result

            extra = list(args.get("devices") or [])
            settle_s = float(args.get("settle_s", 0.5))
            note = str(args.get("note") or "quick_read")

            def _run():
                try:
                    from lucid.acquire.engine import get_engine
                    import bluesky.plan_stubs as bps
                    import bluesky.preprocessors as bpp
                    from lucid.devices import DeviceCatalog

                    cat = DeviceCatalog.get_instance()
                    if not getattr(cat, "is_connected", False):
                        return mcp_error("device catalog not connected")

                    diode = cat.get_device_by_name(DIODE_NAME)
                    if diode is None or diode.ophyd_device is None:
                        return mcp_error(f"diode '{DIODE_NAME}' not available")

                    objs = [diode.ophyd_device]
                    missing: list[str] = []
                    for dn in extra:
                        d = cat.get_device_by_name(dn)
                        if d is None or d.ophyd_device is None:
                            missing.append(dn)
                        else:
                            objs.append(d.ophyd_device)
                    if missing:
                        return mcp_error(f"devices not available: {missing}")

                    md = {"plan_name": "quick_read", "note": note,
                          "detectors": [DIODE_NAME]}

                    @bpp.run_decorator(md=md)
                    def _plan():
                        if settle_s > 0:
                            yield from bps.sleep(settle_s)
                        yield from bps.trigger_and_read(objs)

                    engine = get_engine()
                    proc_id = engine.submit(_plan(), name="quick_read", skip_pre_submit=True)
                    return mcp_result({
                        "success": True,
                        "procedure_id": proc_id,
                        "detector": DIODE_NAME,
                        "extra_devices": extra,
                        "note": note,
                        "engine_state": engine.state_name,
                        "next": "call ncs_wait_for_idle to receive the uid",
                    })
                except Exception as exc:  # noqa: BLE001
                    return mcp_error(f"quick_read failed: {exc}")

            return run_on_main_thread(_run)

        @tool(
            name="plot_alignment_scan",
            description=(
                "Open an interactive matplotlib window showing the diode-vs-motor scan "
                "for the given run uid, with the fit overlay and chosen halfcut/peak "
                "marker. `kind` selects \"lift\" (falling-edge overlay), \"theta\" "
                "(peak overlay), or \"auto\" (infer from the motor's x range). Returns "
                "the fit summary so the agent can react in the same turn."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "uid": {"type": "string", "description": "Run uid of the scan to plot."},
                    "kind": {
                        "type": "string",
                        "enum": ["lift", "theta", "auto"],
                        "default": "auto",
                    },
                    "x_field": {"type": "string", "description": "Motor column (optional)."},
                    "y_field": {"type": "string", "description": "Diode column (optional)."},
                },
                "required": ["uid"],
            },
        )
        async def plot_alignment_scan(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread
            from lucid.plugins.agents._mcp_helpers import mcp_error, mcp_result

            uid = args.get("uid")
            if not uid:
                return mcp_error("uid is required")
            kind = str(args.get("kind") or "auto")

            def _run():
                try:
                    return mcp_result(
                        _plot_alignment_scan_impl(
                            uid,
                            x_field=args.get("x_field"),
                            y_field=args.get("y_field"),
                            kind=kind,
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    return mcp_error(f"plot_alignment_scan failed: {exc}")

            # Matplotlib + Qt windows must be created on the GUI thread.
            return run_on_main_thread(_run)

        @tool(
            name="plot_convergence",
            description=(
                "Open an interactive matplotlib window showing lift and theta vs cycle "
                "with tolerance bands around the most recent value. Pass the same "
                "`cycles` list you give to check_convergence. Use after each cycle so "
                "the operator can see whether the loop is closing in or oscillating."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "cycles": {
                        "type": "array",
                        "description": "Ordered per-cycle {lift, theta}.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "lift": {"type": "number"},
                                "theta": {"type": "number"},
                            },
                            "required": ["lift", "theta"],
                        },
                    },
                    "lift_tol": {"type": "number", "default": 10.0},
                    "theta_tol": {"type": "number", "default": 0.25},
                },
                "required": ["cycles"],
            },
        )
        async def plot_convergence(args: dict) -> dict[str, Any]:
            from lucid.claude._internal.threading import run_on_main_thread
            from lucid.plugins.agents._mcp_helpers import mcp_error, mcp_result

            cycles = args.get("cycles")
            if not cycles:
                return mcp_error("cycles is required")
            lift_tol = float(args.get("lift_tol", 10.0))
            theta_tol = float(args.get("theta_tol", 0.25))

            def _run():
                try:
                    return mcp_result(
                        _plot_convergence_impl(cycles, lift_tol=lift_tol, theta_tol=theta_tol)
                    )
                except Exception as exc:  # noqa: BLE001
                    return mcp_error(f"plot_convergence failed: {exc}")

            return run_on_main_thread(_run)

        return [
            check_beam,
            fit_lift_halfcut,
            fit_theta_peak,
            check_convergence,
            quick_read,
            plot_alignment_scan,
            plot_convergence,
        ]
