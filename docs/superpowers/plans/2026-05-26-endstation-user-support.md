# Endstation 7.0.1.1 User-Support Skill Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a prompt-only `EndstationSupportAgent` LUCID skill to `lucid-endstation-7011` that gives the embedded agent a growing, beamline-specific user-support triage, initialized with the "detector shows no signal" procedure.

**Architecture:** One `AgentPlugin` subclass with a short always-loaded router prompt (when-to-use + safety rules + a topic table) and a `references/` directory of per-topic procedures loaded lazily by the SDK Skill tool. It reuses the agent's existing MCP tools (`ncs_get_beam_status`, the `device_tools` family) and contributes no tools of its own. Registered via one `manifest.py` entry.

**Tech Stack:** Python 3.11+, `lucid.plugins.agent_plugin.AgentPlugin`, pytest. Markdown reference files. No new runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-05-26-endstation-user-support-design.md`

**Environment note:** Run pytest from the repo root (`lucid-endstation-7011/`) in the environment where `lucid` is importable — the same one the existing `tests/observers/blackfly` and `tests/alignment` suites use. `testpaths = ["tests"]` is already configured.

---

## File Structure

- Create: `src/lucid_endstation_7011/support/__init__.py` — empty package marker.
- Create: `src/lucid_endstation_7011/support/skill.py` — `EndstationSupportAgent(AgentPlugin)`: metadata, router system prompt, references dir. No `create_tools` override (base returns `[]`).
- Create: `src/lucid_endstation_7011/support/references/detector_no_signal.md` — the first topic's full procedure.
- Modify: `src/lucid_endstation_7011/manifest.py` — add one `PluginEntry` for the agent.
- Create: `tests/support/__init__.py` — empty package marker.
- Create: `tests/support/test_skill.py` — `AgentPlugin` surface, references-dir + content tripwires, manifest registration.

---

## Task 1: EndstationSupportAgent surface + router prompt

**Files:**
- Create: `tests/support/__init__.py`
- Create: `tests/support/test_skill.py`
- Create: `src/lucid_endstation_7011/support/__init__.py`
- Create: `src/lucid_endstation_7011/support/skill.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/support/__init__.py` as an empty file.

Create `tests/support/test_skill.py`:

```python
"""Smoke tests for the EndstationSupportAgent skill."""
from __future__ import annotations

from lucid_endstation_7011.support.skill import EndstationSupportAgent


def test_support_agent_metadata():
    agent = EndstationSupportAgent()
    assert agent.name == "endstation_support"
    assert agent.display_name == "Endstation 7.0.1.1 Support"
    assert agent.category == "operations"
    assert agent.description.strip(), "description must not be empty"


def test_support_agent_has_no_tools():
    # Prompt-only skill: it reuses the agent's existing tools, ships none of its own.
    assert EndstationSupportAgent().create_tools() == []


def test_support_agent_system_prompt_is_a_router():
    body = EndstationSupportAgent().get_system_prompt()
    assert body.strip(), "system prompt must not be empty"
    # Both global safety rules must be present.
    assert "propose-then-confirm" in body
    assert "Catalog-or-manual" in body
    # The topic table must point at the first reference file.
    assert "references/detector_no_signal.md" in body
    # The tool inventory must name the beam-status tool used in step 1.
    assert "ncs_get_beam_status" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/support/test_skill.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lucid_endstation_7011.support'`.

- [ ] **Step 3: Create the package marker and the skill**

Create `src/lucid_endstation_7011/support/__init__.py` as an empty file.

Create `src/lucid_endstation_7011/support/skill.py`:

