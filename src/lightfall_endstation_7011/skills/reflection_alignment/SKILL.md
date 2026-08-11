---
name: reflection-alignment
description: "Knife-edge + rocking-curve alignment of a sample in reflection geometry"
---

## Reflection Alignment Skill

Use this skill when the user asks to align a sample in reflection geometry on
the 7.0.1.1 endstation, or mentions a reflection alignment, a knife-edge +
rocking-curve alignment, or aligning sample lift and theta against the
detector diode.

### Canonical devices (use these names EXACTLY — no substitutes)
- Lift motor:    `sample_lift` (microns)
- Theta motor:   `sample_rotate_steppertheta` (degrees)
- Held axis:     `sample_translate` (microns) — mechanically coupled to
                 `sample_lift`; see "Coupled motors" below.
- Detector:      `DetectorDiodeCurrent` (nA, instantaneous). This is the
                 ONLY detector for every alignment scan. `check_beam` reads
                 this exact device and echoes its name in the response.
                 Never use a different diode (e.g. `DIAG112_Diode`) for the
                 scan even if it appears in the catalog.
- Video:         Blackfly Chamber Cam live view (manual centering only)

If a name is missing from the catalog (`ncs_list_devices`), ask the operator
which device to use before proceeding.

### Coupled motors — REQUIRED reading
`sample_lift` is mechanically coupled to `sample_translate`: large lift
moves drift translate by tens to hundreds of microns. A plain `bp.scan` or
`bp.rel_scan` over `sample_lift` will silently let `sample_translate` drift,
which corrupts every halfcut fit downstream (this happened in the original
run — see issue 2).

Use the per-step plan template under "Running scans" for every lift scan.
Theta scans use plain `rel_scan_1d` (no coupling).

### Tools this skill provides
- `check_beam()` -> {detector, current_nA, beam_present, threshold_nA}.
  Beam present at >= 500 nA; ~15000 nA is healthy; below 500 nA means no
  usable beam. Call before every alignment scan.
- `fit_lift_halfcut(uid)` -> fits a falling edge and returns the half-cut
  (50%-of-step) position. Returns {detected, halfcut, r2, reason, ...}.
  When detected=true AND r2 >= 0.9, move `sample_lift` to halfcut. When
  detected=false OR r2 < 0.5 the halfcut field is null — never move.
- `fit_theta_peak(uid)` -> {detected, peak, r2, method, peak_at_boundary,
  reason, ...}. method is "gaussian" | "voigt" | "asymmetric_gaussian" |
  "centroid_topN" | "argmax" | "none" — the algorithm that produced the
  position. If `peak_at_boundary` is true, the response is monotonic or the
  peak pegged to the scan edge; WIDEN the scan, do not move.
- `check_convergence(cycles)` -> {converged, ...}. Pass the full ordered
  list of per-cycle {lift, theta}; returns whether the loop has converged.
- `quick_read()` -> ad-hoc trigger-and-read of `DetectorDiodeCurrent`
  wrapped in a run. Use for sanity checks between scans (no need to compose
  a Bluesky plan yourself).
- `plot_alignment_scan(uid, kind="auto")` -> draw the diode-vs-motor curve
  with fit overlay and halfcut/peak marker. Call after every fit.
- `plot_convergence(cycles)` -> draw lift and theta vs cycle with
  tolerance bands. Call after each cycle to give the operator a visual
  status.

### Running scans

#### Theta scans (no coupling)
Use the registered relative 1D scan plan `rel_scan_1d` via `ncs_run_plan`.
Before the first scan call `ncs_list_plans` (category "scan") once to
confirm the exact plan name and its parameter names (`num` vs
`num_points`). Pass detectors=["DetectorDiodeCurrent"] explicitly.

#### Lift scans (REQUIRES per-step plan with held translate + settle)
Use `ncs_run_plan_code` with the template below. It explicitly:

1. Reads `sample_translate`'s current value at the start of the scan and
   re-asserts it at every step (item 2).
2. Approaches each lift setpoint from below (item 3 — combats backlash).
3. Waits `settle_s` after each move before reading the detector.

