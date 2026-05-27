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
