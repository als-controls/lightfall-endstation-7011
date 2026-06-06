# XPCS Panel (lightfall-endstation-7011) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `XPCSPanel` PanelPlugin — live measurement-progress/quality instrument for the xpcs-live NATS service — per the spec at `docs/superpowers/specs/2026-06-05-xpcs-lightfall-port-design.md`.

**Architecture:** A `BasePanel` with: live detector image (lightfall's `OphydImageView`, device from `DeviceCatalog`) + pyqtgraph `RectROI` overlays, four tabbed plots (g2, sections, I(t), convergence), stats/controls row. NATS via `lightfall.ipc.service.get_ipc_service()` wrapped in an `XPCSClient` QObject (signals on main thread); run binding via a RunEngine subscription publishing `xpcs.run.bind`/`xpcs.run.stop` on start/stop docs. Backend is the single source of truth; resync via `xpcs.status` + `xpcs.sections.get`.

**Tech Stack:** PySide6, pyqtgraph (via `lightfall.visualization.pg` themed wrappers), lightfall plugin API, pytest-qt.

**Prerequisite:** the backend plan (`xpcs_live/docs/superpowers/plans/2026-06-05-nats-tiled-rescope.md`) at least through its Task 10 (NATS service) for live testing; unit tests here run fully against fakes.

**Working directory:** `C:\Users\rp\PycharmProjects\ncs\lightfall-endstation-7011`. Run tests with the project venv python (check for `.venv\Scripts\python`; if absent, use the interpreter the existing tests run under — `python -m pytest tests/ -v` from the repo root with lightfall importable). Never bare `pytest`.

---

## File structure (end state)

```
src/lightfall_endstation_7011/xpcs/
├── __init__.py
├── shapes.py        RectShape dataclass ↔ wire dicts (mirror of backend xpcs_live/shapes.py schema)
├── client.py        XPCSClient (QObject): actions + event signals over IPCService
├── binding.py       RunBindingController: RE subscription → bind/stop publishes
├── plots.py         G2Plot, SectionsPlot, IntensityPlot, ConvergencePlot
├── roi_overlay.py   ROIOverlayManager: RectROIs on a pg.PlotItem, colors, debounce
├── panel.py         XPCSPanel(BasePanel)
└── plugin.py        XPCSPanelPlugin(PanelPlugin)
tests/xpcs/
├── __init__.py
├── conftest.py      FakeIPC fixture
├── test_shapes.py
├── test_client.py
├── test_binding.py
├── test_plots.py
├── test_roi_overlay.py
└── test_panel.py
src/lightfall_endstation_7011/manifest.py   MOD: add panel PluginEntry
```

Wire schemas are normative in the spec; coordinate convention: **x = column, y = row** (shared with backend).

ROI color cycle (used by overlays AND all plots so curves match boxes):
`["#D55E00", "#009E73", "#F0E442", "#0072B2", "#CC79A7"]` (the g2viewer palette); `"average"` is always the theme default pen.

---

### Task 1: Shape dataclass + serialization

**Files:**
- Create: `src/lightfall_endstation_7011/xpcs/__init__.py` (empty)
- Create: `src/lightfall_endstation_7011/xpcs/shapes.py`
- Create: `tests/xpcs/__init__.py` (empty)
- Test: `tests/xpcs/test_shapes.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/xpcs/test_shapes.py
import pytest

from lightfall_endstation_7011.xpcs.shapes import RectShape


def test_round_trip():
    r = RectShape(x=10.0, y=20.0, w=64.0, h=32.0)
    d = r.to_dict()
    assert d == {"type": "rect", "x": 10.0, "y": 20.0, "w": 64.0, "h": 32.0}
    assert RectShape.from_dict(d) == r


def test_from_dict_rejects_unknown_type():
    with pytest.raises(ValueError):
        RectShape.from_dict({"type": "ellipse", "x": 0, "y": 0, "w": 1, "h": 1})


def test_from_roi_state():
    # pyqtgraph RectROI state: pos = (x, y) bottom-left in data coords, size = (w, h)
    r = RectShape.from_pos_size((5.0, 7.0), (10.0, 12.0))
    assert (r.x, r.y, r.w, r.h) == (5.0, 7.0, 10.0, 12.0)
```

- [ ] **Step 2: Run, expect FAIL**

Run: `python -m pytest tests/xpcs/test_shapes.py -v`

- [ ] **Step 3: Implement**

```python
# src/lightfall_endstation_7011/xpcs/shapes.py
"""ROI/mask geometry, mirroring xpcs_live/shapes.py wire schema.

Convention: x = column, y = row, origin top-left of the detector array.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RectShape:
    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict:
        return {"type": "rect", "x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, d: dict) -> "RectShape":
        if d.get("type") != "rect":
            raise ValueError(f"Unsupported shape type: {d.get('type')!r}")
        return cls(x=float(d["x"]), y=float(d["y"]), w=float(d["w"]), h=float(d["h"]))

    @classmethod
    def from_pos_size(cls, pos, size) -> "RectShape":
        return cls(x=float(pos[0]), y=float(pos[1]), w=float(size[0]), h=float(size[1]))
```

- [ ] **Step 4: Run, expect 3 passed**
- [ ] **Step 5: Commit** — `git add src/lightfall_endstation_7011/xpcs tests/xpcs && git commit -m "feat(xpcs): RectShape wire schema"`

---

### Task 2: FakeIPC fixture + XPCSClient

**Files:**
- Create: `tests/xpcs/conftest.py`
- Create: `src/lightfall_endstation_7011/xpcs/client.py`
- Test: `tests/xpcs/test_client.py`

The client wraps `IPCService` (`lightfall/src/lightfall/ipc/service.py`): `request(subject, data, timeout_ms) -> dict | None`, `publish(subject, data)`, `subscribe(subject, callback, *, main_thread=True)` where callback signature is `callback(subject, data, reply)`.

- [ ] **Step 0: Ensure pytest-qt is available** — the `qtbot` fixture is used from here on. Check `pyproject.toml`'s test/dev extras; if `pytest-qt` is absent, add it there and install.

- [ ] **Step 1: Write the FakeIPC fixture**

```python
# tests/xpcs/conftest.py
import pytest


class FakeIPC:
    """Duck-type of lightfall.ipc.service.IPCService for unit tests."""

    def __init__(self):
        self.published = []      # (subject, data)
        self.requests = []       # (subject, data)
        self.replies = {}        # subject -> dict to return from request()
        self.subscriptions = {}  # subject -> callback

    def publish(self, subject, data):
        self.published.append((subject, data))

    def request(self, subject, data, timeout_ms=1000):
        self.requests.append((subject, data))
        return self.replies.get(subject)

    def subscribe(self, subject, callback, *, main_thread=True):
        self.subscriptions[subject] = callback

    def emit(self, subject, data):
        """Test hook: simulate an incoming NATS message."""
        self.subscriptions[subject](subject, data, None)


@pytest.fixture
def fake_ipc():
    return FakeIPC()
```

- [ ] **Step 2: Write the failing client tests**

```python
# tests/xpcs/test_client.py
from lightfall_endstation_7011.xpcs.client import XPCSClient
from lightfall_endstation_7011.xpcs.shapes import RectShape


def test_actions_map_to_subjects(fake_ipc):
    c = XPCSClient(ipc=fake_ipc)
    fake_ipc.replies = {s: {"status": "ok"} for s in (
        "xpcs.processing.enable", "xpcs.processing.disable", "xpcs.reset",
        "xpcs.roi.set", "xpcs.roi.remove", "xpcs.roi.clear",
        "xpcs.mask.set", "xpcs.mask.clear",
    )}
    c.enable(); c.disable(); c.reset()
    c.set_roi("r1", RectShape(0, 0, 2, 2))
    c.remove_roi("r1"); c.clear_rois()
    c.set_mask([RectShape(0, 0, 1, 1)]); c.clear_mask()
    subjects = [s for s, _ in fake_ipc.requests]
    assert subjects == [
        "xpcs.processing.enable", "xpcs.processing.disable", "xpcs.reset",
        "xpcs.roi.set", "xpcs.roi.remove", "xpcs.roi.clear",
        "xpcs.mask.set", "xpcs.mask.clear",
    ]
    roi_payload = fake_ipc.requests[3][1]
    assert roi_payload == {"roi_id": "r1",
                           "shape": {"type": "rect", "x": 0.0, "y": 0.0, "w": 2.0, "h": 2.0}}
    mask_payload = fake_ipc.requests[6][1]
    assert mask_payload == {"shapes": [{"type": "rect", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}]}


def test_bind_and_stop_publish(fake_ipc):
    c = XPCSClient(ipc=fake_ipc)
    c.bind_run("uid1", tiled_url="http://t", tiled_api_key="k")
    c.run_stop("uid1")
    assert fake_ipc.published[0] == ("xpcs.run.bind",
        {"run_uid": "uid1", "tiled_url": "http://t", "tiled_api_key": "k"})
    assert fake_ipc.published[1] == ("xpcs.run.stop", {"run_uid": "uid1"})


def test_status_and_sections(fake_ipc):
    fake_ipc.replies["xpcs.status"] = {"status": "ok", "state": "Idle", "rois": {}}
    fake_ipc.replies["xpcs.sections.get"] = {"status": "ok", "sections": [], "total": 0}
    c = XPCSClient(ipc=fake_ipc)
    assert c.status()["state"] == "Idle"
    assert c.get_sections(from_section=0, limit=10)["total"] == 0
    assert fake_ipc.requests[1][1] == {"from_section": 0, "limit": 10}


def test_event_signals(fake_ipc, qtbot):
    c = XPCSClient(ipc=fake_ipc)
    got = {}
    c.g2Updated.connect(lambda p: got.setdefault("g2", p))
    c.sectionCompleted.connect(lambda p: got.setdefault("sec", p))
    c.stateChanged.connect(lambda p: got.setdefault("state", p))
    c.errorReceived.connect(lambda p: got.setdefault("err", p))
    fake_ipc.emit("xpcs.g2.updated", {"seq": 1})
    fake_ipc.emit("xpcs.section.completed", {"index": 1})
    fake_ipc.emit("xpcs.state", {"state": "Processing"})
    fake_ipc.emit("xpcs.error", {"message": "boom"})
    assert got["g2"]["seq"] == 1
    assert got["sec"]["index"] == 1
    assert got["state"]["state"] == "Processing"
    assert got["err"]["message"] == "boom"


def test_discover(fake_ipc):
    fake_ipc.replies["_xpcs.discover"] = {"prefix": "xpcs", "state": "Idle"}
    c = XPCSClient(ipc=fake_ipc)
    assert c.discover()["prefix"] == "xpcs"


def test_no_ipc_is_safe():
    c = XPCSClient(ipc=None)
    assert c.discover() is None
    assert c.status() is None
    c.enable()  # no raise
```

- [ ] **Step 3: Run, expect FAIL**
- [ ] **Step 4: Implement**

```python
# src/lightfall_endstation_7011/xpcs/client.py
"""Qt-side client for the xpcs-live NATS service.

Wraps lightfall's IPCService: request/reply for actions, subscriptions
(dispatched to the Qt main thread by IPCService) re-emitted as signals.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from .shapes import RectShape


class XPCSClient(QObject):
    g2Updated = Signal(dict)
    sectionCompleted = Signal(dict)
    stateChanged = Signal(dict)
    errorReceived = Signal(dict)

    def __init__(self, ipc=None, parent=None) -> None:
        super().__init__(parent)
        if ipc is None:
            try:
                from lightfall.ipc.service import get_ipc_service
                ipc = get_ipc_service()
            except Exception:
                ipc = None
        self._ipc = ipc
        if self._ipc is not None:
            self._ipc.subscribe("xpcs.g2.updated", self._on_g2)
            self._ipc.subscribe("xpcs.section.completed", self._on_section)
            self._ipc.subscribe("xpcs.state", self._on_state)
            self._ipc.subscribe("xpcs.error", self._on_error)

    # --- incoming events (already on main thread via IPCService) ---

    def _on_g2(self, subject, data, reply):
        self.g2Updated.emit(data)

    def _on_section(self, subject, data, reply):
        self.sectionCompleted.emit(data)

    def _on_state(self, subject, data, reply):
        self.stateChanged.emit(data)

    def _on_error(self, subject, data, reply):
        self.errorReceived.emit(data)

    # --- actions ---

    def _request(self, subject: str, data: dict | None = None, timeout_ms: int = 2000):
        if self._ipc is None:
            return None
        return self._ipc.request(subject, data or {}, timeout_ms=timeout_ms)

    def discover(self):
        if self._ipc is None:
            return None
        return self._ipc.request("_xpcs.discover", {}, timeout_ms=2000)

    def enable(self):
        return self._request("xpcs.processing.enable")

    def disable(self):
        return self._request("xpcs.processing.disable")

    def reset(self):
        return self._request("xpcs.reset")

    def set_roi(self, roi_id: str, shape: RectShape):
        return self._request("xpcs.roi.set", {"roi_id": roi_id, "shape": shape.to_dict()})

    def remove_roi(self, roi_id: str):
        return self._request("xpcs.roi.remove", {"roi_id": roi_id})

    def clear_rois(self):
        return self._request("xpcs.roi.clear")

    def set_mask(self, shapes: list[RectShape]):
        return self._request("xpcs.mask.set", {"shapes": [s.to_dict() for s in shapes]})

    def clear_mask(self):
        return self._request("xpcs.mask.clear")

    def status(self):
        return self._request("xpcs.status")

    def get_sections(self, from_section: int = 0, limit: int = 20):
        return self._request("xpcs.sections.get",
                             {"from_section": from_section, "limit": limit})

    # bind/stop are fire-and-forget publishes (run docs must not block on replies)

    def bind_run(self, run_uid: str, tiled_url: str = "", tiled_api_key=None) -> None:
        if self._ipc is None:
            return
        self._ipc.publish("xpcs.run.bind", {
            "run_uid": run_uid, "tiled_url": tiled_url, "tiled_api_key": tiled_api_key})

    def run_stop(self, run_uid: str) -> None:
        if self._ipc is None:
            return
        self._ipc.publish("xpcs.run.stop", {"run_uid": run_uid})
```

- [ ] **Step 5: Run, expect 6 passed**
- [ ] **Step 6: Commit** — `git commit -m "feat(xpcs): XPCSClient over IPCService (+ FakeIPC fixture)"`

---

### Task 3: RunBindingController

**Files:**
- Create: `src/lightfall_endstation_7011/xpcs/binding.py`
- Test: `tests/xpcs/test_binding.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/xpcs/test_binding.py
from unittest.mock import MagicMock

from lightfall_endstation_7011.xpcs.binding import RunBindingController


def _controller(fake_ipc):
    from lightfall_endstation_7011.xpcs.client import XPCSClient
    client = XPCSClient(ipc=fake_ipc)
    re = MagicMock()
    re.subscribe.return_value = 7  # token
    creds = lambda: ("http://t", "key", None)
    ctl = RunBindingController(client=client, run_engine_getter=lambda: re,
                               credentials_getter=creds)
    return ctl, re, fake_ipc


def test_enable_subscribes_and_calls_backend(fake_ipc):
    fake_ipc.replies["xpcs.processing.enable"] = {"status": "ok"}
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    re.subscribe.assert_called_once()
    assert ("xpcs.processing.enable", {}) in ipc.requests


def test_start_doc_publishes_bind(fake_ipc):
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    callback = re.subscribe.call_args[0][0]
    callback("start", {"uid": "runX"})
    assert ipc.published == [("xpcs.run.bind",
        {"run_uid": "runX", "tiled_url": "http://t", "tiled_api_key": "key"})]


def test_stop_doc_publishes_stop(fake_ipc):
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    callback = re.subscribe.call_args[0][0]
    callback("start", {"uid": "runX"})
    callback("stop", {"run_start": "runX"})
    assert ipc.published[-1] == ("xpcs.run.stop", {"run_uid": "runX"})


def test_disable_unsubscribes_and_calls_backend(fake_ipc):
    fake_ipc.replies["xpcs.processing.disable"] = {"status": "ok"}
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    ctl.disable()
    re.unsubscribe.assert_called_once_with(7)
    assert ("xpcs.processing.disable", {}) in ipc.requests


def test_other_docs_ignored(fake_ipc):
    ctl, re, ipc = _controller(fake_ipc)
    ctl.enable()
    callback = re.subscribe.call_args[0][0]
    callback("descriptor", {"uid": "d1"})
    callback("event", {"uid": "e1"})
    assert ipc.published == []
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**

```python
# src/lightfall_endstation_7011/xpcs/binding.py
"""Run binding: RunEngine subscription -> xpcs.run.bind / xpcs.run.stop.

Every run is bound while enabled; the backend opens its Tiled stream
lazily on first snapshot, so non-XPCS runs stay clean. Credentials follow
the adaptive plan's pattern (TiledService url + SessionManager api key).
"""

from __future__ import annotations

from typing import Callable

from loguru import logger


def _default_run_engine():
    from lightfall.acquire.engine import get_engine
    return get_engine().RE  # reach through the engine wrapper


def _default_credentials():
    """(tiled_url, tiled_api_key, proxy_url) — mirrors
    lightfall.acquire.plans.adaptive._get_tiled_credentials()."""
    tiled_url, api_key, proxy = "", None, None
    try:
        from lightfall.core.services import ServiceRegistry
        from lightfall.services.tiled_service import TiledService
        ts = ServiceRegistry.get_instance().get(TiledService, None)
        if ts and ts.config:
            tiled_url = ts.config.url or ""
    except Exception:
        pass
    try:
        from lightfall.auth.session import SessionManager
        api_key = SessionManager.get_instance().get_api_key("tiled")
    except Exception:
        pass
    return tiled_url, api_key, proxy


class RunBindingController:
    def __init__(
        self,
        client,
        run_engine_getter: Callable = _default_run_engine,
        credentials_getter: Callable = _default_credentials,
    ) -> None:
        self._client = client
        self._get_re = run_engine_getter
        self._get_creds = credentials_getter
        self._token = None
        self._re = None
        self._bound_uid: str | None = None

    @property
    def enabled(self) -> bool:
        return self._token is not None

    def enable(self) -> None:
        if self.enabled:
            return
        self._client.enable()
        self._re = self._get_re()
        self._token = self._re.subscribe(self._on_document)

    def disable(self) -> None:
        if not self.enabled:
            return
        try:
            self._re.unsubscribe(self._token)
        finally:
            self._token = None
            self._re = None
        self._client.disable()

    def _on_document(self, name: str, doc: dict) -> None:
        try:
            if name == "start":
                uid = doc["uid"]
                tiled_url, api_key, _proxy = self._get_creds()
                self._client.bind_run(uid, tiled_url=tiled_url, tiled_api_key=api_key)
                self._bound_uid = uid
            elif name == "stop":
                uid = doc.get("run_start") or self._bound_uid
                if uid:
                    self._client.run_stop(uid)
                self._bound_uid = None
        except Exception as ex:  # never break the RunEngine document stream
            logger.exception(ex)
```

- [ ] **Step 4: Run, expect 5 passed**
- [ ] **Step 5: Commit** — `git commit -m "feat(xpcs): RunBindingController (RE docs -> bind/stop)"`

---

### Task 4: Plot widgets

**Files:**
- Create: `src/lightfall_endstation_7011/xpcs/plots.py`
- Test: `tests/xpcs/test_plots.py`

All four widgets share a tiny base holding a `pg.PlotWidget` and a per-curve `PlotDataItem` registry keyed by curve id, colored by the shared ROI palette. Use `from lightfall.visualization import pg` (themed wrappers).

- [ ] **Step 1: Write the failing tests**

```python
# tests/xpcs/test_plots.py
import numpy as np
import pytest

from lightfall_endstation_7011.xpcs.plots import (
    ROI_COLORS, ConvergencePlot, G2Plot, IntensityPlot, SectionsPlot, color_for,
)


def test_color_cycle_stable():
    assert color_for("average") is None  # theme default
    assert color_for("roi-a", ["roi-a", "roi-b"]) == ROI_COLORS[0]
    assert color_for("roi-b", ["roi-a", "roi-b"]) == ROI_COLORS[1]


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
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**

```python
# src/lightfall_endstation_7011/xpcs/plots.py
"""The four tabbed plots: g2 (log-tau), per-section overlay, I(t), convergence."""

from __future__ import annotations

import numpy as np
from lightfall.visualization import pg
from PySide6.QtWidgets import QVBoxLayout, QWidget

ROI_COLORS = ["#D55E00", "#009E73", "#F0E442", "#0072B2", "#CC79A7"]


def color_for(curve_id: str, roi_order: list[str] | None = None):
    """Stable color per ROI id; None for 'average' (theme default pen)."""
    if curve_id == "average":
        return None
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
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plot)

    def _set_curve(self, curve_id: str, x, y, roi_order=None) -> None:
        if curve_id not in self._curves:
            color = color_for(curve_id, roi_order)
            kwargs = {"name": curve_id}
            if color is not None:
                kwargs["pen"] = pg.mkPen(color, width=2)
            self._curves[curve_id] = self._plot.plot(**kwargs)
        self._curves[curve_id].setData(np.asarray(x, dtype=float),
                                       np.asarray(y, dtype=float))

    def _prune(self, keep: set[str]) -> None:
        for cid in list(self._curves):
            if cid not in keep:
                self._plot.removeItem(self._curves.pop(cid))

    def clear(self) -> None:
        self._prune(set())


class G2Plot(_CurvePlot):
    log_x = True
    x_label = "tau (s)"
    y_label = "g2"

    def update_from_payload(self, payload: dict) -> None:
        tau = payload.get("tau") or []
        g2 = payload.get("g2") or {}
        roi_order = [k for k in g2 if k != "average"]
        for cid, ys in g2.items():
            if len(ys) == len(tau) and tau:
                self._set_curve(cid, tau, ys, roi_order)
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
        curve = self._plot.plot(np.asarray(tau, float), np.asarray(g2, float),
                                pen=pg.mkPen(pg.intColor(hue, 255), width=1),
                                name=f"section {idx}")
        self._section_curves.append(curve)

    def clear(self) -> None:
        for c in self._section_curves:
            self._plot.removeItem(c)
        self._section_curves.clear()


class IntensityPlot(_CurvePlot):
    x_label = "frame"
    y_label = "mean intensity"

    def update_from_payload(self, payload: dict) -> None:
        intensity = payload.get("intensity") or {}
        frames = intensity.get("frame_index") or []
        roi_order = [k for k in intensity if k not in ("frame_index", "average")]
        keep = set()
        for cid, ys in intensity.items():
            if cid == "frame_index":
                continue
            if len(ys) == len(frames) and frames:
                self._set_curve(cid, frames, ys, roi_order)
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
```

- [ ] **Step 4: Run, expect 5 passed** — `python -m pytest tests/xpcs/test_plots.py -v`
- [ ] **Step 5: Commit** — `git commit -m "feat(xpcs): g2/sections/intensity/convergence plot widgets"`

---

### Task 5: ROI overlay manager

**Files:**
- Create: `src/lightfall_endstation_7011/xpcs/roi_overlay.py`
- Test: `tests/xpcs/test_roi_overlay.py`

Manages `pg.RectROI` items on a host `pg.PlotItem` (the image view's plot item). Two flavors: ROIs (sync to backend on release, debounced 300 ms) and mask rects (local until "Apply"). Emits Qt signals; the panel wires them to `XPCSClient`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/xpcs/test_roi_overlay.py
import pyqtgraph as pg
import pytest
from PySide6.QtCore import Qt

from lightfall_endstation_7011.xpcs.roi_overlay import ROIOverlayManager
from lightfall_endstation_7011.xpcs.shapes import RectShape


@pytest.fixture
def host(qtbot):
    w = pg.PlotWidget()
    qtbot.addWidget(w)
    return w.getPlotItem()


def test_add_roi_assigns_id_and_color(host, qtbot):
    mgr = ROIOverlayManager(host, debounce_ms=0)
    rid = mgr.add_roi(RectShape(10, 20, 64, 64))
    assert rid in mgr.rois
    assert len(host.items) > 0


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


def test_mask_rects_local_until_collected(host, qtbot):
    mgr = ROIOverlayManager(host, debounce_ms=0)
    mgr.add_mask_rect(RectShape(0, 0, 4, 4))
    mgr.add_mask_rect(RectShape(10, 10, 4, 4))
    shapes = mgr.mask_shapes()
    assert len(shapes) == 2
    mgr.clear_mask_rects()
    assert mgr.mask_shapes() == []
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**

```python
# src/lightfall_endstation_7011/xpcs/roi_overlay.py
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
```

- [ ] **Step 4: Run, expect 5 passed**
- [ ] **Step 5: Commit** — `git commit -m "feat(xpcs): ROI/mask overlay manager with debounced sync"`

---

### Task 6: XPCSPanel

**Files:**
- Create: `src/lightfall_endstation_7011/xpcs/panel.py`
- Test: `tests/xpcs/test_panel.py`

Layout: `QSplitter` — left: image area (`OphydImageView` when a device resolves, else a placeholder label) + control row; right: `QTabWidget` with the four plots; bottom strip: state label, frames/buffer label, file path label.

Image device resolution: `DeviceCatalog.get_instance().get_device_by_name(name).ophyd_device` (see `lightfall/src/lightfall/plugins/agents/device_tools.py:43-47,223`), device name from panel constructor arg `detector_device_name` (default `"andor"` — confirm the actual catalog name at integration time; it's a constructor arg precisely so the plugin can pass the right one). `OphydImageView` is at `lightfall/src/lightfall/ui/widgets/camera/image_view.py`, constructor `OphydImageView(ophyd_device, parent=None)`, overlay host attribute `_plot_item` (a `pg.PlotItem`).

For unit tests, everything injects: `XPCSPanel(client=..., binding=..., image_widget_factory=...)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/xpcs/test_panel.py
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


def test_section_event_feeds_sections_plot(panel):
    panel.test_ipc.emit("xpcs.section.completed",
                        {"index": 1, "tau": [1, 2], "g2": {"average": [1.5, 1.0]}})
    assert len(panel._sections_plot._section_curves) == 1


def test_state_event_updates_label(panel):
    panel.test_ipc.emit("xpcs.state", {"state": "Processing", "run_uid": "u1"})
    assert "Processing" in panel._state_label.text()


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
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement**

```python
# src/lightfall_endstation_7011/xpcs/panel.py
"""XPCS live panel: measurement progress / quality / doneness instrument."""

from __future__ import annotations

from typing import Callable, ClassVar

from loguru import logger
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)
from PySide6.QtCore import Qt

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
    Returns (widget, overlay_plot_item) or a placeholder on failure."""
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
    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lightfall_endstation_7011.panels.xpcs",
        name="XPCS Live",
        description="Live XPCS g2 correlation: measurement progress, quality, convergence",
        icon="chart-scatter",
        category="Analysis",
        singleton=True,
        closable=True,
        keywords=["xpcs", "g2", "correlation", "live"],
        default_area="center",
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
        self._client = client or XPCSClient()
        self._binding = binding or RunBindingController(client=self._client)
        self._image_factory = image_widget_factory or (
            lambda: _default_image_factory(detector_device_name))
        super().__init__(parent)
        self._connect_client()
        self.resync()

    # BasePanel calls this during __init__
    def _setup_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal)

        # left: image + controls
        left = QWidget()
        left_layout = QVBoxLayout(left)
        image_widget, plot_item = self._image_factory()
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

        # bottom: stats strip
        stats_row = QHBoxLayout()
        self._state_label = QLabel("State: —")
        self._stats_label = QLabel("Frames: 0")
        self._file_label = QLabel("File: —")
        self._error_label = QLabel("")
        self._error_label.setStyleSheet("color: #d9534f;")
        for w in (self._state_label, self._stats_label, self._file_label):
            stats_row.addWidget(w)
        stats_row.addStretch()
        stats_row.addWidget(self._error_label)

        self._layout.addWidget(splitter)
        self._layout.addLayout(stats_row)

    def _connect_client(self) -> None:
        self._client.g2Updated.connect(self._on_g2_updated)
        self._client.sectionCompleted.connect(self._sections_plot.add_section)
        self._client.stateChanged.connect(self._on_state_changed)
        self._client.errorReceived.connect(self._on_error)
        self._roi_overlay.roiChanged.connect(self._client.set_roi)
        self._roi_overlay.roiRemoved.connect(self._client.remove_roi)

    # --- event handlers ---

    def _on_g2_updated(self, payload: dict) -> None:
        self._g2_plot.update_from_payload(payload)
        self._intensity_plot.update_from_payload(payload)
        self._convergence_plot.update_from_payload(payload)
        frames = payload.get("frames_count", 0)
        buf = payload.get("buffer_size", 0)
        self._stats_label.setText(f"Frames: {frames}  Buffer: {buf}")
        path = payload.get("file_path")
        if path:
            self._file_label.setText(f"File: {path}")

    def _on_state_changed(self, payload: dict) -> None:
        self._state_label.setText(f"State: {payload.get('state', '?')}")

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
                if not page or not page.get("sections"):
                    break
                for sec in page["sections"]:
                    self._sections_plot.add_section(sec)
                fetched += len(page["sections"])
```

- [ ] **Step 4: Run, expect 9 passed** — `python -m pytest tests/xpcs/test_panel.py -v`
- [ ] **Step 5: Run the whole xpcs test dir** — `python -m pytest tests/xpcs -v`
- [ ] **Step 6: Commit** — `git commit -m "feat(xpcs): XPCSPanel — image+ROIs, tabbed plots, stats, resync"`

---

### Task 7: Plugin + manifest registration

**Files:**
- Create: `src/lightfall_endstation_7011/xpcs/plugin.py`
- Modify: `src/lightfall_endstation_7011/manifest.py`
- Test: extend `tests/xpcs/test_panel.py` (new test file section) → `tests/xpcs/test_plugin.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/xpcs/test_plugin.py
from lightfall_endstation_7011.manifest import manifest
from lightfall_endstation_7011.xpcs.plugin import XPCSPanelPlugin


def test_plugin_provides_panel_class():
    plugin = XPCSPanelPlugin()
    assert plugin.name == "xpcs"
    cls = plugin.get_panel_class()
    assert cls.panel_metadata.id == "lightfall_endstation_7011.panels.xpcs"


def test_manifest_contains_panel_entry():
    entries = [p for p in manifest.plugins if p.type_name == "panel" and p.name == "xpcs"]
    assert len(entries) == 1
    assert entries[0].import_path == (
        "lightfall_endstation_7011.xpcs.plugin:XPCSPanelPlugin")
```

- [ ] **Step 2: Run, expect FAIL**
- [ ] **Step 3: Implement plugin**

```python
# src/lightfall_endstation_7011/xpcs/plugin.py
"""PanelPlugin registration for the XPCS live panel."""

from __future__ import annotations

from lightfall.plugins.panel_plugin import PanelPlugin


class XPCSPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "xpcs"

    @property
    def description(self) -> str:
        return "Live XPCS g2 panel for the xpcs-live correlator service"

    def get_panel_class(self):
        from lightfall_endstation_7011.xpcs.panel import XPCSPanel
        return XPCSPanel
```

- [ ] **Step 4: Add to manifest.py** — append to the `plugins=[...]` list:

```python
        # Panel plugin: live XPCS g2 / measurement-progress panel
        PluginEntry(
            type_name="panel",
            name="xpcs",
            import_path="lightfall_endstation_7011.xpcs.plugin:XPCSPanelPlugin",
        ),
```

(If `PanelPlugin.description` is not abstract in lightfall's base, the property is harmless; if `PluginEntry` requires `metadata`, match the existing entries' style.)

- [ ] **Step 5: Run, expect 2 passed; then full suite** — `python -m pytest tests/ -v`
- [ ] **Step 6: Commit** — `git commit -m "feat(xpcs): register XPCSPanelPlugin in manifest"`

---

### Task 8: Live verification checklist (manual, with backend running)

No code — a structured smoke test once both sides exist. Record outcomes in the PR description.

- [ ] **Step 1:** Start a local NATS broker (`nats-server`) or use bcgnats; start the backend: `xpcs-live run --nats-url nats://localhost:4222 --lightfall-prefix als.7011` (on the GPU box for real frames, or anywhere with the FakeCorrelator-style test source for plumbing).
- [ ] **Step 2:** Launch Lightfall with this plugin installed; open the XPCS Live panel. Confirm: state shows `Disabled`, discover/status resync works (no "backend not found").
- [ ] **Step 3:** Toggle *Enable Processing* → backend state `Idle`; run any bluesky plan → `xpcs.run.bind` fires (backend log), state `Processing`.
- [ ] **Step 4:** Add an ROI, drag it → backend status echoes new geometry; g2 tab grows a colored curve matching the box color.
- [ ] **Step 5:** Let a section boundary pass → Sections tab gains an overlay curve; I(t) and Convergence tabs populate.
- [ ] **Step 6:** Stop the run → final snapshot in Tiled (`run[uid]/xpcs/snapshot_*` with `final: true`); sections present as `section_*`.
- [ ] **Step 7:** Close + reopen the panel mid-processing → ROIs and sections rebuild from resync.
- [ ] **Step 8:** Kill Tiled (or use a bad URL) mid-run → live view keeps updating; `xpcs.error` shows in the panel's error label.

---

## Self-review checklist (run after writing code, before PR)

- Spec coverage: image-PV view ✓ (Task 6), ROI/mask geometric sync ✓ (5/6), g2/sections/I(t)/convergence plots ✓ (4), inline multitau payload consumption ✓ (4/6), run binding via RE subscription ✓ (3), resync ✓ (6), error surfacing ✓ (6), manifest registration ✓ (7).
- Out of scope here (backend plan): rasterization, Tiled record, NATS service.
- Known integration-time decisions: actual detector device name in the catalog (constructor arg), `OphydImageView._plot_item` attribute name (verify at Task 6 step 3 against the installed lightfall).
