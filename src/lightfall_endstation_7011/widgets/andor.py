"""Andor camera controller plugin for 7.0.1.1 endstation.

Provides a ControllerPlugin that creates Andor-specific camera control
widgets with cooler controls.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from lightfall.plugins.controller_plugin import ControllerPlugin
from PySide6.QtWidgets import QGroupBox, QWidget

if TYPE_CHECKING:
    from lightfall.ui.models.device_tree import DeviceTreeItem


class AndorCameraControlWidget:
    """Andor camera control widget with cooler panel.

    Extends PlanBasedCameraControlWidget with Andor-specific controls:
    - Cooler on/off control
    - Temperature setpoint
    - Temperature readback
    - Cooler status display
    - Dark frame collection support
    """

    display_name: ClassVar[str] = "Andor Camera"

    def __init__(self, parent: QWidget | None = None) -> None:
        # Import here to avoid circular imports at module load time
        from lightfall.ui.widgets.camera.panels.cooler import CoolerPanel
        from lightfall.ui.widgets.camera.plan_based import PlanBasedCameraControlWidget

        # Create the actual widget
        self._widget = _AndorCameraWidget(parent)

    def __getattr__(self, name: str) -> Any:
        """Delegate attribute access to the wrapped widget."""
        return getattr(self._widget, name)


class _AndorCameraWidget:
    """Internal widget class that does the actual work."""

    def __new__(cls, parent: QWidget | None = None):
        """Create the widget by subclassing at runtime."""
        from lightfall.ui.widgets.camera.panels.cooler import CoolerPanel
        from lightfall.ui.widgets.camera.plan_based import PlanBasedCameraControlWidget

        class _Widget(PlanBasedCameraControlWidget):
            """Andor camera widget with cooler panel."""

            display_name: ClassVar[str] = "Andor Camera"
            priority: ClassVar[int] = 100
            supported_tags: ClassVar[set[str]] = {"andor"}
            supported_classes: ClassVar[set[str]] = {"Andor", "AndorCamera", "AndorDetector"}

            def __init__(self, parent: QWidget | None = None) -> None:
                self._cooler_panel: CoolerPanel | None = None
                super().__init__(parent)

            def _create_device_panels(self) -> list[QGroupBox]:
                """Create the Andor cooler panel."""
                panels = super()._create_device_panels()
                self._cooler_panel = CoolerPanel()
                panels.append(self._cooler_panel)
                return panels

            def set_items(self, items: list) -> None:
                """Set the camera device to control."""
                super().set_items(items)
                if self._cooler_panel is not None:
                    self._cooler_panel.set_device(self._device)

            def get_introspection_data(self) -> dict[str, Any]:
                """Get introspection data for MCP tools."""
                data = super().get_introspection_data()
                if self._cooler_panel is not None:
                    data["cooler"] = self._cooler_panel.get_introspection_data()
                return data

            def closeEvent(self, event) -> None:
                """Clean up on close."""
                if self._cooler_panel is not None:
                    self._cooler_panel.close()
                super().closeEvent(event)

        return _Widget(parent)


class AndorControllerPlugin(ControllerPlugin):
    """Controller plugin for Andor cameras.

    This plugin provides Andor-specific camera control widgets for
    devices tagged with 'andor' or having Andor-related device classes.
    """

    @property
    def name(self) -> str:
        return "andor_camera"

    @property
    def display_name(self) -> str:
        return "Andor Camera"

    @property
    def description(self) -> str:
        return "Andor camera control with cooler"

    def can_control(self, items: list[DeviceTreeItem]) -> int | None:
        """Check if this controller can handle the given items.

        Returns priority 150 for Andor devices (higher than generic camera).
        """
        if len(items) != 1:
            return None

        item = items[0]

        # Check tags
        if item.device_info and item.device_info.tags:
            tags = {tag.lower() for tag in item.device_info.tags}
            if "andor" in tags:
                return 150

        # Check device class
        device_class = ""
        if item.device_info and item.device_info.device_class:
            device_class = item.device_info.device_class
        elif item.ophyd_obj is not None:
            device_class = type(item.ophyd_obj).__name__

        if any(c.lower() in device_class.lower() for c in ("andor", "andorcamera", "andordetector")):
            return 150

        return None

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create an Andor camera control widget."""
        return _AndorCameraWidget(parent)
