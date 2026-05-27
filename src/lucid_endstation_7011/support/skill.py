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
