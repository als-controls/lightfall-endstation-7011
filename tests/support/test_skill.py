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
    doc = refs / "detector_no_signal.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    for token in [
        "13PICAM1:cam1:ShutterTimingMode",
        "BL7ANDOR1:cam1:AndorShutterMode",
        "cam1:Acquire",
        "DIAG111",
        "CAL111",
        "DIAG101",
        "PinholeX",
        "PinholeY",
        "ncs_get_beam_status",
    ]:
        assert token in text, f"missing {token!r} in detector_no_signal.md"
    assert "propose" in text.lower(), "propose-before-write rule must be stated"


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
    assert entry.metadata.get("priority") == 20
