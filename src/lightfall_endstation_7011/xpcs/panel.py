"""XPCS live panel: measurement progress / quality / doneness instrument."""

from __future__ import annotations

from typing import Callable, ClassVar

from loguru import logger
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from lightfall.ui.panels.base import BasePanel, PanelMetadata

from .binding import RunBindingController
from .client import XPCSClient
from .plots import ConvergencePlot, G2Plot, IntensityPlot, SectionsPlot
from .roi_overlay import ROIOverlayManager
from .shapes import RectShape

DEFAULT_ROI = RectShape(x=992, y=992, w=64, h=64)     # near center of 2048^2
DEFAULT_MASK = RectShape(x=974, y=974, w=100, h=100)


def _default_image_factory(detector_device_name: str):
    """Resolve the detector ophyd device and build an OphydImageView.

    Returns (widget, overlay_plot_item) or a placeholder on failure.
    OphydImageView stores the pg.PlotItem as self._plot_item (verified in
    lightfall/src/lightfall/ui/widgets/camera/image_view.py:146).
    """
    try:
        from lightfall.devices import DeviceCatalog
        from lightfall.ui.widgets.camera.image_view import OphydImageView

        catalog = DeviceCatalog.get_instance()
        info = catalog.get_device_by_name(detector_device_name)
        if info is None or info.ophyd_device is None:
            raise LookupError(f"device {detector_device_name!r} not in catalog")
        view = OphydImageView(info.ophyd_device)
        return view, view._plot_item
    except Exception as ex:
        logger.warning(f"XPCS image view unavailable: {ex}")
        from lightfall.visualization import pg
        w = pg.PlotWidget()
        w.setTitle(f"No image source ({detector_device_name})")
        return w, w.getPlotItem()


