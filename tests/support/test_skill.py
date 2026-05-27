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
