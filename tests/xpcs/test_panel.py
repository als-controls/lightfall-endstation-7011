from unittest.mock import MagicMock

import pyqtgraph as pg
import pytest
from PySide6.QtWidgets import QWidget

from lightfall_endstation_7011.xpcs.client import XPCSClient
from lightfall_endstation_7011.xpcs.panel import XPCSPanel


@pytest.fixture
def panel(qtbot, fake_ipc):
    client = XPCSClient(ipc=fake_ipc)
    binding = MagicMock()
    binding.enabled = False

    def image_factory():
        w = pg.PlotWidget()
        return w, w.getPlotItem()

    p = XPCSPanel(client=client, binding=binding, image_widget_factory=image_factory)
    qtbot.addWidget(p)
    p.test_ipc = fake_ipc
    p.test_binding = binding
    return p


def test_metadata():
    md = XPCSPanel.panel_metadata
    assert md.id == "lightfall_endstation_7011.panels.xpcs"
    assert md.singleton is True
    # must not hijack the center area (reserved for the Logbook) and must
    # live in a dockable area so it gets a sidebar button
    assert md.default_area != "center"
    assert md.default_area in ("left", "right", "bottom")


def test_g2_event_updates_plots_and_stats(panel):
    panel.test_ipc.emit("xpcs.g2.updated", {
        "run_uid": "u1", "frames_count": 100, "buffer_size": 100,
        "file_path": "C:/data/x.h5", "seq": 1,
        "tau": [1.0, 2.0], "g2": {"average": [1.5, 1.0]},
        "intensity": {"frame_index": [0, 1], "average": [1.0, 1.1]},
        "metrics": {"average": {"Time-scale 0": 0.3}},
    })
    assert "average" in panel._g2_plot._curves
    assert "average" in panel._intensity_plot._curves
    assert ("average", "Time-scale 0") in panel._convergence_plot._series
    assert "100" in panel._stats_label.text()
    assert "x.h5" in panel._file_label.text()


def test_detector_resolved_rebuilds_image_view(panel):
    from types import SimpleNamespace
    before = panel._image_widget
    panel._on_detector_resolved(SimpleNamespace(prefix="13X:", name="mte3test"))
    assert panel._image_widget is not before          # rebuilt onto the detector
    assert panel._current_detector_prefix == "13X:"
    # same detector again -> no rebuild (idempotent)
    again = panel._image_widget
    panel._on_detector_resolved(SimpleNamespace(prefix="13X:", name="mte3test"))
    assert panel._image_widget is again


def test_new_run_clears_accumulating_plots(panel):
    panel.test_ipc.emit("xpcs.state", {"state": "Processing", "run_uid": "run-A"})
    panel.test_ipc.emit("xpcs.section.completed",
                        {"index": 1, "tau": [1, 2], "g2": {"average": [1.5, 1.0]}})
    assert len(panel._sections_plot._section_curves) == 1
    # a different run resets the backend correlator -> clear the display
    panel.test_ipc.emit("xpcs.state", {"state": "Processing", "run_uid": "run-B"})
    assert len(panel._sections_plot._section_curves) == 0


def test_section_event_feeds_sections_plot(panel):
    panel.test_ipc.emit("xpcs.section.completed",
                        {"index": 1, "tau": [1, 2], "g2": {"average": [1.5, 1.0]}})
    assert len(panel._sections_plot._section_curves) == 1


def test_state_event_updates_label(panel):
    panel.test_ipc.emit("xpcs.state", {"state": "Processing", "run_uid": "u1"})
    assert "Processing" in panel._state_label.text()


def test_enable_toggle_lives_in_title_bar(panel):
    assert panel._enable_toggle in panel.title_bar_widgets


def test_reset_action_in_title_bar(panel):
    panel.test_ipc.replies["xpcs.reset"] = {"status": "ok"}
    assert panel._reset_action in panel.title_bar_actions
    panel._reset_action.trigger()
    assert any(r[0] == "xpcs.reset" for r in panel.test_ipc.requests)


def test_roi_and_mask_tools_injected_into_image_toolbar(qtbot, fake_ipc):
    injected = []

    class _ToolView(pg.PlotWidget):
        def add_toolbar_action(self, action):
            injected.append(action)
            return None

    def factory():
        v = _ToolView()
        return v, v.getPlotItem()

    binding = MagicMock()
    binding.enabled = False
    p = XPCSPanel(client=XPCSClient(ipc=fake_ipc), binding=binding,
                  image_widget_factory=factory)
    qtbot.addWidget(p)
    assert p._add_roi_action in injected
    assert p._clear_rois_action in injected
    assert p._mask_action in injected          # masking submenu launcher


def test_enable_toggle_drives_binding(panel, qtbot):
    panel._enable_toggle.setChecked(True)
    panel.test_binding.enable.assert_called_once()
    panel._enable_toggle.setChecked(False)
    panel.test_binding.disable.assert_called_once()


def test_add_roi_button_syncs_to_backend(panel):
    panel.test_ipc.replies["xpcs.roi.set"] = {"status": "ok"}
    panel._on_add_roi()
    roi_requests = [r for r in panel.test_ipc.requests if r[0] == "xpcs.roi.set"]
    assert len(roi_requests) == 1
    assert roi_requests[0][1]["shape"]["type"] == "rect"


def test_apply_mask_sends_shapes(panel):
    panel.test_ipc.replies["xpcs.mask.set"] = {"status": "ok"}
    panel._on_add_mask()
    panel._on_apply_mask()
    mask_requests = [r for r in panel.test_ipc.requests if r[0] == "xpcs.mask.set"]
    assert len(mask_requests) == 1
    assert len(mask_requests[0][1]["shapes"]) == 1


def test_resync_rebuilds_rois_and_sections(panel):
    panel.test_ipc.replies["xpcs.status"] = {
        "status": "ok", "state": "Idle", "frames_count": 0, "buffer_size": 0,
        "file_path": None, "run_uid": None, "sections_count": 1,
        "rois": {"r1": {"type": "rect", "x": 1, "y": 2, "w": 3, "h": 4}},
        "mask": {"shapes": [], "path": None},
    }
    panel.test_ipc.replies["xpcs.sections.get"] = {
        "status": "ok", "total": 1,
        "sections": [{"index": 1, "frames": 10, "tau": [1, 2],
                      "g2": {"average": [1.5, 1.0]}}],
    }
    panel.resync()
    assert set(panel._roi_overlay.rois) == {"r1"}
    assert len(panel._sections_plot._section_curves) == 1


def test_error_event_shows_in_status(panel):
    panel.test_ipc.emit("xpcs.error", {"message": "GPU on fire"})
    assert "GPU on fire" in panel._error_label.text()


def test_panel_close_disables_binding(panel):
    panel.test_binding.enabled = True
    panel._on_closing()
    panel.test_binding.disable.assert_called_once()


def test_enable_toggle_rolls_back_on_failure(panel):
    panel.test_binding.enable.side_effect = RuntimeError("backend gone")
    panel.test_binding.enabled = False
    panel._enable_toggle.setChecked(True)
    assert panel._enable_toggle.isChecked() is False
