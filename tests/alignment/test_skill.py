"""Smoke tests for the ReflectionAlignmentAgent skill surface."""
from __future__ import annotations

from lucid_endstation_7011.alignment.skill import ReflectionAlignmentAgent


def test_metadata():
    agent = ReflectionAlignmentAgent()
    assert agent.name == "reflection_alignment"
    assert agent.category == "operations"
    assert agent.description.strip()


def test_system_prompt_covers_devices_and_rules():
    body = ReflectionAlignmentAgent().get_system_prompt()
    assert body.strip(), "system prompt must not be empty"
    for token in (
        "sample_lift",
        "sample_rotate_steppertheta",
        "DetectorDiodeCurrent",
        "500",
        "half-cut",
        "rel_scan",
        "check_beam",
        "fit_lift_halfcut",
        "fit_theta_peak",
    ):
        assert token in body, f"prompt missing {token!r}"


def test_exposes_three_mcp_tools():
    tools = ReflectionAlignmentAgent().create_tools()
    if not tools:
        import pytest

        pytest.skip("claude_agent_sdk not available")
    names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools}
    assert names == {"check_beam", "fit_lift_halfcut", "fit_theta_peak"}
