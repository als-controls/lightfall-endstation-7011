"""ReflectionAlignmentAgent: drive reflection-geometry sample alignment.

Numerical decisions live in lucid_endstation_7011.alignment.fitting and
.convergence (pure, unit-tested). This module contributes the procedure
prompt and thin MCP tools that wrap those functions plus the existing
DeviceCatalog / Tiled access. Scans reuse the registry plan ``rel_scan``.
"""
from __future__ import annotations

from typing import Any

from lucid.plugins.agent_plugin import AgentPlugin
from lucid.utils.logging import logger

from lucid_endstation_7011.alignment.fitting import fit_falling_edge_halfcut, fit_peak

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


def _select_xy_fields(
    cols: list[str], x_field: str | None = None, y_field: str | None = None
) -> tuple[str, str]:
    """Choose the (x, y) column names for a scan.

    y defaults to the diode column (a column containing "iode", else
    ``DIODE_NAME``, else the last column); x defaults to the first column that
    is not y. Raises RuntimeError if there are no columns or if x and y cannot
    be resolved to two distinct columns (pass x_field/y_field explicitly then).
    """
    if not cols:
        raise RuntimeError("no data columns in primary stream")
    yf = y_field if (y_field and y_field in cols) else None
    if yf is None:
        match = [c for c in cols if "iode" in str(c)]
        yf = match[0] if match else (DIODE_NAME if DIODE_NAME in cols else cols[-1])
    if x_field and x_field in cols:
        xf = x_field
    else:
        xf = next((c for c in cols if c != yf), None)
    if xf is None or xf == yf:
        raise RuntimeError(
            "could not infer distinct x and y columns from the scan; "
            "pass x_field and y_field explicitly"
        )
    return xf, yf


def _read_scan_xy(uid: str, x_field: str | None = None, y_field: str | None = None):
    """Read (x, y) numpy arrays from a Bluesky run's primary stream via Tiled.

    y defaults to the diode column; x to the scanned motor column (the first
    non-diode, non-timestamp column). Raises RuntimeError on missing data.
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

    cols = [c for c in events.keys() if not str(c).startswith("ts_")]
    xf, yf = _select_xy_fields(cols, x_field, y_field)

    x = np.asarray(events[xf], dtype=float)
    y = np.asarray(events[yf], dtype=float)
    if x.size == 0 or y.size == 0:
        raise RuntimeError("primary stream has no data points")
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


class ReflectionAlignmentAgent(AgentPlugin):
    """Skill that drives reflection-geometry sample alignment.

    Teaches the embedded agent the knife-edge (lift) + rocking-curve (theta)
    procedure and contributes three MCP tools: check_beam, fit_lift_halfcut,
    fit_theta_peak. Scans, run polling, run-data display, and motor moves all
    reuse existing LUCID acquisition tools.
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
1. PRE-FLIGHT (manual - ask the operator and WAIT for confirmation):
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
- NEVER guess a half-cut or peak by eyeballing data - always use the fit
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
            from lucid.claude._internal.threading import run_on_main_thread
            from lucid.devices import DeviceCatalog
            from lucid.plugins.agents._mcp_helpers import mcp_result

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

        return [check_beam, fit_lift_halfcut, fit_theta_peak]
