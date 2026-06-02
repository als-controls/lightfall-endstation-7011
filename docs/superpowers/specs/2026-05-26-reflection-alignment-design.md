# Reflection Alignment Skill — Design

**Date:** 2026-05-26
**Status:** Approved (design); pending implementation plan
**Repos touched:** `lightfall-endstation-7011` (primary), `ncs/ncs` (one small core tool)

## Problem

Operators at ALS beamline 7.0.1.1 align a sample in reflection geometry by
hand: center the sample at the beam using the chamber camera, then iterate
knife-edge (lift) and rocking-curve (theta) scans against a detector diode,
fitting each scan and stepping the motor to the fitted position, until the
lift and theta positions stop changing. This is repetitive, requires
judgement about whether a scan actually shows a feature, and is exactly the
kind of bounded numerical loop an embedded agent can drive — provided the
numerical decisions are made by tested code, not by an LLM eyeballing data.

Procedure as provided by Sophie (the alignment expert):

1. Use video to place the estimated sample center/area-of-interest at the
   known beam position (chamber YAG viewing crystal).
2. Move `sample_rotate_steppertheta` to 0.
3. Set detector diode sensitivity to 5 µA/V (manual knob).
4. Coarse lift scan: diode vs `sample_lift`, ±500 µm, step 50 µm. Fit the
   step; move lift to the half-cut (50%) position.
5. Fine lift scan: ±100 µm, step 10 µm. Fit; move to half-cut.
6. Theta scan: diode vs `sample_rotate_steppertheta`, ±5°, step 0.25°. Fit
   the peak; move theta to the peak.
7. Finer lift scan: ±50 µm, step 5 µm. Fit; move to half-cut.
8. Theta scan: ±5°, step 0.25°. Fit the peak; move to peak.
9. Repeat the alternating lift/theta refinement until the optimized theta and
   lift positions converge (threshold: 10 µm lift, 0.25° theta).

Beam sanity (diode current): below 500 nA → no beam; ~15000 nA → beam present.

## Decisions (resolved during brainstorming)

- **Lift fit:** single **falling** edge (diode signal drops as the sample
  rises into the beam). Half-cut target = the 50%-of-step-height midpoint.
- **No-feature handling:** when a scan shows no clear step/peak, **pause and
  hand control back to the operator** (report, optionally display the run).
  Do not auto-widen or silently abort.
- **Beam gate:** **hard** check before each scan — refuse to scan when
  `DetectorDiodeCurrent` reads < 500 nA.
- **Loop shape:** the coarse ±500 µm lift scan runs **once** at the start to
  get into range; thereafter alternate **fine lift → theta**, tightening the
  lift range on later passes, until convergence.
- **Convergence definition:** stop when **two consecutive cycle-to-cycle
  comparisons** both agree within threshold (i.e. three cycles whose lift and
  theta positions all fall within 10 µm / 0.25° of their predecessor). This is
  the conservative reading of "converge twice to the same."
- **Dwell:** the diode reads instantaneously, so scans use plain `rel_scan`
  with no added per-point dwell/settle.

## Device names (runtime DeviceCatalog)

| Role        | Catalog name                      | Notes                          |
|-------------|-----------------------------------|--------------------------------|
| Lift motor  | `sample_lift`                     | knife-edge axis (µm)           |
| Theta motor | `sample_rotate_steppertheta`      | rocking axis (deg)             |
| Diode       | `DetectorDiodeCurrent`            | reads in **nA**, instantaneous |
| Video       | Blackfly **Chamber Cam**          | existing observer in package   |

The skill resolves these by name at runtime and confirms with the operator if
a name is missing or ambiguous; the names above are the defaults baked into
the system prompt.

## Architecture (Approach C: deterministic core + agent orchestration)

Numerical truth lives in pure, unit-tested functions. Sequencing,
human checkpoints, and hardware I/O live in the agent, which calls thin MCP
tools that wrap the pure core and the existing Lightfall run/data/move
infrastructure.

