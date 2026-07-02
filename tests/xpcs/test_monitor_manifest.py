import importlib

from lightfall.monitor.monitor_plugin import MonitorPlugin

from lightfall_endstation_7011.manifest import manifest


def test_manifest_has_xpcs_monitor_entry():
    entries = [e for e in manifest.plugins
               if getattr(e, "type_name", None) == "monitor"
               and getattr(e, "name", None) == "xpcs_dynamics"]
    assert len(entries) == 1
    entry = entries[0]
    # import_path resolves to a MonitorPlugin subclass
    module_path, _, cls_name = entry.import_path.partition(":")
    mod = importlib.import_module(module_path)
    cls = getattr(mod, cls_name)
    assert issubclass(cls, MonitorPlugin)
    plugin = cls()
    assert plugin.create_feeds()[0].name == "xpcs_dynamics"