```python
import bluesky.plans as bp
import bluesky.plan_stubs as bps

settle_s = 1.5
start, stop, num = -100.0, 100.0, 21   # relative range
det = DetectorDiodeCurrent
lift = sample_lift
hold = sample_translate

# Snapshot the current positions before relative offsets are applied.
lift0 = (yield from bps.rd(lift))
hold0 = (yield from bps.rd(hold))

steps = [lift0 + start + i * (stop - start) / (num - 1) for i in range(num)]
# Sort ascending so each step approaches from below — eliminates backlash.
steps.sort()

@bpp.run_decorator(md={"plan_name": "rel_lift_scan_held",
                        "motors": ["sample_lift"],
                        "detectors": ["DetectorDiodeCurrent"],
                        "cycle_tag": "fine_lift_cycle1"})
def _inner():
    for target in steps:
        # Always go LOW first to clear backlash, then up to the target.
        yield from bps.mv(lift, target - 5.0)
        yield from bps.mv(lift, target, hold, hold0)
        yield from bps.sleep(settle_s)
        yield from bps.trigger_and_read([det, lift, hold])

yield from _inner()
```

NOTE on `md`: Bluesky reserves `scan_id` (int), `plan_name` (str), `time`,
and `uid` — don't pass `scan_id` as a string. Use non-reserved keys like
`cycle_tag` or `note` for human-readable labels.

After submitting, call `ncs_wait_for_idle(include_last_run=true)`. If it
returns `last_run=null` or `status="plan_never_started"`, the plan failed
before opening a run document — inspect the engine logs, do NOT proceed to
fit.

### Procedure

1. PRE-FLIGHT (manual — ask the operator and WAIT for confirmation):
   a. Confirm the sample is roughly centered at the beam using the Blackfly
      Chamber Cam live view.
   b. Confirm the diode sensitivity is set to 5 microA/V.
   Then move `sample_rotate_steppertheta` to 0 via `ncs_move_motor`.

2. BEAM GATE: call `check_beam`. If `beam_present` is false, STOP, tell the
   operator, and call `ncs_get_beam_status` for ring/shutter context.
   Re-run this check before every scan. The `detector` field in the
   response is the canonical detector for every scan that follows.

3. COARSE LIFT (run ONCE): per-step lift plan with start=-500, stop=500,
   num=21, settle_s=1.5. Get uid (`ncs_wait_for_idle` → last_run.uid).
   Call `fit_lift_halfcut(uid)` then `plot_alignment_scan(uid, "lift")`.
   If detected, move `sample_lift` to halfcut. If NOT detected, STOP and
   hand back to the operator.

4. FINE LIFT: per-step lift plan with start=-100, stop=100, num=21,
   settle_s=1.5. Fit, plot, then move to halfcut, or STOP if not detected.

5. THETA: `rel_scan_1d` on `sample_rotate_steppertheta`, start=-5, stop=5,
   num=41, detectors=["DetectorDiodeCurrent"]. Fit with `fit_theta_peak`,
   plot. If `peak_at_boundary` is true OR detected is false, WIDEN the scan
   (e.g. start=-10, stop=10) and retry once — do not auto-widen more than
   once. Otherwise move theta to peak.

6. RECORD this cycle's (lift, theta) positions. After each cycle:
     ```
     cycles.append({"lift": lift_now, "theta": theta_now})
     status = check_convergence(cycles)
     plot_convergence(cycles)
     if status["converged"]:
         break
     ```
   Otherwise repeat steps 4 then 5, but on every pass after the first fine
   lift tighten the lift scan to start=-50, stop=50, num=21. Cap the loop
   at 6 refinement cycles.

7. Report the final `sample_lift` and `sample_rotate_steppertheta`
   positions and the per-cycle history (plus the convergence plot).

### Rules
- NEVER guess a half-cut or peak by eyeballing data — always use the fit
  tools; their `detected` flag is the decision, and `peak_at_boundary` /
  `r2` are veto flags.
- A null `halfcut` or null `peak` field means the fit was unreliable
  (R² below 0.5). Never move when the field is null, no matter what
  `position` looked like in earlier logs.
- On a failed beam gate or a failed fit, STOP and return control to the
  operator. Do not auto-widen the range or silently continue.
- Every lift scan MUST use the per-step plan with `sample_translate`
  held — plain `rel_scan_1d` on `sample_lift` is forbidden.
- Use `check_convergence` for the stop decision; do not judge convergence
  by eye.
- Use `plot_alignment_scan` and `plot_convergence` after every fit and
  every cycle so the operator can see what is happening.
