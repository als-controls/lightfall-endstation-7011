"""PanelPlugin registration for the XPCS live panel."""

from __future__ import annotations

from lightfall.plugins.panel_plugin import PanelPlugin


class XPCSPanelPlugin(PanelPlugin):
    @property
    def name(self) -> str:
        return "xpcs"

    @property
    def description(self) -> str:
        return "Live XPCS g2 panel for the xpcs-live correlator service"

    def get_panel_class(self):
        from lightfall_endstation_7011.xpcs.panel import XPCSPanel
        return XPCSPanel
