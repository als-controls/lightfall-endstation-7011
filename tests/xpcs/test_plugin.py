from lightfall_endstation_7011.manifest import manifest
from lightfall_endstation_7011.xpcs.plugin import XPCSPanelPlugin


def test_plugin_provides_panel_class():
    plugin = XPCSPanelPlugin()
    assert plugin.name == "xpcs"
    cls = plugin.get_panel_class()
    assert cls.panel_metadata.id == "lightfall_endstation_7011.panels.xpcs"


def test_manifest_contains_panel_entry():
    entries = [p for p in manifest.plugins if p.type_name == "panel" and p.name == "xpcs"]
    assert len(entries) == 1
    assert entries[0].import_path == (
        "lightfall_endstation_7011.xpcs.plugin:XPCSPanelPlugin")
