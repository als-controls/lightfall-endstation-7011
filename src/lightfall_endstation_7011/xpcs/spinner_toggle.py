"""A checkable toggle that reuses Lightfall's RunEngine SpinnerIndicator.

Used for the XPCS panel's Enable control: the ALS-logo spinner spins (color)
while processing is enabled and sits gray/static when disabled.
"""

from __future__ import annotations

from lightfall.ui.widgets.runengine_control import SpinnerIndicator
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QWidget


class SpinnerToggle(QWidget):
    """Click-to-toggle wrapper around SpinnerIndicator.

    Mirrors the slice of QAbstractButton the panel relies on: ``isChecked``,
    ``setChecked`` (emits ``toggled`` only on an actual state change), and a
    ``toggled(bool)`` signal. Checked → spinner status "running"; unchecked →
    "idle".
    """

    toggled = Signal(bool)

    def __init__(self, parent: QWidget | None = None,
                 tooltip: str = "Enable processing") -> None:
        super().__init__(parent)
        self._checked = False
        self._spinner = SpinnerIndicator(self)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._spinner)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tooltip)
        self._apply_status()

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool) -> None:
        checked = bool(checked)
        if checked == self._checked:
            return
        self._checked = checked
        self._apply_status()
        self.toggled.emit(checked)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.setChecked(not self._checked)
            event.accept()
            return
        super().mousePressEvent(event)

    def _apply_status(self) -> None:
        self._spinner.set_status("running" if self._checked else "idle")
