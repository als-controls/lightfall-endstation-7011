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
