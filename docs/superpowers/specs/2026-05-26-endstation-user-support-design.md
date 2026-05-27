# Endstation 7.0.1.1 User-Support Skill — Design

**Date:** 2026-05-26
**Status:** Approved (design); pending implementation plan
**Repos touched:** `lucid-endstation-7011` (only). No `ncs/ncs` changes — reuses the
existing `ncs_get_beam_status` and `device_tools` MCP tools already shipped to the
embedded agent.

## Problem

Users at ALS beamline 7.0.1.1 hit recurring operational problems whose triage is a
known, beamline-specific checklist — part of which the embedded LUCID agent can read
and act on itself, and part of which requires a human at the hutch (covering
viewports, throwing a manual switch, eyeballing a YAG). Today that knowledge lives in
the heads of experts (Sophie). We want a **growing** user-support skill for the
embedded agent that runs the automatable checks itself and walks the user through the
manual ones, one issue at a time.

Initialize with a single issue: **"Why can't I see anything on the detector?"**

The detector-no-signal procedure, as provided by Sophie:

1. Ring current?
2. All vacuum valves open/green up to the endstation on the CSS-Phoebus panel?
3. Are you saturating the CCD with optical light? Check all chamber viewports are
   covered; check whether the chamber light is on.
4. Query the shutter of the CCD in use — should be in TV mode:
   - PICAM: `13PICAM1:cam1:ShutterTimingMode` = `Normal` (or `Always Open` if using
     the Diode); `13PICAM1:cam1:Acquire` = `1`.
   - ANDOR: `BL7ANDOR1:cam1:AndorShutterMode` = `FullyAuto` (or `Always Open` if using
     the Diode); `BL7ANDOR1:cam1:Acquire` = `1`.
5. Are you blocking the beam with a diagnostic? `DIAG111` position = 0 mm; manual YAG
   before `M112` rotated out of the beampath; `CAL111` = 60 mm; `DIAG111` is out
   [manual switch]; `DIAG101` = 0 mm.
6. Check the chamber cam; put `PinholeX`,`PinholeY` to the YAG position (0,0) in the
   endstation → do you see light on the YAG?
7. Manually rotate the YAG before `M112`; check the window viewport.

## Scope

- This spec establishes the **support-skill scaffold** (one `AgentPlugin`, a router
  system prompt, a `references/` directory) **and the first topic**
  (`detector_no_signal`).
- Future issues/setups are added later as additional `references/<topic>.md` files
  plus a line in the prompt's topic table — each its own brainstorm → plan →
  implement cycle. The scaffold is designed for exactly that growth, so adding a
  topic touches only the new reference file and the table.

## Decisions (resolved during brainstorming)

- **Artifact:** one `AgentPlugin` subclass, `EndstationSupportAgent`, prompt-only —
  `create_tools()` returns `[]`. It contributes a `SKILL.md` body and a `references/`
  dir; it reuses the agent's existing tools rather than shipping its own.
- **Structure:** a short, always-loaded **router prompt** (when-to-use + global
  safety rules + a topic table of contents) plus **per-topic reference files** loaded
  lazily by the SDK Skill tool only when a topic is relevant. This keeps always-on
  context small as the topic library grows, and mirrors the existing `references/`
  pattern (blackfly, reflection-alignment).
- **Action boundary:** **reads auto-run; writes/moves are propose-then-confirm.** For
  any set/move the agent states the exact device + target value, gets an explicit OK,
  *then* calls `ncs_set_device` / `ncs_move_motor` (both DEVICE_CONTROL-gated).
- **Signal access — catalog-or-manual:** try the named DeviceCatalog device first; if
  it is not registered or not readable that way, fall back to asking the user to check
  or change it manually in CSS-Phoebus, citing the PV string and expected value. **No
  new raw caget/caput tool** is added.
- **Detector branch:** chosen by **asking the user** (PICAM `13PICAM1` vs ANDOR
  `BL7ANDOR1`), and whether the **Diode** is in use (changes the expected shutter
  value to `Always Open`).
- **Beam check first:** lead with `ncs_get_beam_status` — it is instant and beam-down
  is a common root cause. Ring-current sanity ≈ **500 mA**.

## Architecture

```
src/lucid_endstation_7011/support/
  __init__.py
  skill.py                      # EndstationSupportAgent(AgentPlugin), prompt-only
  references/
    detector_no_signal.md       # first issue (full procedure below)
tests/support/
  __init__.py
  test_skill.py                 # AgentPlugin surface + references-dir tripwires
```

A new manifest entry registers the agent:

