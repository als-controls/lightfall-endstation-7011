"""Smoke tests for the BlackflyAgent skill."""
from __future__ import annotations

from lightfall_endstation_7011.observers.blackfly.skill import BlackflyAgent


def test_blackfly_agent_metadata():
    agent = BlackflyAgent()
    assert agent.name == "blackfly"
    assert agent.display_name == "Blackfly Camera"
    assert "Blackfly" in agent.description
    assert agent.category == "devices"


def test_blackfly_agent_system_prompt_non_empty():
    """Skill body must be non-empty and reference the public API entry points."""
    body = BlackflyAgent().get_system_prompt()
    assert body.strip(), "system prompt must not be empty"
    # The prompt must teach the agent the public import paths.
    assert "lightfall.ui.widgets.observers" in body
    assert "lightfall_endstation_7011.observers.blackfly" in body
    # And the workflow must mention the discover tool by name.
    assert "discover_blackfly_cameras" in body


def test_blackfly_agent_exposes_one_mcp_tool():
    """create_tools returns exactly the discover_blackfly_cameras tool.

    Skipped gracefully when claude_agent_sdk is not installed in the venv —
    in that case, AgentPlugin.create_tools returns []; the production runtime
    handles the missing-SDK case identically (tool surface is empty).
    """
    tools = BlackflyAgent().create_tools()
    if not tools:
        import pytest

        pytest.skip("claude_agent_sdk not available")
    assert len(tools) == 1, f"expected one tool, got {len(tools)}"
    tool = tools[0]
    name = getattr(tool, "name", None) or getattr(tool, "__name__", None)
    assert name == "discover_blackfly_cameras", f"unexpected tool name: {name!r}"


def test_blackfly_agent_references_dir_resolves():
    """get_references_dir must point at an existing directory containing panel_template.py."""
    refs = BlackflyAgent().get_references_dir()
    assert refs is not None
    assert refs.is_dir()
    assert (refs / "panel_template.py").is_file()


def test_panel_template_is_parseable_python():
    """The template is the literal source the agent hands to ncs_create_user_plugin —
    if it ever drifts to invalid Python, every Blackfly panel creation breaks silently
    until a hardware-test catches it. Parse it here as a tripwire."""
    import ast

    refs = BlackflyAgent().get_references_dir()
    assert refs is not None
    src = (refs / "panel_template.py").read_text(encoding="utf-8")
    ast.parse(src)
    # And the placeholders the workflow promises must actually be present:
    assert "<IP>" in src
    assert "<HOST>" in src
