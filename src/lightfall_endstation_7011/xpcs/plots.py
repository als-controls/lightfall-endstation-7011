"""The four tabbed plots: g2 (log-tau), per-section overlay, I(t), convergence."""

from __future__ import annotations

import numpy as np
from lightfall.visualization import pg
from PySide6.QtWidgets import QVBoxLayout, QWidget

ROI_COLORS = ["#D55E00", "#009E73", "#F0E442", "#0072B2", "#CC79A7"]


def color_for(curve_id: str, roi_order: list[str] | None = None,
              colors: dict[str, str] | None = None):
    """Color for a curve id; None for 'average' (theme default pen).

    An explicit ``colors`` map (roi_id -> color, supplied by the ROI overlay)
    wins, so g2/intensity curves match the on-image ROI colors exactly. Ids
    absent from the map fall back to positional cycling within ``roi_order``.
    """
    if curve_id == "average":
        return None
    if colors and curve_id in colors:
        return colors[curve_id]
    roi_order = roi_order or []
    try:
        idx = roi_order.index(curve_id)
    except ValueError:
        idx = len(roi_order)
    return ROI_COLORS[idx % len(ROI_COLORS)]


class _CurvePlot(QWidget):
    """PlotWidget + per-curve-id PlotDataItem registry."""

    log_x = False
    log_y = False
    x_label = ""
    y_label = ""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._plot = pg.PlotWidget()
        self._plot.setLogMode(x=self.log_x, y=self.log_y)
        self._plot.setLabel("bottom", self.x_label)
        self._plot.setLabel("left", self.y_label)
        self._plot.addLegend()
        self._curves: dict[str, pg.PlotDataItem] = {}
        self._errors: dict[str, pg.ErrorBarItem] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot)

    def _set_curve(self, curve_id: str, x, y, roi_order=None, colors=None) -> None:
        if curve_id not in self._curves:
            color = color_for(curve_id, roi_order, colors)
            # NaN means "not measured" -- an ROI added mid-run has no I(t)
            # before it existed, and a lag can have too few pairs to estimate.
            # Break the line there rather than drawing across the gap.
            kwargs = {"name": curve_id, "connect": "finite"}
            if color is not None:
                kwargs["pen"] = pg.mkPen(color, width=2)
            self._curves[curve_id] = self._plot.plot(**kwargs)
        self._curves[curve_id].setData(np.asarray(x, dtype=float),
                                       np.asarray(y, dtype=float))

    def _set_errors(self, curve_id: str, x, y, sigma) -> None:
        """Attach (or update) error bars for a curve.

        Bars are only drawn where sigma is finite: a lag with fewer than two
        accumulated pairs reports NaN, and drawing that as a zero-height bar
        would read as certainty rather than as no estimate.
        """
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        sigma = np.asarray(sigma, dtype=float)
        finite = np.isfinite(sigma) & np.isfinite(y)
        item = self._errors.get(curve_id)
        if item is None:
            pen = self._curves[curve_id].opts.get("pen")
            item = pg.ErrorBarItem(pen=pen)
            self._plot.addItem(item)
            self._errors[curve_id] = item
        # log-x plots position items in log space, matching the curve
        xs = np.log10(x[finite]) if self.log_x else x[finite]
        item.setData(x=xs, y=y[finite], height=2.0 * sigma[finite])

    def _prune_errors(self, keep: set[str]) -> None:
        for cid in list(self._errors):
            if cid not in keep:
                self._plot.removeItem(self._errors.pop(cid))

    def _prune(self, keep: set[str]) -> None:
        for cid in list(self._curves):
            if cid not in keep:
                self._plot.removeItem(self._curves.pop(cid))
        self._prune_errors(keep)

    def clear(self) -> None:
        self._prune(set())


class G2Plot(_CurvePlot):
    log_x = True
    x_label = "tau (s)"
    y_label = "g2"

    ERROR_BAR_TOOLTIP = (
        "Error bars: ±1 standard error, assuming independent pairs.\n"
        "The estimate is optimistic at short tau (successive products share "
        "frames) and conservative at long tau (cascade averaging suppresses "
        "contrast faster than the pair count falls).\n"
        "No bar is drawn where fewer than two pairs have accumulated."
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setToolTip(self.ERROR_BAR_TOOLTIP)

    def update_from_payload(self, payload: dict, colors: dict | None = None) -> None:
        tau = payload.get("tau") or []
        g2 = payload.get("g2") or {}
        roi_order = [k for k in g2 if k != "average"]
        errs = payload.get("g2_err") or {}
        for cid, ys in g2.items():
            if len(ys) == len(tau) and tau:
                self._set_curve(cid, tau, ys, roi_order, colors)
                sigma = errs.get(cid)
                # absent uncertainty is left undrawn rather than drawn as zero
                if sigma is not None and len(sigma) == len(tau):
                    self._set_errors(cid, tau, ys, sigma)
        self._prune(set(g2))


class SectionsPlot(_CurvePlot):
    """Per-section average-g2 overlay, color-graded by section index."""

    log_x = True
    x_label = "tau (s)"
    y_label = "g2 (per section)"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._section_curves: list = []

    def add_section(self, payload: dict) -> None:
        tau = payload.get("tau") or []
        g2 = (payload.get("g2") or {}).get("average")
        if not tau or g2 is None:
            return
        idx = payload.get("index", len(self._section_curves) + 1)
        # color-grade: early sections dim, late sections bright
        hue = int(200 * (1 - 1 / (1 + 0.15 * idx)))
        # no legend name: long runs accumulate 100+ sections and the color
        # gradient alone communicates ordering
        curve = self._plot.plot(np.asarray(tau, float), np.asarray(g2, float),
                                pen=pg.mkPen(pg.intColor(hue, 255), width=1))
        self._section_curves.append(curve)

    def clear(self) -> None:
        for c in self._section_curves:
            self._plot.removeItem(c)
        self._section_curves.clear()


class IntensityPlot(_CurvePlot):
    x_label = "frame"
    y_label = "mean intensity"

    def update_from_payload(self, payload: dict, colors: dict | None = None) -> None:
        intensity = payload.get("intensity") or {}
        frames = intensity.get("frame_index") or []
        roi_order = [k for k in intensity if k not in ("frame_index", "average")]
        keep = set()
        for cid, ys in intensity.items():
            if cid == "frame_index":
                continue
            if len(ys) == len(frames) and frames:
                self._set_curve(cid, frames, ys, roi_order, colors)
                keep.add(cid)
        self._prune(keep)


class ConvergencePlot(_CurvePlot):
    """RMS convergence metric history vs frames, per (curve, time-scale)."""

    log_y = True
    x_label = "frames"
    y_label = "g2 RMS change"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._series: dict[tuple[str, str], list[tuple[int, float]]] = {}

    def update_from_payload(self, payload: dict) -> None:
        frames = payload.get("frames_count")
        metrics = payload.get("metrics") or {}
        if frames is None:
            return
        for cid, values in metrics.items():
            for scale, value in values.items():
                if scale == "frames" or scale.endswith(" end"):
                    continue
                series = self._series.setdefault((cid, scale), [])
                if not series or series[-1][0] != frames:
                    series.append((int(frames), float(value)))
                curve_id = f"{cid} / {scale}"
                xs, ys = zip(*series)
                self._set_curve(curve_id, xs, ys)

    def clear(self) -> None:
        self._series = {}
        super().clear()
