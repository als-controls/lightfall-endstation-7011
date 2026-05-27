"""Smoke tests for the ReflectionAlignmentAgent skill surface."""
from __future__ import annotations

from lucid_endstation_7011.alignment.skill import ReflectionAlignmentAgent


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
    assert names == {"check_beam", "fit_lift_halfcut", "fit_theta_peak", "check_convergence"}


def test_manifest_registers_reflection_alignment():
    from lucid_endstation_7011.manifest import manifest

    entry = next((p for p in manifest.plugins if p.name == "reflection_alignment"), None)
    assert entry is not None, "reflection_alignment not registered in manifest"
    assert entry.type_name == "agent"
    assert entry.import_path == (
        "lucid_endstation_7011.alignment.skill:ReflectionAlignmentAgent"
    )
