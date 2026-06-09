from PySide6.QtCore import Qt

from lightfall_endstation_7011.xpcs.spinner_toggle import SpinnerToggle


def test_set_checked_reflects_state_and_emits_on_change(qtbot):
    t = SpinnerToggle()
    qtbot.addWidget(t)
    emitted = []
    t.toggled.connect(emitted.append)
    assert t.isChecked() is False
    t.setChecked(True)
    assert t.isChecked() is True
    assert emitted == [True]
    t.setChecked(True)          # no-op: same value must not re-emit
    assert emitted == [True]
    t.setChecked(False)
    assert emitted == [True, False]


def test_click_toggles(qtbot):
    t = SpinnerToggle()
    qtbot.addWidget(t)
    emitted = []
    t.toggled.connect(emitted.append)
    qtbot.mouseClick(t, Qt.MouseButton.LeftButton)
    assert emitted == [True]


def test_checked_spins_unchecked_static(qtbot):
    t = SpinnerToggle()
    qtbot.addWidget(t)
    assert t._spinner._status == "idle"   # gray, static when off
    t.setChecked(True)
    assert t._spinner._status == "running"  # color, spinning when on