```python
"""EndstationSupportAgent: guided user-support triage for ALS beamline 7.0.1.1.

Prompt-only AgentPlugin. Contributes a router SKILL.md (when-to-use + global
safety rules + a topic table) plus a references/ directory of per-topic
procedures, loaded lazily by the SDK Skill tool. Reuses the embedded agent's
existing tools (ncs_get_beam_status and the device_tools family); contributes
no MCP tools of its own.
"""
from __future__ import annotations

from pathlib import Path

from lucid.plugins.agent_plugin import AgentPlugin


class EndstationSupportAgent(AgentPlugin):
    """Beamline 7.0.1.1 user-support triage skill (prompt-only)."""

    @property
    def name(self) -> str:
        return "endstation_support"

    @property
    def display_name(self) -> str:
        return "Endstation 7.0.1.1 Support"

    @property
    def description(self) -> str:
        return (
            "Beamline 7.0.1.1 user-support triage: guided checks for common "
            "endstation problems."
        )

    @property
    def category(self) -> str:
        return "operations"

    @property
    def priority(self) -> int:
        # Lower sorts earlier in the settings UI; general triage above
        # device-specific skills.
        return 20

    def get_references_dir(self) -> Path | None:
        return Path(__file__).parent / "references"

    def get_system_prompt(self) -> str:
        return """\
## Endstation 7.0.1.1 User-Support Skill

Use this skill when a user at ALS beamline 7.0.1.1 reports an operational
problem covered by the topics below. For the first topic, that is phrasing like
"I can't see anything on the detector", "the detector is blank", or "no
counts/signal on the CCD".

### Safety rules (apply to every topic)

1. **Reads auto-run; writes and moves are propose-then-confirm.** Never set a
   value or move a motor without first stating the exact device and target value
   and getting the user's explicit OK. Only then call `ncs_set_device` or
   `ncs_move_motor` (both DEVICE_CONTROL-gated).
2. **Catalog-or-manual.** Resolve a named device with `ncs_list_devices` /
   `ncs_read_device`. If it is not registered or not readable that way, ask the
   user to check or change it manually in CSS-Phoebus, citing the PV name and
   expected value. Do not invent raw channel access.

### Topics

| Topic | Use when the user says… | Read this file |
|-------|--------------------------|----------------|
| Detector shows no signal | "can't see anything on the detector", blank CCD, no counts/signal | `references/detector_no_signal.md` |

### Tools to use

`ncs_get_beam_status`, `ncs_list_devices`, `ncs_read_device`,
`ncs_get_device_state`, `ncs_set_device`, `ncs_move_motor`.

### How to respond

Identify the matching topic, read its reference file, and follow it step by
step: run the AUTO checks yourself, ask the user to perform the MANUAL ones, and
for CONFIRM steps propose the exact change and wait for an explicit OK before
executing.
"""
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/support/test_skill.py -v`
Expected: PASS — `test_support_agent_metadata`, `test_support_agent_has_no_tools`, `test_support_agent_system_prompt_is_a_router`.

- [ ] **Step 5: Commit**

```bash
git add tests/support/__init__.py tests/support/test_skill.py \
  src/lucid_endstation_7011/support/__init__.py \
  src/lucid_endstation_7011/support/skill.py
git commit -m "feat(support): add EndstationSupportAgent router skill"
```

---

## Task 2: detector_no_signal reference file

