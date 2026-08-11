"""Proactive XPCS speckle/dynamics monitor feed.

Judges xpcs_live's already-computed g2 (read via the lightfall monitor
scheduler's derived("xpcs") provider) — NO correlation, NO fitting. Emits a
warning when there is no coherent speckle, and reports whether the dynamics
have been captured. No-ops on non-XPCS runs (provider returns None)."""

from __future__ import annotations

import math

from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.feed import MonitorFeed
from lightfall.monitor.models import ExperimentContext, Observation
from lightfall.monitor.monitor_plugin import MonitorPlugin

FEED_NAME = "xpcs_dynamics"


def _as_floats(curve) -> list[float]:
    try:
        return [float(v) for v in curve]
    except (TypeError, ValueError):
        return []


class XPCSDynamicsFeed(MonitorFeed):
    name = FEED_NAME
    default_interval_s = 60.0  # xpcs_live records a snapshot ~every 60 s

    def evaluate(
        self, ctx: ExperimentContext, window: DataWindow, prior: list[Observation]
    ) -> Observation | None:
        data = window.derived("xpcs")
        if not data:
            return None  # not an XPCS run / xpcs_live not running / no snapshot yet

        cfg = ctx.for_feed(FEED_NAME)
        roi_id = str(cfg.get("roi_id", "average"))
        contrast_warn = float(cfg.get("contrast_warn", 0.05))
        min_frames = int(cfg.get("min_frames", 30))

        try:
            frames = int(data.get("frames_count", 0) or 0)
        except (TypeError, ValueError):
            frames = 0
        if frames < min_frames:
            return None  # too few frames to judge; wait for the next snapshot

        g2map = data.get("g2") or {}
        curve = _as_floats(g2map.get(roi_id) if roi_id in g2map else g2map.get("average"))
        if len(curve) < 2:
            return None

        beta = curve[0] - 1.0  # speckle contrast = g2(tau->0) - 1
        snap = data.get("snapshot", "?")

        # 1) No coherent speckle.
        if beta < contrast_warn:
            return Observation(
                severity="warn", feed_name=FEED_NAME, run_uid=window.run_uid,
                title="No coherent speckle",
                message=(f"Speckle contrast beta={beta:.3g} (g2[0]-1) is below "
                         f"{contrast_warn:.3g} for ROI '{roi_id}' ({snap}, {frames} frames)."),
                state_key=f"{FEED_NAME}:no_speckle",
                metrics={"contrast": beta, "frames": float(frames)},
                recommendation="Check beam coherence / pinhole / sample position.",
            )

        # 2) Dynamics captured? g2 should decay from 1+beta toward 1 within the
        # measured tau. "Captured" when the tail contrast has fallen below 1/e
        # of the initial contrast.
        tail = curve[-1] - 1.0
        if tail < beta / math.e:
            return Observation(
                severity="info", feed_name=FEED_NAME, run_uid=window.run_uid,
                title="Dynamics captured",
                message=(f"g2 has decayed to {tail / beta:.0%} of its initial contrast "
                         f"within the measured lag (beta={beta:.3g}, {snap})."),
                state_key=f"{FEED_NAME}:captured",
                metrics={"contrast": beta, "tail_fraction": (tail / beta) if beta else 0.0,
                         "frames": float(frames)},
                recommendation="Sufficient dynamics captured; you may stop or move on.",
            )
        return Observation(
            severity="info", feed_name=FEED_NAME, run_uid=window.run_uid,
            title="Dynamics not yet captured",
            message=(f"g2 is still near 1+beta at the longest measured lag "
                     f"(tail={tail:.3g} vs beta={beta:.3g}, {snap}); extend acquisition."),
            state_key=f"{FEED_NAME}:not_captured",
            metrics={"contrast": beta, "tail_fraction": (tail / beta) if beta else 1.0,
                     "frames": float(frames)},
            recommendation="Keep acquiring; the correlation has not decayed yet.",
        )


class XPCSMonitorPlugin(MonitorPlugin):
    @property
    def name(self) -> str:
        return FEED_NAME

    @property
    def description(self) -> str:
        return "XPCS speckle-contrast and dynamics-capture monitor (reads xpcs_live g2)."

    @property
    def category(self) -> str:
        return "acquisition"

    def create_feeds(self) -> list[MonitorFeed]:
        return [XPCSDynamicsFeed()]
