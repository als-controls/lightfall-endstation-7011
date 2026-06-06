"""RectROI overlays on the live image: ROIs (backend-synced, debounced)
and mask rects (local until Apply). Colors match the plot palette."""

from __future__ import annotations

import uuid

from lightfall.visualization import pg
from PySide6.QtCore import QObject, QTimer, Signal

from .plots import ROI_COLORS
from .shapes import RectShape

MASK_COLOR = "#888888"


class ROIOverlayManager(QObject):
    roiChanged = Signal(str, object)   # roi_id, RectShape — debounced, post-release
    roiRemoved = Signal(str)

    def __init__(self, plot_item, debounce_ms: int = 300, parent=None) -> None:
        super().__init__(parent)
        self._plot_item = plot_item
        self._debounce_ms = debounce_ms
        self.rois: dict[str, pg.RectROI] = {}
        self._mask_rects: list[pg.RectROI] = []
        self._timers: dict[str, QTimer] = {}
        self._color_index = 0

    # --- ROIs ---

    def add_roi(self, shape: RectShape, roi_id: str | None = None) -> str:
        roi_id = roi_id or f"roi-{uuid.uuid4().hex[:8]}"
        color = ROI_COLORS[self._color_index % len(ROI_COLORS)]
        self._color_index += 1
        item = pg.RectROI((shape.x, shape.y), (shape.w, shape.h),
                          pen=pg.mkPen(color, width=2), removable=False)
        item.sigRegionChangeFinished.connect(lambda *_: self._debounce(roi_id))
        self._plot_item.addItem(item)
        self.rois[roi_id] = item
        return roi_id

    def shape_of(self, roi_id: str) -> RectShape:
        item = self.rois[roi_id]
        pos, size = item.pos(), item.size()
        return RectShape.from_pos_size((pos.x(), pos.y()), (size.x(), size.y()))

    def _debounce(self, roi_id: str) -> None:
        if self._debounce_ms <= 0:
            self._emit_changed(roi_id)
            return
        timer = self._timers.get(roi_id)
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda rid=roi_id: self._emit_changed(rid))
            self._timers[roi_id] = timer
        timer.start(self._debounce_ms)

    def _emit_changed(self, roi_id: str) -> None:
        if roi_id in self.rois:
            self.roiChanged.emit(roi_id, self.shape_of(roi_id))

    def remove_roi(self, roi_id: str) -> None:
        item = self.rois.pop(roi_id, None)
        if item is not None:
            self._plot_item.removeItem(item)
            self.roiRemoved.emit(roi_id)
        timer = self._timers.pop(roi_id, None)
        if timer is not None:
            timer.stop()

    def clear_rois(self) -> None:
        for roi_id in list(self.rois):
            self.remove_roi(roi_id)
        self._color_index = 0

    def sync_from_status(self, rois: dict[str, dict]) -> None:
        """Rebuild overlays from a backend status echo (resync path).
        Does NOT emit roiChanged/roiRemoved (backend already has these)."""
        for roi_id, item in list(self.rois.items()):
            self._plot_item.removeItem(item)
            self.rois.pop(roi_id)
        # stop pending debounce timers — a timer surviving the rebuild could
        # fire for a re-added id and emit an unsolicited roiChanged
        for timer in self._timers.values():
            timer.stop()
        self._timers.clear()
        self._color_index = 0
        for roi_id, shape_dict in rois.items():
            self.add_roi(RectShape.from_dict(shape_dict), roi_id=roi_id)

    # --- mask rects (local until Apply) ---

    def add_mask_rect(self, shape: RectShape) -> None:
        item = pg.RectROI((shape.x, shape.y), (shape.w, shape.h),
                          pen=pg.mkPen(MASK_COLOR, width=2, style=None))
        self._plot_item.addItem(item)
        self._mask_rects.append(item)

    def mask_shapes(self) -> list[RectShape]:
        out = []
        for item in self._mask_rects:
            pos, size = item.pos(), item.size()
            out.append(RectShape.from_pos_size((pos.x(), pos.y()),
                                               (size.x(), size.y())))
        return out

    def clear_mask_rects(self) -> None:
        for item in self._mask_rects:
            self._plot_item.removeItem(item)
        self._mask_rects.clear()
