"""Detector diode controller plugin for 7.0.1.1 endstation.

Provides a simple read-only display widget for DetectorDiode devices.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lucid.plugins.controller_plugin import ControllerPlugin
from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QGroupBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

if TYPE_CHECKING:
    from lucid.ui.models.device_tree import DeviceTreeItem


class DiodeControlWidget(QWidget):
    """Simple read-only widget for detector diode current display.

    Shows the current diode reading with auto-refresh.
    """

    # Class variables for plugin matching
    display_name: ClassVar[str] = "Detector Diode"
    priority: ClassVar[int] = 100
    supported_classes: ClassVar[set[str]] = {"DetectorDiode"}

    # Qt signals
    value_changed = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the diode control widget."""
        super().__init__(parent)
        self._device = None
        self._diode_signal = None

        # UI elements
        self._value_label = QLabel("--")
        self._units_label = QLabel("")

        # Setup UI
        self._setup_ui()

        # Auto-refresh timer
        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._update_value)
        self._refresh_timer.start(1000)  # Update every second

    def _setup_ui(self) -> None:
        """Create the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Reading group
        reading_group = QGroupBox("Current Reading")
        reading_layout = QGridLayout(reading_group)

        # Value display
        self._value_label.setStyleSheet(
            "font-size: 24pt; font-weight: bold; color: #4CAF50;"
        )
        reading_layout.addWidget(QLabel("Diode:"), 0, 0)
        reading_layout.addWidget(self._value_label, 0, 1)
        reading_layout.addWidget(self._units_label, 0, 2)

        layout.addWidget(reading_group)
        layout.addStretch()

    def set_items(self, items: list[DeviceTreeItem]) -> None:
        """Set the device to control.

        Args:
            items: List containing a single DeviceTreeItem for the diode.
        """
        if not items:
            self._device = None
            self._diode_signal = None
            return

        item = items[0]
        self._device = item.ophyd_obj

        if self._device is not None and hasattr(self._device, "diode"):
            self._diode_signal = self._device.diode

            # Get units from metadata or egu
            units = ""
            if hasattr(self._diode_signal, "metadata"):
                units = self._diode_signal.metadata.get("units", "")
            if not units and hasattr(self._diode_signal, "egu"):
                units = self._diode_signal.egu or ""

            self._units_label.setText(units)

            # Initial update
            self._update_value()

    def _update_value(self) -> None:
        """Refresh the displayed value from the device."""
        if self._diode_signal is None:
            return

        try:
            # Use get_sync() if available (non-blocking)
            if hasattr(self._diode_signal, "get_sync"):
                value = self._diode_signal.get_sync()
            else:
                value = self._diode_signal.get()

            if value is not None:
                # Format with appropriate precision
                if isinstance(value, float):
                    text = f"{value:.4g}"
                else:
                    text = str(value)

                self._value_label.setText(text)
                self.value_changed.emit(float(value))

        except Exception:
            # Silently ignore errors (device may be disconnected)
            pass

    def closeEvent(self, event) -> None:
        """Clean up on close."""
        if self._refresh_timer:
            self._refresh_timer.stop()
        super().closeEvent(event)


class DiodeControllerPlugin(ControllerPlugin):
    """Controller plugin for detector diode devices.

    Provides a simple read-only display widget for photodiode current
    monitoring.
    """

    @property
    def name(self) -> str:
        return "detector_diode"

    @property
    def display_name(self) -> str:
        return "Detector Diode"

    @property
    def description(self) -> str:
        return "Read-only display for detector diode current"

    def can_control(self, items: list[DeviceTreeItem]) -> int | None:
        """Check if this controller can handle the given items.

        Returns priority 100 for DetectorDiode devices.
        """
        if len(items) != 1:
            return None

        item = items[0]

        # Check device class
        device_class = ""
        if item.device_info and item.device_info.device_class:
            device_class = item.device_info.device_class
        elif item.ophyd_obj is not None:
            device_class = type(item.ophyd_obj).__name__

        # Match DetectorDiode class
        if "DetectorDiode" in device_class:
            return 100

        # Check tags
        if item.device_info and item.device_info.tags:
            tags = {tag.lower() for tag in item.device_info.tags}
            if "diode" in tags or "photodiode" in tags:
                return 90

        return None

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        """Create a diode control widget."""
        return DiodeControlWidget(parent)
