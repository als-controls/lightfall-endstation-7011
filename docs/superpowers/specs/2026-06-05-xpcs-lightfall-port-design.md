# XPCS Live → Lightfall Port — Design

**Date:** 2026-06-05
**Status:** Approved (pending implementation plan)
**Repos:** `lbl-camera/xpcs_live` (backend rescope), `lightfall-endstation-7011` (frontend panel)

## Context

`xpcs_live` is a CUDA streaming g2 correlator (incremental GPU computation, EPICS
PV watcher feeding SWMR HDF5 frames). Its existing frontend, `g2viewer`
(Bokeh/Panel), outgrew its platform: 10 s polling, callback/threading
workarounds, fragile multi-session sync. The backend is the valuable part.

This design ports the system into the Lightfall ecosystem, following the
Tsuchinoko rescope precedent (`tsuchinoko/docs/design/2026-04-12-tsuchinoko-rescope.md`):
headless NATS service + Tiled record + Qt panel in Lightfall.

**Baseline:** the rescope builds on xpcs_live PR #11 (`states` branch — the
buffered CUDA `Correlator` with UUID-tracked ROIs, `device_info.py`, tests).
PR #11 is merged to master as a standalone precursor step so the rescope diff
stays reviewable.

## Goals

- g2viewer feature parity: live image, draggable rect ROIs, rect masks with
  deferred apply, multi-ROI g2 plot (log-τ), stats readout.
- Plus: surface `metrics.py` stability metrics (never exposed by g2viewer).
- Processed data recorded to Tiled in the bound bluesky run, Tsuchinoko-style.
- Processing enabled/disabled by Lightfall; runs bound via RunEngine subscription.

## Non-Goals

