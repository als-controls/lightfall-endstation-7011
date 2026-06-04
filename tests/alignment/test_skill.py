"""Smoke tests for the ReflectionAlignmentAgent skill surface."""
from __future__ import annotations

from lightfall_endstation_7011.alignment.skill import ReflectionAlignmentAgent


def test_metadata():
    agent = ReflectionAlignmentAgent()
    assert agent.name == "reflection_alignment"
    assert agent.category == "operations"
    assert agent.description.strip()
    assert agent.display_name == "Reflection Alignment"
    assert agent.priority == 30


def test_system_prompt_covers_devices_and_rules():
    body = ReflectionAlignmentAgent().get_system_prompt()
    assert body.strip(), "system prompt must not be empty"
    for token in (
        "sample_lift",
        "sample_rotate_steppertheta",
        "DetectorDiodeCurrent",
        "500 nA",
        "half-cut",
        "rel_scan_1d",
        "check_beam",
        "fit_lift_halfcut",
        "fit_theta_peak",
        "check_convergence",
    ):
        assert token in body, f"prompt missing {token!r}"


def test_exposes_mcp_tools():
    tools = ReflectionAlignmentAgent().create_tools()
    if not tools:
        import pytest

        pytest.skip("claude_agent_sdk not available")
    names = {getattr(t, "name", None) or getattr(t, "__name__", None) for t in tools}
    # quick_read (issue 8), plot_alignment_scan and plot_convergence
    # (issue 10) were added on top of the original four.
    assert names == {
        "check_beam",
        "fit_lift_halfcut",
        "fit_theta_peak",
        "check_convergence",
        "quick_read",
        "plot_alignment_scan",
        "plot_convergence",
    }


def test_system_prompt_covers_new_alignment_rules():
    """Issue 1, 2, 3, 9 - the prompt must teach the agent the new rules."""
    body = ReflectionAlignmentAgent().get_system_prompt()
    for token in (
        # Issue 1: explicit canonical detector enforcement.
        "ONLY detector",
        # Issue 2: coupled motors documented.
        "sample_translate",
        "Coupled motors",
        # Issue 3: settle + from-below approach.
        "from below",
        "settle",
        # Issue 9: explicit convergence loop template.
        "check_convergence(cycles)",
        # Issue 6: boundary handling.
        "peak_at_boundary",
        # New tools.
        "quick_read",
        "plot_alignment_scan",
        "plot_convergence",
    ):
        assert token in body, f"prompt missing required token {token!r}"


def test_manifest_registers_reflection_alignment():
    from lightfall_endstation_7011.manifest import manifest

    entry = next((p for p in manifest.plugins if p.name == "reflection_alignment"), None)
    assert entry is not None, "reflection_alignment not registered in manifest"
    assert entry.type_name == "agent"
    assert entry.import_path == (
        "lightfall_endstation_7011.alignment.skill:ReflectionAlignmentAgent"
    )
