import pyqtgraph as pg
import pytest
from PySide6.QtCore import Qt

from lightfall_endstation_7011.xpcs.roi_overlay import ROIOverlayManager
from lightfall_endstation_7011.xpcs.shapes import RectShape


@pytest.fixture
def host(qtbot):
    # Keep the PlotWidget alive (qtbot) so the PlotItem's ViewBox is not
    # garbage-collected before the test runs.
    w = pg.PlotWidget()
    qtbot.addWidget(w)
    w.show()  # realize the widget so C++ objects are fully initialized
    plot_item = w.getPlotItem()
    plot_item._host_widget = w  # pin reference to prevent GC
    return plot_item


def test_add_roi_assigns_id_and_color(host, qtbot):
    mgr = ROIOverlayManager(host, debounce_ms=0)
    rid = mgr.add_roi(RectShape(10, 20, 64, 64))
    assert rid in mgr.rois
    assert len(host.items) > 0


def test_color_map_stable_distinct_and_recycled(host, qtbot):
    from lightfall_endstation_7011.xpcs.plots import ROI_COLORS

    mgr = ROIOverlayManager(host, debounce_ms=0)
    a = mgr.add_roi(RectShape(0, 0, 8, 8))
    b = mgr.add_roi(RectShape(10, 10, 8, 8))
    ca = mgr.color_of(a)
    assert ca in ROI_COLORS
    assert ca != mgr.color_of(b)              # distinct colors
    assert mgr.color_map()[a] == ca           # stable across calls
    mgr.remove_roi(a)
    assert a not in mgr.color_map()           # freed on removal
    c = mgr.add_roi(RectShape(1, 1, 8, 8))
    assert mgr.color_of(c) == ca              # freed slot recycled


def test_roi_changed_signal_carries_geometry(host, qtbot):
    mgr = ROIOverlayManager(host, debounce_ms=0)
    rid = mgr.add_roi(RectShape(10, 20, 64, 64))
    with qtbot.waitSignal(mgr.roiChanged, timeout=1000) as blocker:
        mgr.rois[rid].setPos((30, 40))  # triggers sigRegionChangeFinished
    changed_id, shape = blocker.args
    assert changed_id == rid
    assert (shape.x, shape.y) == (30.0, 40.0)


def test_remove_and_clear(host, qtbot):
    mgr = ROIOverlayManager(host, debounce_ms=0)
    removed = []
    mgr.roiRemoved.connect(removed.append)
    a = mgr.add_roi(RectShape(0, 0, 8, 8))
    b = mgr.add_roi(RectShape(10, 10, 8, 8))
    mgr.remove_roi(a)
    assert removed == [a] and set(mgr.rois) == {b}
    mgr.clear_rois()
    assert mgr.rois == {} and removed == [a, b]


def test_sync_from_status_rebuilds(host, qtbot):
    mgr = ROIOverlayManager(host, debounce_ms=0)
    mgr.add_roi(RectShape(0, 0, 8, 8))
    mgr.sync_from_status({
        "r9": {"type": "rect", "x": 5, "y": 6, "w": 7, "h": 8},
    })
    assert set(mgr.rois) == {"r9"}
    shape = mgr.shape_of("r9")
    assert (shape.x, shape.y, shape.w, shape.h) == (5.0, 6.0, 7.0, 8.0)


def test_sync_cancels_pending_debounce(host, qtbot):
    mgr = ROIOverlayManager(host, debounce_ms=500)
    rid = mgr.add_roi(RectShape(10, 20, 64, 64))
    mgr.rois[rid].setPos((30, 40))  # starts the 500 ms debounce timer
    assert rid in mgr._timers and mgr._timers[rid].isActive()
    mgr.sync_from_status({rid: {"type": "rect", "x": 5, "y": 6, "w": 7, "h": 8}})
    assert mgr._timers == {}  # stale timer stopped — no unsolicited roiChanged
    emitted = []
    mgr.roiChanged.connect(lambda *a: emitted.append(a))
    qtbot.wait(600)
    assert emitted == []


def test_mask_rects_local_until_collected(host, qtbot):
    mgr = ROIOverlayManager(host, debounce_ms=0)
    mgr.add_mask_rect(RectShape(0, 0, 4, 4))
    mgr.add_mask_rect(RectShape(10, 10, 4, 4))
    shapes = mgr.mask_shapes()
    assert len(shapes) == 2
    mgr.clear_mask_rects()
    assert mgr.mask_shapes() == []
