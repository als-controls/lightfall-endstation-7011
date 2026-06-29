# XPCS Monitor Feed (Plan B-xpcs) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`). This repo (`lightfall-endstation-7011`) provides the XPCS speckle/dynamics feed for the proactive monitor whose framework lives in the `lightfall` package (feature branch). The feed imports the new `lightfall.monitor` API.

**Goal:** Add an XPCS speckle/dynamics `MonitorFeed` + `MonitorPlugin` that, during an XPCS run, reads xpcs_live's already-computed g₂ (via `window.derived("xpcs")`) and emits unsolicited feedback: warn when there's no coherent speckle, and report whether the dynamics have been captured. No data analysis in-process — it judges the recorded g₂ curve directly (contrast and decay), no fitting.

**Architecture:** `XPCSDynamicsFeed(MonitorFeed)` consumes the dict produced by the lightfall scheduler's `derived("xpcs")` provider (`{"tau", "g2": {"average", "<roi_id>"}, "frames_count", "metrics", "intensity_average", "snapshot"}` or `None`). `XPCSMonitorPlugin(MonitorPlugin)` contributes the feed; it's registered through the endstation's `manifest.py` (entry-point group `lightfall.plugins`). It no-ops on non-XPCS runs (provider returns `None`).

**Tech Stack:** Python ≥3.11, the `lightfall.monitor` API (`MonitorFeed`, `MonitorPlugin`, `DataWindow`, `ExperimentContext`, `Observation`), the endstation's `PluginManifest`/`PluginEntry`.

## Global Constraints

- **No data analysis in the Lightfall process.** The feed reads already-computed g₂ arrays and applies threshold/decay *judgment* only — no correlation, **no curve fitting** (xpcs_live records no fit; only raw g₂ + RMS convergence).
- **Advisory only**, never blocks anything, must not raise (the lightfall scheduler wraps `evaluate` in try/except, but the feed should still degrade to `None` cleanly).
- **No Qt in the feed** — it's headless judgment (the feed module must not import PySide6).
- **The feed reads `window.derived("xpcs")`** — never NATS, never raw frames.
- **Cross-repo test command** (run from the endstation worktree; the feed needs the feature-branch `lightfall`): the lightfall worktree `src` must precede the endstation `src` on `PYTHONPATH` (Windows `os.pathsep` = `;`):
  ```bash
  PYTHONPATH="C:/Users/rp/PycharmProjects/ncs/lightfall/.claude/worktrees/proactive-monitor/src;C:/Users/rp/PycharmProjects/ncs/lightfall-endstation-7011-wt-xpcs/src" \
    "C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe" -m pytest tests/xpcs/test_monitor.py -v
  ```
  (No `QT_QPA_PLATFORM` needed — the feed/tests are headless.) Import the concrete submodules (`lightfall.monitor.feed`, `.monitor_plugin`, `.models`, `.data_window`) — `lightfall.monitor.__init__` is empty.

## File Structure

- Create: `src/lightfall_endstation_7011/xpcs/monitor.py` — `XPCSDynamicsFeed` + `XPCSMonitorPlugin`.
- Modify: `src/lightfall_endstation_7011/manifest.py` — add the `type_name="monitor"` entry.
- Test: `tests/xpcs/test_monitor.py`, `tests/xpcs/test_monitor_manifest.py`.

---

### Task 1: XPCS speckle/dynamics feed + plugin

**Files:**
- Create: `src/lightfall_endstation_7011/xpcs/monitor.py`
- Test: `tests/xpcs/test_monitor.py`

**Interfaces:**
- Consumes: `lightfall.monitor.feed.MonitorFeed`, `lightfall.monitor.monitor_plugin.MonitorPlugin`, `lightfall.monitor.models.{ExperimentContext, Observation}`, `lightfall.monitor.data_window.DataWindow`. The `derived("xpcs")` dict shape (from the lightfall scheduler): `{"tau": list|array|None, "g2": {"average": list|array, "<roi_id>": ...}, "frames_count": int, "metrics": {...}, "intensity_average": ..., "snapshot": str}` or `None`.
- Produces: `XPCSDynamicsFeed(MonitorFeed)` (`name="xpcs_dynamics"`, `default_interval_s=60.0`); `XPCSMonitorPlugin(MonitorPlugin)` (`name="xpcs_dynamics"`, `category="acquisition"`, `create_feeds()->[XPCSDynamicsFeed()]`). Config via `ctx.for_feed("xpcs_dynamics")`: `roi_id` (default `"average"`), `contrast_warn` (default `0.05`), `min_frames` (default `30`).

- [ ] **Step 1: Write the failing test**

```python
# tests/xpcs/test_monitor.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONPATH="C:/Users/rp/PycharmProjects/ncs/lightfall/.claude/worktrees/proactive-monitor/src;C:/Users/rp/PycharmProjects/ncs/lightfall-endstation-7011-wt-xpcs/src" "C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe" -m pytest tests/xpcs/test_monitor.py -v
```
Expected: FAIL — `ModuleNotFoundError: lightfall_endstation_7011.xpcs.monitor`.

