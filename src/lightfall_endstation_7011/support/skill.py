"""EndstationSupportAgent: guided user-support triage for ALS beamline 7.0.1.1.

Prompt-only ToolPlugin. Contributes a router SKILL.md (when-to-use + global
safety rules + a topic table) plus a references/ directory of per-topic
procedures, loaded lazily by the SDK Skill tool. Reuses the embedded agent's
existing tools (ncs_get_beam_status and the device_tools family); contributes
no MCP tools of its own.
"""
from __future__ import annotations

from pathlib import Path

from lightfall.plugins.tool_plugin import ToolPlugin


class EndstationSupportAgent(ToolPlugin):
    """Beamline 7.0.1.1 user-support triage skill (prompt-only)."""

    def get_skill_dir(self) -> Path:
        return Path(__file__).parent.parent / "skills" / "endstation_support"

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
