"""Canonical PanelPlugin template for a Blackfly S live-view panel.

The Blackfly skill (lucid_endstation_7011.observers.blackfly.skill.BlackflyAgent)
instructs the embedded Claude agent to:
  1. read this file's source verbatim,
  2. substitute the placeholders <IP> (camera IPv4) and <HOST> (host NIC IPv4)
     with values gathered from the user (and/or discover_blackfly_cameras),
  3. pass the substituted text to mcp__panel_builder__ncs_create_user_plugin.

Two placeholders only — keep it that way. If a user wants something fancier
(extra controls, multi-camera layout, ROI overlays), they edit the resulting
plugin after creation; this template is only the minimum viable live-view.

The file is also a valid Python module on its own (the placeholder strings are
just string literals at construction time), so the skill's smoke-test can
import it.
"""
from __future__ import annotations

from typing import ClassVar

from lucid.plugins.panel_plugin import PanelPlugin
from lucid.ui.panels.base import BasePanel, PanelMetadata
from lucid.ui.widgets.observers import CameraImageView
from lucid_endstation_7011.observers.blackfly import BlackflyCamera


class BlackflyLivePanel(BasePanel):
    """Live-view panel hosting a single BlackflyCamera + CameraImageView."""

    panel_metadata: ClassVar[PanelMetadata] = PanelMetadata(
        id="lucid.panels.user.blackfly_live",
        name="Blackfly S Live View",
        description="Live image stream from a FLIR Blackfly S over GVCP/GVSP",
        category="Devices",
        keywords=["blackfly", "flir", "camera", "gige", "live view"],
    )

    def _setup_ui(self) -> None:
        super()._setup_ui()
        camera = BlackflyCamera(device_ip="<IP>", bind_ip="<HOST>")
        self._layout.addWidget(CameraImageView(camera=camera))


class BlackflyLivePanelPlugin(PanelPlugin):
    """Panel plugin exposing the Blackfly live-view panel under View > User."""

    @property
    def name(self) -> str:
        return "blackfly_live"

    def get_panel_class(self) -> type[BasePanel]:
        return BlackflyLivePanel