- [ ] **Step 3: Write the implementation**

```python
# src/lightfall_endstation_7011/xpcs/monitor.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run (same cross-repo command as Step 2). Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/xpcs/monitor.py tests/xpcs/test_monitor.py
git commit -m "feat(xpcs): proactive speckle/dynamics monitor feed"
```

---

### Task 2: Register the monitor in the manifest

**Files:**
- Modify: `src/lightfall_endstation_7011/manifest.py`
- Test: `tests/xpcs/test_monitor_manifest.py`

**Interfaces:**
- Consumes: the endstation `PluginManifest`/`PluginEntry`; `XPCSMonitorPlugin` (Task 1).
- Produces: a `PluginEntry(type_name="monitor", name="xpcs_dynamics", import_path="lightfall_endstation_7011.xpcs.monitor:XPCSMonitorPlugin")` in the manifest's `plugins` list.

- [ ] **Step 1: Write the failing test**

```python
# tests/xpcs/test_monitor_manifest.py
import importlib

from lightfall.monitor.monitor_plugin import MonitorPlugin

from lightfall_endstation_7011.manifest import manifest


def test_manifest_has_xpcs_monitor_entry():
    entries = [e for e in manifest.plugins
               if getattr(e, "type_name", None) == "monitor"
               and getattr(e, "name", None) == "xpcs_dynamics"]
    assert len(entries) == 1
    entry = entries[0]
    # import_path resolves to a MonitorPlugin subclass
    module_path, _, cls_name = entry.import_path.partition(":")
    mod = importlib.import_module(module_path)
    cls = getattr(mod, cls_name)
    assert issubclass(cls, MonitorPlugin)
    plugin = cls()
    assert plugin.create_feeds()[0].name == "xpcs_dynamics"
```

- [ ] **Step 2: Run test to verify it fails**

Run:
```bash
PYTHONPATH="C:/Users/rp/PycharmProjects/ncs/lightfall/.claude/worktrees/proactive-monitor/src;C:/Users/rp/PycharmProjects/ncs/lightfall-endstation-7011-wt-xpcs/src" "C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe" -m pytest tests/xpcs/test_monitor_manifest.py -v
```
Expected: FAIL — no monitor entry.

- [ ] **Step 3: Add the manifest entry**

In `src/lightfall_endstation_7011/manifest.py`, inside the `plugins=[...]` list (next to the XPCS panel entry), add:

```python
        PluginEntry(
            type_name="monitor",
            name="xpcs_dynamics",
            import_path="lightfall_endstation_7011.xpcs.monitor:XPCSMonitorPlugin",
        ),
```

- [ ] **Step 4: Run test to verify it passes**

Run the Step 2 command. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lightfall_endstation_7011/manifest.py tests/xpcs/test_monitor_manifest.py
git commit -m "feat(xpcs): register xpcs_dynamics monitor in plugin manifest"
```

---

## Final verification

- [ ] Full XPCS test dir (catches regressions):
  ```bash
  PYTHONPATH="C:/Users/rp/PycharmProjects/ncs/lightfall/.claude/worktrees/proactive-monitor/src;C:/Users/rp/PycharmProjects/ncs/lightfall-endstation-7011-wt-xpcs/src" "C:/Users/rp/PycharmProjects/ncs/.venv/Scripts/python.exe" -m pytest tests/xpcs -q
  ```
- [ ] Manifest import smoke:
  ```bash
  PYTHONPATH="...lightfall.../src;...endstation.../src" "...python.exe" -c "import lightfall_endstation_7011.manifest as m; print(len(m.manifest.plugins), 'plugins')"
  ```

## Self-Review (plan author)

- **Spec coverage:** the XPCS speckle ("no coherent speckle") + dynamics ("captured" / "not captured") feed (T1), registered so lightfall loads it (T2). Reads `window.derived("xpcs")` (the lightfall provider built in Plan B-core), judges contrast `g2[0]-1` and decay (1/e crossing) — no fitting, honoring the no-analysis rule.
- **No placeholders:** all code + tests present. The decay heuristic (tail < beta/e) is explicit and tested for both captured/not-captured.
- **Type consistency:** `XPCSDynamicsFeed`/`XPCSMonitorPlugin` match the `MonitorFeed`/`MonitorPlugin` surfaces; `derived("xpcs")` dict keys match the lightfall provider's output (`g2.average`, `g2.<roi>`, `frames_count`).

## Risk note (for the eventual PR)

The recorded `xpcs` Tiled field names (`g2_average`, `g2_roi_<id>`, `frames_count`) come from the design doc; the actual writer is in the external `xpcs_live` repo. The lightfall `derived("xpcs")` provider maps those to the dict this feed consumes. Verify field spelling/casing against a real recorded run before relying in production. The feed degrades to `None` (silent) if the shape doesn't match — safe default. Also: this branch's PR depends on the lightfall `feature/proactive-monitor` branch (the `lightfall.monitor` API) merging first.