```
src/lightfall_endstation_7011/alignment/
  __init__.py
  fitting.py        # pure: fit_falling_edge_halfcut(x, y) -> EdgeFit
                    #       fit_peak(x, y)               -> PeakFit
  convergence.py    # pure: ConvergenceTracker
  skill.py          # ReflectionAlignmentAgent(AgentPlugin) + MCP tools
  references/
    procedure.md    # Sophie's procedure, embedded for the agent to read
tests/alignment/
  test_fitting.py
  test_convergence.py
  test_skill.py
```

A new manifest entry registers the agent:

```python
PluginEntry(
    type_name="agent",
    name="reflection_alignment",
    import_path="lightfall_endstation_7011.alignment.skill:ReflectionAlignmentAgent",
    metadata={"priority": 30},
)
```

### D1 — core tool (ncs/ncs): `ncs_get_beam_status`

A thin MCP tool wrapping the already-existing
`lightfall.services.als_beam_status.ALSBeamStatusService`, which polls
`https://controls.als.lbl.gov/als-beamstatus/curvals` and already exposes a
`get_introspection_data()` method built for exactly this. The tool:

- calls `ALSBeamStatusService.get_instance()`,
- optional `force_refresh` arg → `force_refresh()` before reading,
- returns `get_introspection_data()` (ring current mA, beam_available,
  energy GeV, lifetime hours, x/y RMS µm, ops comment, timestamp,
  connection/error state).

Added to the existing `EngineToolsAgent` in
`lightfall/plugins/agents/engine_tools.py` (acquisition category). No new plugin
registration. Generally useful beyond alignment; the alignment skill uses it
to explain *why* a beam gate failed (ring dump vs shutter closed vs
mis-centering).

## Control flow (the agent's procedure)

1. **Pre-flight (manual, chat-confirmed):**
   - Operator centers the sample at the beam via the Blackfly Chamber Cam.
   - Operator confirms diode sensitivity = 5 µA/V.
   - Agent moves `sample_rotate_steppertheta` to 0.
2. **Beam gate:** read `DetectorDiodeCurrent`; if < 500 nA, pause, report, and
   call `ncs_get_beam_status` for context. Re-gate before every scan.
3. **Coarse lift (once):** `rel_scan([DetectorDiodeCurrent], sample_lift,
   -500, 500, 21)` → `fit_lift_halfcut(uid)` → if `detected`, move `sample_lift`
   to half-cut; else pause.
4. **Fine lift:** `rel_scan(..., sample_lift, -100, 100, 21)` → fit → move.
5. **Theta:** `rel_scan(..., sample_rotate_steppertheta, -5, 5, 41)` →
   `fit_theta_peak(uid)` → move to peak.
6. **Record cycle** `(lift, theta)`; loop **steps 4 → 5** with the lift range
   tightened to ±50 µm / step 5 µm on subsequent passes, feeding each cycle's
   positions to `ConvergenceTracker`, until it reports converged.
7. **Report** final lift/theta positions and the per-cycle history.

Scans reuse the existing registry plan `rel_scan` via `ncs_run_plan`. The
agent waits on `ncs_get_run_status` until idle, fetches the run `uid`
(`ncs_get_last_run`), and the fit tools read the scan via the existing Tiled
path (`ncs_get_scan_data` / `read_events`). Motor moves use the existing
`ncs_move_motor` (DEVICE_CONTROL-gated).

## Fitting math

Both fits use `scipy.optimize.curve_fit` with initial guesses derived from the
data, and return a dataclass carrying the fitted position, shape parameters,
goodness-of-fit, a boolean `detected`, and a human-readable `reason` when not
detected.

**Falling edge (lift):**

```
y(x) = floor + (baseline - floor) * 0.5 * (1 - erf((x - x0) / (sqrt(2) * w)))
```