**Files:**
- Create: `src/lucid_endstation_7011/support/references/detector_no_signal.md`
- Modify: `tests/support/test_skill.py` (append two tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/support/test_skill.py`:

```python
def test_support_agent_references_dir_resolves():
    refs = EndstationSupportAgent().get_references_dir()
    assert refs is not None
    assert refs.is_dir()
    assert (refs / "detector_no_signal.md").is_file()


def test_detector_no_signal_reference_content():
    """Tripwire: the procedure must keep its key PVs/values and the
    propose-before-write rule, so an edit can't silently gut it."""
    refs = EndstationSupportAgent().get_references_dir()
    assert refs is not None
    text = (refs / "detector_no_signal.md").read_text(encoding="utf-8")
    for token in [
        "13PICAM1:cam1:ShutterTimingMode",
        "BL7ANDOR1:cam1:AndorShutterMode",
        "Acquire",
        "DIAG111",
        "CAL111",
        "DIAG101",
        "PinholeX",
        "PinholeY",
        "ncs_get_beam_status",
    ]:
        assert token in text, f"missing {token!r} in detector_no_signal.md"
    assert "propose" in text.lower(), "propose-before-write rule must be stated"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/support/test_skill.py::test_support_agent_references_dir_resolves tests/support/test_skill.py::test_detector_no_signal_reference_content -v`
Expected: FAIL — `assert refs.is_dir()` is False (directory does not exist yet) / file missing.

- [ ] **Step 3: Create the reference file**

Create `src/lucid_endstation_7011/support/references/detector_no_signal.md`:

```markdown
# Detector shows no signal

User symptom: "I can't see anything on the detector" — the CCD image is blank or
shows no counts.

## Safety rules (repeat)

- Reads auto-run; writes/moves are propose-then-confirm: state the exact device
  and target value, get an explicit OK, then call `ncs_set_device` /
  `ncs_move_motor`.
- Catalog-or-manual: resolve named devices via `ncs_list_devices` /
  `ncs_read_device`; if missing or not readable that way, ask the user to
  check/change it in CSS-Phoebus, citing the PV and expected value.

Each step is tagged AUTO (you read it), MANUAL (the user does/observes it), or
CONFIRM (you propose a write/move and execute only on explicit OK).

## Step 0 — Identify the detector (ask)

Ask the user:
- Which detector are you using — PICAM (`13PICAM1`) or ANDOR (`BL7ANDOR1`)?
- Are you using the Diode? If yes, the expected shutter mode below is
  `Always Open`.

## Step 1 — Is there beam? (AUTO)

Call `ncs_get_beam_status`. Confirm ring current is roughly 500 mA and
beam/shutter is available. If the ring current is near zero or beam is down,
that is the likely cause — report it and stop; tell the user to check with the
floor/operators. Downstream checks are moot without beam.

## Step 2 — Vacuum valves (MANUAL)

Ask the user to confirm that all vacuum valves are open/green up to the
endstation on the CSS-Phoebus panel.

## Step 3 — Optical saturation (MANUAL)

Ask the user: are all chamber viewports covered, and is the chamber light off?
Stray optical light can saturate the CCD so it shows nothing useful.

## Step 4 — Camera shutter mode (AUTO read -> CONFIRM write)

Expected (TV mode):
- PICAM: `13PICAM1:cam1:ShutterTimingMode` = `Normal` (or `Always Open` if using
  the Diode).
- ANDOR: `BL7ANDOR1:cam1:AndorShutterMode` = `FullyAuto` (or `Always Open` if
  using the Diode).

Read the current mode (via the catalog if the camera is registered). If it is
wrong, propose setting it to the expected value and set on OK. If it is not
reachable via the catalog, ask the user to set it in Phoebus (cite the PV and
expected value).

## Step 5 — Is it acquiring? (CONFIRM)

Ensure the camera is acquiring: `13PICAM1:cam1:Acquire` (PICAM) or
`BL7ANDOR1:cam1:Acquire` (ANDOR) = `1`. Propose starting acquisition and set on
OK, or ask the user to start it in Phoebus.

## Step 6 — Is a diagnostic blocking the beam? (AUTO where catalog-resolvable, else MANUAL)

- `DIAG111` position = 0 mm (motor readback).
- `CAL111` = 60 mm (motor readback).
- `DIAG101` = 0 mm (motor readback).
- Manual YAG before `M112` rotated **out** of the beampath (MANUAL — physical).
- `DIAG111` is **out** [manual switch] (MANUAL).

Read the motor positions via the catalog where possible; ask the user for the
manual switch and YAG state.

## Step 7 — Is there light on the YAG? (CONFIRM move + MANUAL observe)

Propose moving `PinholeX` and `PinholeY` to the YAG position (0, 0); move on OK
(or ask the user to). Then ask the user to check the chamber cam — is there
light on the YAG? This localizes whether beam reaches the endstation YAG.

## Step 8 — Localize upstream of M112 (MANUAL)

Ask the user to manually rotate the YAG before `M112` into the beam and check
the window viewport — is beam visible there? This distinguishes a problem
upstream vs downstream of `M112`.

## If all checks pass

If beam is present, valves are open, there is no optical saturation, the shutter
is in TV mode, the camera is acquiring, no diagnostic is blocking, and light
reaches the YAG but the detector still shows nothing, escalate to beamline staff
(e.g. Sophie) with the collected results.
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/support/test_skill.py -v`
Expected: PASS — all five tests, including the two new ones.

- [ ] **Step 5: Commit**

```bash
git add src/lucid_endstation_7011/support/references/detector_no_signal.md \
  tests/support/test_skill.py
git commit -m "feat(support): add detector-no-signal triage reference"
```

---

## Task 3: Register the agent in the manifest

**Files:**
- Modify: `src/lucid_endstation_7011/manifest.py`
- Modify: `tests/support/test_skill.py` (append one test)

- [ ] **Step 1: Write the failing test**

Append to `tests/support/test_skill.py`:

```python
def test_manifest_registers_support_agent():
    from lucid_endstation_7011.manifest import manifest

    entries = [p for p in manifest.plugins if p.name == "endstation_support"]
    assert len(entries) == 1, "expected exactly one endstation_support entry"
    entry = entries[0]
    assert entry.type_name == "agent"
    assert (
        entry.import_path
        == "lucid_endstation_7011.support.skill:EndstationSupportAgent"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/support/test_skill.py::test_manifest_registers_support_agent -v`
Expected: FAIL — `assert len(entries) == 1` is False (0 entries; not yet registered).

- [ ] **Step 3: Add the manifest entry**

In `src/lucid_endstation_7011/manifest.py`, add a new `PluginEntry` to the
`plugins=[...]` list, immediately after the existing Blackfly agent entry (the
one with `name="blackfly"`):

```python
        # Agent plugin: beamline 7.0.1.1 user-support triage skill
        PluginEntry(
            type_name="agent",
            name="endstation_support",
            import_path="lucid_endstation_7011.support.skill:EndstationSupportAgent",
            metadata={"priority": 20},
        ),
```

Also update the module docstring's plugin list to mention the new agent (the
docstring currently enumerates the package's plugins): add a line such as
`- Agent plugin for the endstation user-support skill (detector-no-signal triage)`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/support/test_skill.py::test_manifest_registers_support_agent -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/lucid_endstation_7011/manifest.py tests/support/test_skill.py
git commit -m "feat(support): register endstation_support agent in manifest"
```

---

## Task 4: Full-suite verification

**Files:** none (verification only).

- [ ] **Step 1: Run the support tests**

Run: `pytest tests/support/ -v`
Expected: PASS — all six tests.

- [ ] **Step 2: Run the full package test suite (regression check)**

Run: `pytest -q`
Expected: PASS (or only the pre-existing `hw`-marked Blackfly hardware tests skipped/deselected — they require a live camera and are unrelated to this change). No new failures.

- [ ] **Step 3: Lint the new code**

Run: `ruff check src/lucid_endstation_7011/support tests/support`
Expected: no errors. Fix any reported issues (import order, unused names) and re-run.

- [ ] **Step 4: Commit any lint fixes**

Only if Step 3 required changes:

```bash
git add src/lucid_endstation_7011/support tests/support
git commit -m "style(support): ruff fixes"
```

---

## Self-Review (completed during planning)

**Spec coverage:**
- Scaffold (AgentPlugin, router prompt, references dir) → Task 1. ✓
- `create_tools()` empty → Task 1 (`test_support_agent_has_no_tools`; base default). ✓
- Router prompt: safety rules + topic table + tool inventory → Task 1 prompt + tests. ✓
- detector_no_signal procedure (Steps 0–8, AUTO/MANUAL/CONFIRM, PVs, expected values) → Task 2 reference file. ✓
- Device/signal names (PICAM/ANDOR PVs, DIAG/CAL/Pinhole) → Task 2 reference content + tripwire. ✓
- `ncs_get_beam_status` first; ring ≈ 500 mA → Task 2 Step 1. ✓
- Manifest registration (name, type, import_path, priority 20, category operations) → Task 1 (category) + Task 3 (entry). ✓
- Testing (AgentPlugin surface, references-dir, content tripwire, manifest) → Tasks 1–3. ✓
- No new MCP tools / no ncs/ncs changes → honored (no tasks touch ncs/ncs). ✓

**Placeholder scan:** No TBD/TODO; every code/markdown step shows full content. ✓

**Type/name consistency:** `EndstationSupportAgent`, `endstation_support`,
`"Endstation 7.0.1.1 Support"`, `operations`, priority `20`, import path
`lucid_endstation_7011.support.skill:EndstationSupportAgent`, and reference path
`references/detector_no_signal.md` are identical across the spec, prompt, tests,
and manifest entry. ✓