class XPCSPanel(BasePanel):
    # emitted (possibly from the RunEngine thread) when a run's detector is
    # resolved; delivered to _on_detector_resolved on the Qt main thread
    detectorResolved = Signal(object)

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall_endstation_7011.panels.xpcs",
        name="XPCS Live",
        description="Live XPCS g2 correlation: measurement progress, quality, convergence",
        icon="mdi6.chart-sankey",
        category="Analysis",
        singleton=True,
        closable=True,
        keywords=["xpcs", "g2", "correlation", "live"],
        # NOT "center": that area is the Logbook's (center panels call
        # setCentralWidget and there is only one). Dock at the bottom as an
        # on-demand analysis instrument, with a sidebar button.
        default_area="bottom",
        sidebar_group="top",
    )

    def __init__(
        self,
        parent: QWidget | None = None,
        client: XPCSClient | None = None,
        binding: RunBindingController | None = None,
        image_widget_factory: Callable | None = None,
        detector_device_name: str = "andor",
    ) -> None:
        # Set instance attrs that _setup_ui needs BEFORE super().__init__,
        # because BasePanel.__init__ calls _setup_ui() (verified in
        # lightfall/src/lightfall/ui/panels/base.py:195).
        self._client = client or XPCSClient()
        self._binding = binding or RunBindingController(client=self._client)
        self._image_factory = image_widget_factory or (
            lambda: _default_image_factory(detector_device_name))
        self._current_detector_prefix: str | None = None
        self._run_uid: str | None = None
        super().__init__(parent)
        self._connect_client()
        # rebuild the image view when a run resolves its detector (queued to
        # the main thread since the binding fires from the RunEngine thread)
        self.detectorResolved.connect(self._on_detector_resolved)
        self._binding._on_detector_resolved = self.detectorResolved.emit
        # defer initial resync off the construction path — status() blocks the
        # calling thread up to its timeout when the backend is away
        QTimer.singleShot(0, self.resync)

    # BasePanel calls this during __init__
    def _setup_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        # left: image + controls
        left = QWidget()
        left_layout = QVBoxLayout(left)
        self._left_layout = left_layout
        image_widget, plot_item = self._image_factory()
        self._image_widget = image_widget
        self._roi_overlay = ROIOverlayManager(plot_item)
        left_layout.addWidget(image_widget, stretch=1)

        controls = QHBoxLayout()
        self._enable_toggle = QPushButton("Enable Processing")
        self._enable_toggle.setCheckable(True)
        self._enable_toggle.toggled.connect(self._on_enable_toggled)
        reset_btn = QPushButton("Reset")
        reset_btn.clicked.connect(lambda: self._client.reset())
        add_roi_btn = QPushButton("Add ROI")
        add_roi_btn.clicked.connect(self._on_add_roi)
        clear_rois_btn = QPushButton("Clear ROIs")
        clear_rois_btn.clicked.connect(self._on_clear_rois)
        add_mask_btn = QPushButton("Add Mask")
        add_mask_btn.clicked.connect(self._on_add_mask)
        apply_mask_btn = QPushButton("Apply Mask")
        apply_mask_btn.clicked.connect(self._on_apply_mask)
        clear_mask_btn = QPushButton("Clear Mask")
        clear_mask_btn.clicked.connect(self._on_clear_mask)
        for b in (self._enable_toggle, reset_btn, add_roi_btn, clear_rois_btn,
                  add_mask_btn, apply_mask_btn, clear_mask_btn):
            controls.addWidget(b)
        controls.addStretch()
        left_layout.addLayout(controls)
        splitter.addWidget(left)

        # right: tabbed plots
        tabs = QTabWidget()
        self._g2_plot = G2Plot()
        self._sections_plot = SectionsPlot()
        self._intensity_plot = IntensityPlot()
        self._convergence_plot = ConvergencePlot()
        tabs.addTab(self._g2_plot, "g2")
        tabs.addTab(self._sections_plot, "Sections")
        tabs.addTab(self._intensity_plot, "I(t)")
        tabs.addTab(self._convergence_plot, "Convergence")
        splitter.addWidget(tabs)
        splitter.setSizes([500, 500])

        # bottom: stats strip — a thin status bar pinned to its natural
        # height (the panel content lives in a QScrollArea, so without a
        # fixed-height container + splitter stretch the row balloons).
        stats_bar = QWidget()
        stats_bar.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        stats_row = QHBoxLayout(stats_bar)
        stats_row.setContentsMargins(6, 2, 6, 2)
        self._state_label = QLabel("State: —")
        self._stats_label = QLabel("Frames: 0")
        self._file_label = QLabel("File: —")
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #d9534f;")
        for w in (self._state_label, self._stats_label, self._file_label):
            stats_row.addWidget(w)
        stats_row.addStretch()
        stats_row.addWidget(self._error_label)

        self._layout.addWidget(splitter, 1)   # splitter takes the vertical stretch
        self._layout.addWidget(stats_bar, 0)  # status bar stays at natural height

    def _connect_client(self) -> None:
        self._client.g2Updated.connect(self._on_g2_updated)
        self._client.sectionCompleted.connect(self._sections_plot.add_section)
        self._client.stateChanged.connect(self._on_state_changed)
        self._client.errorReceived.connect(self._on_error)
        self._roi_overlay.roiChanged.connect(self._client.set_roi)
        self._roi_overlay.roiRemoved.connect(self._client.remove_roi)

    def _on_closing(self) -> None:
        """Tear down: unsubscribe the RunEngine + disable backend processing."""
        try:
            self._binding.disable()
        except Exception as ex:
            logger.exception(ex)
        super()._on_closing()

    # --- event handlers ---

    def _on_g2_updated(self, payload: dict) -> None:
        self._g2_plot.update_from_payload(payload)
        self._intensity_plot.update_from_payload(payload)
        self._convergence_plot.update_from_payload(payload)
        frames = payload.get("frames_count", 0)
        buf = payload.get("buffer_size", 0)
        self._stats_label.setText(f"Frames: {frames}  Buffer: {buf}")
        path = payload.get("file_path")
        self._file_label.setText(f"File: {path}" if path else "File: —")

    def _on_state_changed(self, payload: dict) -> None:
        self._state_label.setText(f"State: {payload.get('state', '?')}")
        # a new bound run resets the backend correlator; clear the
        # accumulating plots so the display starts fresh too
        run_uid = payload.get("run_uid")
        if run_uid and run_uid != self._run_uid:
            self._run_uid = run_uid
            self._clear_plots()

    def _clear_plots(self) -> None:
        self._g2_plot.clear()
        self._intensity_plot.clear()
        self._sections_plot.clear()
        self._convergence_plot.clear()

    def _on_detector_resolved(self, device) -> None:
        """Rebuild the live image view onto the run's active detector.

        Runs on the Qt main thread (queued from detectorResolved). The backend
        clears ROIs/mask on a detector switch, so the overlay restarts clean.
        """
        prefix = getattr(device, "prefix", None)
        if prefix and prefix == self._current_detector_prefix:
            return  # same detector — nothing to rebuild
        try:
            from lightfall.ui.widgets.camera.image_view import OphydImageView
            new_view = OphydImageView(device)
            new_plot = new_view._plot_item
        except Exception as ex:
            logger.warning(f"XPCS: could not build image view for "
                           f"{getattr(device, 'name', device)!r}: {ex}")
            return
        self._left_layout.replaceWidget(self._image_widget, new_view)
        self._left_layout.setStretchFactor(new_view, 1)
        self._image_widget.setParent(None)
        self._image_widget.deleteLater()
        self._image_widget = new_view
        # rebuild the ROI/mask overlay on the new image's plot item
        self._roi_overlay.clear_rois()
        self._roi_overlay.clear_mask_rects()
        self._roi_overlay = ROIOverlayManager(new_plot)
        self._roi_overlay.roiChanged.connect(self._client.set_roi)
        self._roi_overlay.roiRemoved.connect(self._client.remove_roi)
        self._current_detector_prefix = prefix

    def _on_error(self, payload: dict) -> None:
        self._error_label.setText(payload.get("message", "error"))

    # --- controls ---

    def _on_enable_toggled(self, checked: bool) -> None:
        try:
            if checked:
                self._binding.enable()
            else:
                self._binding.disable()
        except Exception as ex:
            logger.exception(ex)
            self._error_label.setText(str(ex))
            self._enable_toggle.setChecked(self._binding.enabled)

    def _on_add_roi(self) -> None:
        roi_id = self._roi_overlay.add_roi(DEFAULT_ROI)
        self._client.set_roi(roi_id, self._roi_overlay.shape_of(roi_id))

    def _on_clear_rois(self) -> None:
        self._roi_overlay.clear_rois()
        self._client.clear_rois()
        self._g2_plot.clear()
        self._intensity_plot.clear()

    def _on_add_mask(self) -> None:
        self._roi_overlay.add_mask_rect(DEFAULT_MASK)

    def _on_apply_mask(self) -> None:
        self._client.set_mask(self._roi_overlay.mask_shapes())

    def _on_clear_mask(self) -> None:
        self._roi_overlay.clear_mask_rects()
        self._client.clear_mask()

    # --- resync (panel open / service reconnect) ---

    def resync(self) -> None:
        status = self._client.status()
        if not status:
            self._state_label.setText("State: backend not found")
            return
        self._state_label.setText(f"State: {status.get('state', '?')}")
        self._roi_overlay.sync_from_status(status.get("rois", {}))
        n_sections = status.get("sections_count", 0)
        if n_sections:
            self._sections_plot.clear()
            fetched = 0
            while fetched < n_sections:
                page = self._client.get_sections(from_section=fetched, limit=20)
                sections = (page or {}).get("sections") or []
                if not sections:
                    break
                for sec in sections:
                    self._sections_plot.add_section(sec)
                fetched += len(sections)
                if len(sections) < 20:
                    break