- Two-time correlation (doesn't exist in the correlator).
- True multitau *computation* in the correlator (intensity averaging at long
  lags); only multitau *rebinning* of the linear-τ result for display.
- Replay visualization widget (`BaseVisualization`) for recorded xpcs streams —
  the Tiled record exists regardless; the replay viewer is future work.
- q-bin/calibration-derived masks (geometric JSON has room to grow; the
  `xpcs.mask.set` file-path form covers bad-pixel maps).

## Architecture

```
                         ┌─────────────────────────────────────────┐
                         │ Lightfall (GUI process)                 │
  EPICS image PV ───────▶│  XPCSPanel (endstation-7011)            │
                         │   ├─ live image: existing PV widget     │
                         │   ├─ ROI/mask overlays (pyqtgraph)      │
                         │   ├─ g2 plot + metrics + stats          │
                         │  RunEngine subscription ──┐             │
                         └───────────┬───────────────┼─────────────┘
                                     │ NATS (JSON)   │ bind/stop on
                              xpcs.* actions/events  │ start/stop docs
                         ┌───────────▼───────────────▼─────────────┐
  areaDetector ──HDF5──▶ │ xpcs-live service (headless, GPU box)   │
  FilePath PV ──watch──▶ │  PV watcher → g2Thread → Correlator     │
                         │  NATS service (xpcs.*) │ TiledPublisher │
                         └─────────────────────────┬───────────────┘
                                                   │ HTTP
                                              Tiled ("xpcs" stream
                                              in the bound run)
```

Three data paths, each on the transport suited to it:

1. **Live image** — the panel reads the areaDetector image PV directly via the
   existing Lightfall image widget. The backend never transports images.
   (NATS is JSON-only control per Lightfall convention; 16 MB frames don't fit.)
2. **Live g2** — the service publishes `xpcs.g2.updated` events with curve data
   **inline** at ~1–2 Hz, multitau-rebinned (below). Curves are small at
   display resolution; no Tiled round-trip latency.
3. **Record** — full-resolution g2/τ/metrics snapshots written to an `xpcs`
   stream in the bound bluesky run, throttled + final snapshot at run stop.

## Backend: xpcs_live rescope

### Repo changes

| Added | Kept | Dropped |
|---|---|---|
| `xpcs_live/nats/` (config, client, service — modeled on `tsuchinoko/nats/`) | `buffer_cuda_g2.py` (Correlator) | ZMQ server in `live_processor.py` |
| `xpcs_live/tiled/` (connect, writer) | PV watcher + `g2Thread` frame loop (extracted from ZMQ plumbing) | `widgets/` (PySide6 UI) |
| CLI: `xpcs-live run --nats-url … --lightfall-prefix als.7011 --config config.yml` (click + pydantic `AppConfig`, like Tsuchinoko) | `metrics.py`, `device_info.py`, tests | `xicam_plugins/` |
| | | `pyzmq`, `pyside6` deps |

On startup the service performs the Lightfall auth handshake
(`{prefix}.auth.request`) to learn the Tiled URL; per-run credentials arrive
with each `xpcs.run.bind` (aligned with the auth-v2 short-TTL direction).

### NATS interface

Namespace `xpcs.*`; catalog endpoints `_xpcs.discover`, `xpcs.meta.actions`,
`xpcs.meta.events` (same discovery convention as Tsuchinoko, so a future
NATS-MCP bridge picks both up identically).

**Actions (request/reply):**

| Subject | Payload → Reply |
|---|---|
| `xpcs.processing.enable` / `.disable` | `{}` → `{status, state}` |
| `xpcs.run.bind` | `{run_uid, tiled_url, tiled_api_key}` → `{status, run_uid}` — resets correlator, arms `xpcs` stream writer |
| `xpcs.run.stop` | `{run_uid}` → `{status}` — final snapshot, close stream |
| `xpcs.reset` | `{}` → `{status}` — clear accumulators mid-run |
| `xpcs.roi.set` | `{roi_id, shape: {type: "rect", x, y, w, h}}` → `{status}` |
| `xpcs.roi.remove` | `{roi_id}` → `{status}` |
| `xpcs.roi.clear` | `{}` → `{status}` |
| `xpcs.mask.set` | `{shapes: [rect…]}` or `{path: "<bad-pixel file>"}` → `{status}` |
| `xpcs.mask.clear` | `{}` → `{status}` |
| `xpcs.status` | `{}` → `{state, frames_count, buffer_size, file_path, pv, run_uid, rois, mask}` — ROIs/mask echoed as geometry for panel resync |

ROIs and masks travel as geometric JSON; the backend rasterizes to boolean
arrays internally. No pixel arrays cross NATS. Mask shapes mark regions to
**exclude**: the backend ORs them together and inverts to produce the
include-mask the correlator expects (g2viewer's semantics). A file-path mask
is used as-is (True = include).

**Events (pub):**

| Subject | Payload |
|---|---|
| `xpcs.state` | `{state, run_uid}` on every transition |
| `xpcs.g2.updated` | `{run_uid, frames_count, buffer_size, file_path, tau: […], g2: {average: […], <roi_id>: […]}, metrics: {…}, seq}` at ~1–2 Hz while processing; τ/g2 multitau-rebinned |
| `xpcs.error` | `{message, traceback}` |

### Multitau rebinning (inline payload)

g2 length grows as `frames/2`, so a 100 k-frame run would be ~400 kB/curve at
full resolution — over NATS limits with several ROIs. The inline payload uses
the canonical multitau structure: fixed points per level `m` (default 16), bin
width doubling each level; τ spacing linear within a level, dyadic across
levels. τ = bin mean, g2 = bin mean. Point count is ~`m·log2(N)` regardless of
run length (100 k frames → ~260 points). The correlator itself still computes
full linear-τ g2; rebinning is display-side packaging only. The Tiled record
stays full resolution.

### Service states

`Disabled → Idle → Processing` (+ `Errored`, recoverable via reset/disable).

- **Idle** (enabled, unbound): PV watcher sees files but frames are *not* fed
  to the correlator — acquisition always goes through Lightfall, so a file
  without a bind is noise; skipping avoids ambiguous unbound output.
- Bind and the file PV may race in either order; processing starts when both
  are present.
- Each `xpcs.run.bind` resets the correlator (new run = fresh accumulation).
  ROIs and mask persist across runs and resets (matches existing semantics).

### Tiled record

Written into the bound run alongside `primary`, via the
`bluesky_tiled_plugins` `_RunWriter` (same machinery as Tsuchinoko's
`TiledPublisher`):

```
run[run_uid]/
├── primary/          (Lightfall's acquisition)
└── xpcs/
    ├── config: ROI geometry, mask spec, detector PV, correlator params
    ├── snapshot_001/
    │   ├── tau (n,), g2_average (n,), g2_roi_<id> (n,)
    │   ├── metrics_* (per-ROI stability metrics)
    │   └── frames_count
    └── snapshot_NNN/
```

- Snapshots every `snapshot_interval` (default 60 s) + always one final at
  `xpcs.run.stop`.
- Per-snapshot sub-containers are immutable; shapes vary freely between
  snapshots (g2 grows) — same rationale as Tsuchinoko's `iter_NNN`.
- The `xpcs` stream is opened **lazily on the first snapshot**, so bound runs
  that never produce XPCS frames get no stream pollution.

## Frontend: lightfall-endstation-7011

New package `src/lightfall_endstation_7011/xpcs/`:

- `panel.py` — `XPCSPanel(BasePanel)`, metadata id
  `lightfall_endstation_7011.panels.xpcs`
- `plugin.py` — `XPCSPanelPlugin(PanelPlugin)`, registered in `manifest.py` as
  `PluginEntry(type_name="panel", name="xpcs", …)`
- `binding.py` — run-binding controller

### Run binding

The panel's *Enable Processing* toggle drives everything:

- **Enable:** call `xpcs.processing.enable`, subscribe to the RunEngine
  (`get_engine().RE.subscribe` — reach through the engine wrapper). On each
  start doc, publish `xpcs.run.bind` with `{run_uid, tiled_url, tiled_api_key}`
  (credentials pulled from Lightfall's Tiled/auth services, as the adaptive
  plan does). On each stop doc, `xpcs.run.stop`.
- **Disable:** unsubscribe + `xpcs.processing.disable`.
- Every run is bound while enabled (no detector filtering) — lazy stream
  opening makes this harmless.

### Panel layout

- **Left — live image:** existing image-PV viewer (the widget the Andor
  controller uses; exact class chosen during planning), overlaid with
  pyqtgraph `RectROI` items color-cycled per ROI, plus mask rects. ROI release
  (`sigRegionChangeFinished`) → short QTimer debounce → `xpcs.roi.set`.
- **Masks:** deferred-apply semantics — edit locally, *Apply Mask* sends
  `xpcs.mask.set` (mask changes trigger GPU buffer reallocation, so they stay
  an explicit action).
- **Right — g2 plot:** themed `pg.PlotWidget`, log-τ axis, one curve per ROI +
  average, curve colors matching ROI overlay colors. Fed directly from
  `xpcs.g2.updated` payloads.
- **Below — metrics + stats:** per-ROI stability badges from the `metrics`
  payload; state, frame count, buffer-fill bar, current file path. Controls:
  Enable toggle, Reset, Add ROI, Clear ROIs, Add/Apply/Clear Mask.

NATS access via `lightfall.ipc.service.get_ipc_service()`; subscriptions with
`main_thread=True` so payload handling lands on the Qt main thread.

### Session resync

On panel open or service reconnect: `_xpcs.discover` → `xpcs.status`; the
echoed ROI/mask geometry rebuilds the overlays. Panel restarts and service
restarts both recover cleanly — the backend is the single source of truth,
replacing g2viewer's fragile multi-session sync.

## Error handling

- `xpcs.error` events → non-blocking notification + status line in the panel.
- IPC disconnect (`sigConnectionChanged`) → controls disabled + banner.
- `xpcs.run.bind` with no responder → warn the user that processing isn't
  recording.
- Backend Tiled write failure → log, emit `xpcs.error`, **keep correlating** —
  a flaky Tiled never kills a run's live view.
- GPU/correlator exception → `Errored` state + `xpcs.error`; recoverable via
  reset or disable/enable.

## Testing

**Backend (xpcs_live), following Tsuchinoko's test layout:**

- Rect rasterization (geometry → boolean array) unit tests.
- Multitau rebinning: point count, monotonic τ, mean preservation per bin.
- NATS service handlers against a fake connection.
- `TiledPublisher` against a temp Tiled server (lazy stream open, snapshot
  shape variation, final-snapshot-on-stop).

**Frontend (endstation-7011):**

- pytest-qt with a stubbed `IPCService` injecting `g2.updated` payloads.
- ROI geometry serialization (RectROI state → JSON → backend shape).
- Binding controller against synthetic start/stop documents.

Both repos: run tests with the project venv python, never bare `pytest`.

## Implementation order

1. Merge xpcs_live PR #11 (`states` → master).
2. Backend rescope on top: NATS service + Tiled writer + CLI; drop ZMQ/Qt/Xi-CAM.
3. Frontend panel in endstation-7011.
4. End-to-end against a live (or simulated-PV) backend.

## Resolved decisions

| Question | Decision |
|---|---|
| Run correlation | Bind to Lightfall bluesky runs; bind ids pushed by a RunEngine subscription while processing is enabled |
| Live image transport | Direct EPICS image PV via existing widgets; never via backend |
| ROI/mask protocol | Geometric JSON, backend rasterizes; file-path form for bad-pixel masks |
| Live g2 path | Inline in NATS events (multitau-rebinned); Tiled is the full-res record |
| Inline binning | Multitau (m=16/level, dyadic), not generic log-binning |
| Scope | g2viewer parity + stability metrics; no replay viz widget yet |
| Backend approach | Rescope xpcs_live in place (Tsuchinoko-style); drop ZMQ — no remaining consumers |
| Baseline | PR #11 (`states` branch) merged first |
