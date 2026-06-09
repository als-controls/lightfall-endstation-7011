import numpy as np
import pyqtgraph as pg
import pytest

from lightfall_endstation_7011.xpcs.plots import (
    ROI_COLORS, ConvergencePlot, G2Plot, IntensityPlot, SectionsPlot, color_for,
)


def test_color_cycle_stable():
    assert color_for("average") is None  # theme default
    assert color_for("roi-a", ["roi-a", "roi-b"]) == ROI_COLORS[0]
    assert color_for("roi-b", ["roi-a", "roi-b"]) == ROI_COLORS[1]


def test_color_for_explicit_map_wins_over_position():
    # an explicit roi_id -> color map (from the overlay) overrides the
    # positional fallback, so g2 curves match the on-image ROI colors
    colors = {"roi-b": ROI_COLORS[3]}
    assert color_for("roi-b", ["roi-b", "roi-a"], colors) == ROI_COLORS[3]
    assert color_for("average", ["roi-b"], colors) is None
    # ids not in the map still fall back to positional cycling
    assert color_for("roi-a", ["roi-b", "roi-a"], colors) == ROI_COLORS[1]


def test_g2_plot_paints_curve_with_mapped_color(qtbot):
    w = G2Plot()
    qtbot.addWidget(w)
    colors = {"r1": ROI_COLORS[2]}
    w.update_from_payload(
        {"tau": [1.0, 2.0], "g2": {"average": [1.5, 1.0], "r1": [2.0, 1.0]}},
        colors,
    )
    pen = w._curves["r1"].opts["pen"]
    assert pg.mkColor(pen.color()).name() == pg.mkColor(ROI_COLORS[2]).name()


def test_intensity_plot_paints_curve_with_mapped_color(qtbot):
    w = IntensityPlot()
    qtbot.addWidget(w)
    colors = {"r1": ROI_COLORS[4]}
    w.update_from_payload(
        {"intensity": {"frame_index": [0, 1], "average": [1.0, 1.1], "r1": [2.0, 2.1]}},
        colors,
    )
    pen = w._curves["r1"].opts["pen"]
    assert pg.mkColor(pen.color()).name() == pg.mkColor(ROI_COLORS[4]).name()


def test_g2_plot_updates_curves(qtbot):
    w = G2Plot()
    qtbot.addWidget(w)
    payload = {"tau": [1.0, 2.0, 4.0],
               "g2": {"average": [1.5, 1.2, 1.0], "r1": [2.0, 1.5, 1.0]}}
    w.update_from_payload(payload)
    assert set(w._curves) == {"average", "r1"}
    x, y = w._curves["average"].getData()
    np.testing.assert_allclose(y, [1.5, 1.2, 1.0])
    # stale curves removed
    w.update_from_payload({"tau": [1.0], "g2": {"average": [1.0]}})
    assert set(w._curves) == {"average"}


def test_sections_plot_accumulates(qtbot):
    w = SectionsPlot()
    qtbot.addWidget(w)
    w.add_section({"index": 1, "tau": [1, 2], "g2": {"average": [1.5, 1.0]}})
    w.add_section({"index": 2, "tau": [1, 2], "g2": {"average": [1.4, 1.0]}})
    assert len(w._section_curves) == 2
    w.clear()
    assert len(w._section_curves) == 0


def test_intensity_plot(qtbot):
    w = IntensityPlot()
    qtbot.addWidget(w)
    w.update_from_payload({"intensity": {
        "frame_index": [0, 1, 2], "average": [1.0, 1.1, 1.2], "r1": [2.0, 2.1, 2.2]}})
    assert set(w._curves) == {"average", "r1"}


def test_convergence_plot_accumulates_history(qtbot):
    w = ConvergencePlot()
    qtbot.addWidget(w)
    w.update_from_payload({"frames_count": 20,
                           "metrics": {"average": {"Time-scale 0": 0.5}}})
    w.update_from_payload({"frames_count": 40,
                           "metrics": {"average": {"Time-scale 0": 0.2,
                                                   "Time-scale 1": 0.4}}})
    key0 = ("average", "Time-scale 0")
    assert key0 in w._series
    assert w._series[key0] == [(20, 0.5), (40, 0.2)]
    w.clear()
    assert w._series == {}


def test_convergence_consumes_real_metrics_keys(qtbot):
    """Round-trip: actual MetricsTracker output shape -> ConvergencePlot."""
    w = ConvergencePlot()
    qtbot.addWidget(w)
    # shape produced by xpcs_live MetricsTracker.update(): per-curve dicts with
    # "Time-scale N" (+ optional "Time-scale N end") keys
    payload = {"frames_count": 40, "metrics": {
        "average": {"Time-scale 0": 0.2, "Time-scale 1": 0.4, "Time-scale 1 end": 0.4},
        "roi-abc": {"Time-scale 0": 0.3},
    }}
    w.update_from_payload(payload)
    assert ("average", "Time-scale 0") in w._series
    assert ("average", "Time-scale 1") in w._series
    assert ("roi-abc", "Time-scale 0") in w._series
    assert not any(scale.endswith(" end") for _, scale in w._series)