```python
PluginEntry(
    type_name="agent",
    name="endstation_support",
    import_path="lucid_endstation_7011.support.skill:EndstationSupportAgent",
    metadata={"priority": 20},
)
```

`AgentPlugin` surface for `EndstationSupportAgent`:

- `name` → `"endstation_support"`
- `display_name` → `"Endstation 7.0.1.1 Support"`
- `description` → one line, e.g. *"Beamline 7.0.1.1 user-support triage: guided checks
  for common endstation problems."*
- `category` → `"operations"`
- `priority` → `20` (sorts near the top of the settings UI; general triage, above
  device-specific skills)
- `get_system_prompt()` → the router prompt (below)
- `get_references_dir()` → `Path(__file__).parent / "references"`
- `create_tools()` → `[]`

## Router system prompt (`get_system_prompt`)

Always loaded, kept short. Contains:

1. **When to use:** the user reports a beamline-7.0.1.1 operational problem — for the
   first topic, phrases like "I can't see anything on the detector", "detector is
   blank", "no signal/counts on the CCD".
2. **Global safety rules** (inherited by every topic):
   - *Reads auto-run; writes/moves are propose-then-confirm.* Never set or move
     without stating the exact device + target value and getting an explicit OK first;
     then use `ncs_set_device` / `ncs_move_motor`.
   - *Catalog-or-manual.* Resolve a named device via `ncs_list_devices` /
     `ncs_read_device`; if it is missing or not readable that way, ask the user to
     check/change it manually in CSS-Phoebus, citing the PV and expected value. Do not
     fabricate raw channel access.
3. **Topic table of contents** — one row per topic mapping trigger → reference file:

   | Topic | Trigger | Read this |
   |-------|---------|-----------|
   | Detector shows no signal | "can't see anything on the detector", blank CCD, no counts | `references/detector_no_signal.md` |

4. **Tool inventory** to use: `ncs_get_beam_status`, `ncs_list_devices`,
   `ncs_read_device`, `ncs_get_device_state`, `ncs_set_device`, `ncs_move_motor`.
5. **Instruction:** identify the matching topic, **read its reference file**, and
   follow it step by step — running AUTO checks itself, requesting MANUAL ones, and
   proposing CONFIRM writes before executing.

## Reference: `detector_no_signal.md`

Each step is tagged **AUTO** (agent reads), **MANUAL** (user does/observes), or
**CONFIRM** (agent proposes a write/move and executes only on explicit OK).

- **Step 0 — Branch (ask):** Which detector — PICAM (`13PICAM1`) or ANDOR
  (`BL7ANDOR1`)? Are you using the Diode? (Diode ⇒ expected shutter value is
  `Always Open`.) These choices select the Step-4/5 branch and expected values.
- **Step 1 — Beam present? (AUTO):** Call `ncs_get_beam_status`. Confirm ring current
  ≈ 500 mA and beam/shutter available. If ring current is ~0 or beam is down, report
  that as the likely cause and stop (direct the user to the floor/operators) — no point
  continuing downstream checks with no beam.
- **Step 2 — Vacuum valves (MANUAL):** Ask the user to confirm all vacuum valves are
  open/green up to the endstation on the CSS-Phoebus panel.
- **Step 3 — Optical saturation (MANUAL):** Ask the user: are all chamber viewports
  covered, and is the chamber light off? Optical light can saturate/blind the CCD.
- **Step 4 — Camera shutter mode (AUTO read → CONFIRM write):**
  - PICAM: expected `13PICAM1:cam1:ShutterTimingMode` = `Normal` (or `Always Open` if
    using the Diode).
  - ANDOR: expected `BL7ANDOR1:cam1:AndorShutterMode` = `FullyAuto` (or `Always Open`
    if using the Diode).
  - Read via the catalog if the camera is a registered device; if the mode is wrong,
    propose the change and set on OK. If not reachable via the catalog, ask the user to
    set it in Phoebus (cite PV + expected value).
- **Step 5 — Acquiring? (CONFIRM):** Ensure `…:cam1:Acquire` = `1`. Propose starting
  acquisition; set on OK (or ask the user to do it in Phoebus).
- **Step 6 — Beam blocked by a diagnostic? (AUTO where catalog-resolvable, else
  MANUAL):**
  - `DIAG111` position = 0 mm — motor readback (AUTO if in catalog).
  - `CAL111` = 60 mm — motor readback (AUTO if in catalog).
  - `DIAG101` = 0 mm — motor readback (AUTO if in catalog).
  - Manual YAG before `M112` rotated **out** of the beampath — MANUAL (physical).
  - `DIAG111` is **out** [manual switch] — MANUAL.
