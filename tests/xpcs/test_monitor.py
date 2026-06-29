import math

from lightfall.monitor.data_window import DataWindow
from lightfall.monitor.models import ExperimentContext

from lightfall_endstation_7011.xpcs.monitor import XPCSDynamicsFeed, XPCSMonitorPlugin


def _window(derived):
    return DataWindow(run_uid="u1", derived_provider=lambda name: derived if name == "xpcs" else None)


def _ctx(**cfg):
    return ExperimentContext(feed_config={"xpcs_dynamics": cfg})


def _g2(beta_initial, beta_final, n=10):
    # linear ramp of (g2-1) from beta_initial down to beta_final
    return [1.0 + beta_initial + (beta_final - beta_initial) * i / (n - 1) for i in range(n)]


def test_no_coherent_speckle_warns():
    derived = {"tau": list(range(10)), "g2": {"average": _g2(0.01, 0.005)},
               "frames_count": 100, "metrics": {}, "snapshot": "snapshot_002"}
    obs = XPCSDynamicsFeed().evaluate(_ctx(contrast_warn=0.05, min_frames=30), _window(derived), [])
    assert obs is not None
    assert obs.state_key == "xpcs_dynamics:no_speckle"
    assert obs.severity == "warn"


def test_dynamics_captured_when_g2_decays():
    # strong contrast that decays below beta/e within the window
    derived = {"tau": list(range(10)), "g2": {"average": _g2(0.4, 0.05)},
               "frames_count": 200, "metrics": {}, "snapshot": "snapshot_005"}
    obs = XPCSDynamicsFeed().evaluate(_ctx(), _window(derived), [])
    assert obs is not None and obs.state_key == "xpcs_dynamics:captured"


def test_dynamics_not_captured_when_g2_flat():
    # strong contrast, barely decays -> not captured
    derived = {"tau": list(range(10)), "g2": {"average": _g2(0.4, 0.38)},
               "frames_count": 200, "metrics": {}, "snapshot": "snapshot_005"}
    obs = XPCSDynamicsFeed().evaluate(_ctx(), _window(derived), [])
    assert obs is not None and obs.state_key == "xpcs_dynamics:not_captured"


def test_insufficient_frames_returns_none():
    derived = {"tau": [0, 1], "g2": {"average": [1.4, 1.1]},
               "frames_count": 5, "metrics": {}, "snapshot": "snapshot_001"}
    assert XPCSDynamicsFeed().evaluate(_ctx(min_frames=30), _window(derived), []) is None


def test_no_derived_data_returns_none():
    assert XPCSDynamicsFeed().evaluate(_ctx(), _window(None), []) is None


def test_roi_id_selects_curve():
    derived = {"tau": [0, 1], "g2": {"average": _g2(0.4, 0.05), "0": _g2(0.01, 0.005)},
               "frames_count": 100, "metrics": {}, "snapshot": "snapshot_002"}
    obs = XPCSDynamicsFeed().evaluate(_ctx(roi_id="0", contrast_warn=0.05), _window(derived), [])
    assert obs is not None and obs.state_key == "xpcs_dynamics:no_speckle"  # roi 0 has no speckle


def test_plugin_exposes_feed():
    p = XPCSMonitorPlugin()
    assert p.name == "xpcs_dynamics"
    assert p.type_name == "monitor"
    feeds = p.create_feeds()
    assert len(feeds) == 1 and feeds[0].name == "xpcs_dynamics"