- Half-cut position = `x0`.
- `detected` requires: `(baseline - floor) > k * noise` (default k = 5,
  noise = residual std of a flat reference / robust MAD of y),
  `r2 >= r_min` (default 0.9), and `x0` within the scanned range.

**Peak (theta):**

```
y(x) = bg + A * exp(-(x - x0)**2 / (2 * sigma**2))
```

- Peak position = `x0`.
- `detected` requires: `A > k * noise`, `r2 >= r_min`, and `x0` within range.

Initial guesses: edge `x0` from the steepest-gradient point, `w` from the
10–90% span; peak `x0` from argmax, `A` from peak-minus-baseline, `sigma` from
the half-width above half-max. Fits that fail to converge return
`detected = False` with the reason, rather than raising.

## Convergence

`ConvergenceTracker(lift_tol=10.0, theta_tol=0.25, stable_required=2)`:

- `record(lift, theta)` appends a cycle.
- A pairwise comparison "agrees" when `abs(d_lift) <= lift_tol` **and**
  `abs(d_theta) <= theta_tol`.
- `converged` is True once the most recent `stable_required` consecutive
  pairwise comparisons all agree (default 2 ⇒ three cycles within tolerance).
- Exposes the full history for the final report.

Pure, no I/O.

## MCP tools (skill.py)

- `check_beam()` → `{ current_nA, beam_present }` (reads `DetectorDiodeCurrent`).
- `fit_lift_halfcut(uid, x_field?, y_field?)` → reads scan, runs
  `fit_falling_edge_halfcut`, returns `{ detected, halfcut, diagnostics }`.
  Field names default from run metadata (motor = x, diode = y).
- `fit_theta_peak(uid, x_field?, y_field?)` → as above with `fit_peak`.

The system prompt (and `references/procedure.md`) encode the procedure,
device-name defaults, beam-gate rule, loop shape, convergence rule, and the
"pause on no-feature / failed gate" policy. The agent reuses existing tools
for running scans, polling, reading runs, moving motors, and showing runs.

## Human checkpoints

Video centering and the 5 µA/V sensitivity setting are **manual pre-flight**
confirmations requested in chat (not RunEngine pauses). On any
`detected = false`, failed beam gate, motor-limit error, or scan-submit
failure, the agent **stops the loop, reports, and hands back to the operator**,
optionally calling `ncs_show_run` so the operator can eyeball the scan.

## Error handling

- No feature detected → pause + report + (optional) show run.
- Beam gate fail (< 500 nA) → pause + report + ring/shutter status.
- Motor move outside limits / scan submission failure → stop the loop, report,
  leave hardware where it is.
- Max-cycle cap (default 6 refinement cycles) prevents an unbounded loop.

## Testing

- `test_fitting.py`: synthetic erf edge and Gaussian peak with added noise;
  flat / no-feature signals; feature near a scan boundary; an edge with the
  wrong (rising) polarity → assert `detected` correctness, fitted position
  accuracy within tolerance, and graceful failure on non-convergence.
- `test_convergence.py`: sequences that converge, oscillate, drift, and never
  converge; off-by-one around `stable_required`.
- `test_skill.py`: `AgentPlugin` surface (name, display_name, category,
  priority, system prompt non-empty, tool list) mirroring the Blackfly
  `test_skill.py`; tool callables exercised against a fake catalog / fake
  Tiled run where practical.
- D1: a focused test that `ncs_get_beam_status` wraps the service singleton
  and returns its introspection payload (service mocked).

## Dependencies

- Add `scipy>=1.10` to `lightfall-endstation-7011` runtime dependencies (used by
  `fitting.py` for `curve_fit`; `scipy.special.erf` for the edge model).

## Out of scope

- New scan PlanPlugins — the existing `rel_scan` covers all scans here.
- Automatic re-centering via the camera — the video step stays manual.
- Persisting alignment results to the logbook (possible follow-up).
