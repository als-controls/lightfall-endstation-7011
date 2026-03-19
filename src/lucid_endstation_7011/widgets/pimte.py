"""Princeton PIMTE camera controller plugin for 7.0.1.1 endstation.

Provides a ControllerPlugin that creates PIMTE-specific camera control
widgets with temperature display.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from PySide6.QtWidgets import QGroupBox, QWidget

if TYPE_CHECKING:
    from lucid.ui.models.device_tree import DeviceTreeItem


class _PIMTECameraWidget:
    """Internal widget class that creates the PIMTE camera widget."""

    def __new__(cls, parent: QWidget | None = None):
        """Create the widget by subclassing at runtime."""
        from lucid.ui.widgets.camera.panels.temperature import TemperaturePanel
        from lucid.ui.widgets.camera.plan_based import PlanBasedCameraControlWidget

        class _Widget(PlanBasedCameraControlWidget):
            """PIMTE camera widget with temperature panel."""

            display_name: ClassVar[str] = "PIMTE Camera"
            priority: ClassVar[int] = 100
            supported_tags: ClassVar[set[str]] = {"pimte", "mte", "princeton"}
            supported_classes: ClassVar[set[str]] = {"PIMTE", "MTE", "ProEM", "Princeton"}

            def __init__(self, parent: QWidget | None = None) -> None:
                self._temp_panel: TemperaturePanel | None = None
                super().__init__(parent)

            def _create_device_panels(self) -> list[QGroupBox]:
                """Create the PIMTE temperature panel."""
                panels = super()._create_device_panels()
                self._temp_panel = TemperaturePanel()
                panels.append(self._temp_panel)
                return panels

            def set_items(self, items: list) -> None:
                """Set the camera device to control."""
                super().set_items(items)
                if self._temp_panel is not None:
                    self._temp_panel.set_device(self._device)

            def get_introspection_data(self) -> dict[str, Any]:
                """Get introspection data for MCP tools."""
                data = super().get_introspection_data()
                if self._temp_panel is not None:
                    data["temperature"] = self._temp_panel.get_introspection_data()
                return data

            def closeEvent(self, event) -> None:
                """Clean up on close."""
                if self._temp_panel is not None:
                    self._temp_panel.close()
                super().closeEvent(event)

        return _Widget(parent)


class PIMTEControllerPlugin:
    """Controller plugin for Princeton PIMTE cameras.

    This plugin provides PIMTE-specific camera control widgets for
    devices tagged with 'pimte', 'mte', 'princeton' or having
    PIMTE-related device classes.
    """

    type_name: ClassVar[str] = "controller"
    is_singleton: ClassVar[bool] = True

    @property
    def name(self) -> str:
        return "pimte_camera"

    @property
    def display_name(self) -> str:
        return "PIMTE Camera"

    @property
    def description(self) -> str:
        return "Princeton PIMTE camera control with temperature display"

    def can_control(self, items: list[DeviceTreeItem]) -> int | None:
        """Check if this controller can handle the given items.

        Returns priority 150 for PIMTE devices (higher than generic camera).
        """
        if len(items) != 1:
            return None

        item = items[0]

        # Check tags
        if item.device_info and item.device_info.tags:
            tags = {tag.lower() for tag in item.device_info.tags}
            if tags & {"pimte", "mte", "princeton"}:
                return 150

        # Check device class
        device_class = ""
        if item.device_info and item.device_info.device_class:
            device_class = item.device_info.device_class
        elif item.ophyd_obj is not None:
            device_class = type(item.ophyd_obj).__name__

        if any(c.lower() in device_class.lower() for c in ("pimte", "mte", "proem", "princeton")):
            return 150

        return None

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create a PIMTE camera control widget."""
        return _PIMTECameraWidget(parent)

    def get_introspection_data(self) -> dict[str, Any]:
        """Get introspection data for MCP tools."""
        return {
            "type": self.type_name,
            "name": self.name,
            "display_name": self.display_name,
            "class": self.__class__.__name__,
            "module": self.__class__.__module__,
        }