- **Step 7 — Light on the YAG? (CONFIRM move + MANUAL observe):** Propose moving
  `PinholeX`,`PinholeY` to the YAG position (0,0); move on OK (or user does it). Then
  ask the user to check the chamber cam — is there light on the YAG? This localizes
  whether beam reaches the endstation YAG.
- **Step 8 — Localize upstream (MANUAL):** Ask the user to manually rotate the YAG
  before `M112` into the beam and check the window viewport — is beam visible there?
  Distinguishes a problem upstream vs downstream of `M112`.

The reference file also restates the global safety rules at the top, so the procedure
is self-contained when the agent reads it.

## Device / signal names

Catalog names are resolved at runtime via `ncs_list_devices`; the names below are the
defaults the procedure assumes and confirms (or falls back to the PV) when missing.

| Role | Catalog name (default) | PV (manual fallback) | Expected |
|------|------------------------|----------------------|----------|
| Ring current | `ring_current` | via `ncs_get_beam_status` | ≈ 500 mA |
| PICAM shutter mode | resolve (`13PICAM1`) | `13PICAM1:cam1:ShutterTimingMode` | `Normal` (`Always Open` if Diode) |
| PICAM acquire | resolve (`13PICAM1`) | `13PICAM1:cam1:Acquire` | `1` |
| ANDOR shutter mode | resolve (`BL7ANDOR1`) | `BL7ANDOR1:cam1:AndorShutterMode` | `FullyAuto` (`Always Open` if Diode) |
| ANDOR acquire | resolve (`BL7ANDOR1`) | `BL7ANDOR1:cam1:Acquire` | `1` |
| Diagnostic DIAG111 | `DIAG111` | — | 0 mm; also **out** via manual switch |
| Calibration CAL111 | `CAL111` | — | 60 mm |
| Diagnostic DIAG101 | `DIAG101` | — | 0 mm |
| Pinhole X | `PinholeX` | — | 0 (YAG position) |
| Pinhole Y | `PinholeY` | — | 0 (YAG position) |
| YAG before M112 | manual/physical | — | rotated **out** (normal); **in** for Step 8 |

## Tools reused (no new tools)

- `ncs_get_beam_status` — ring current, beam/shutter availability (Step 1).
- `ncs_list_devices` / `ncs_read_device` / `ncs_get_device_state` — resolve and read
  catalog devices (Steps 4, 6).
- `ncs_set_device` — shutter mode, `Acquire` (Steps 4, 5); DEVICE_CONTROL-gated.
- `ncs_move_motor` — pinhole move (Step 7); DEVICE_CONTROL-gated.

## Human checkpoints / manual steps

Vacuum-valve verification (Phoebus), optical-saturation checks (viewports/chamber
light), the `M112` manual YAG and `DIAG111` manual switch, the chamber-cam
observation of light on the YAG, and the window-viewport check are all requested from
the user — the agent cannot perform them.

## Error handling

- **Beam down / ring ≈ 0** (Step 1): report as the likely cause and stop; downstream
  checks are moot without beam.
- **Device not in catalog / not readable:** fall back to the manual Phoebus
  instruction with the PV + expected value (catalog-or-manual rule).
- **Write denied (no DEVICE_CONTROL):** `ncs_set_device` / `ncs_move_motor` returns a
  permission error; surface it and ask the user to perform the change in Phoebus.
- **User declines a proposed write/move:** do not execute; record it and continue with
  the remaining diagnostic steps (or stop if the user asks).

## Testing

`tests/support/test_skill.py`, mirroring blackfly's `test_skill.py`:

- `AgentPlugin` surface: `name == "endstation_support"`, `display_name`, `category ==
  "operations"`, and `create_tools() == []`.
- `get_system_prompt()` is non-empty, states the two safety rules, and lists the
  detector-no-signal topic pointing at `references/detector_no_signal.md`.
- `get_references_dir()` resolves to an existing directory containing
  `detector_no_signal.md`.
- Content tripwire on `detector_no_signal.md`: it mentions the key PVs/expected values
  (`13PICAM1:cam1:ShutterTimingMode`, `BL7ANDOR1:cam1:AndorShutterMode`, `Acquire`,
  `DIAG111`, `CAL111`, `DIAG101`, `PinholeX`, `PinholeY`) and the propose-before-write
  rule, so an edit can't silently gut the procedure.

## Out of scope

- New MCP tools (the skill reuses existing ones).
- Auto-detecting which detector is in use (the agent asks).
- Wiring vacuum-valve and chamber-light state to PVs (stays manual until names are
  provided).
- Additional support topics — each is a later brainstorm → plan → implement cycle that
  adds a reference file and a topic-table row.
