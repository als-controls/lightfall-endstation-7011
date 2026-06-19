"""Tests for JamRecoveryEpicsMotor."""
from __future__ import annotations

import logging

import pytest
from ophyd.sim import make_fake_device

from lightfall_endstation_7011.devices.motor import JamRecoveryEpicsMotor

FakeJamMotor = make_fake_device(JamRecoveryEpicsMotor)


def make_motor(**attrs):
    """Build a fake motor with fast timeouts and overridable seams."""
    m = FakeJamMotor("FAKE:MOT", name="energy")
    m.poll_interval = 0.001
    m.stall_timeout = 0.02
    m.settle_timeout = 0.5
    m.max_attempts = 5
    for k, v in attrs.items():
        setattr(m, k, v)
    # Default deadband tolerance for tests (overrides .RDBD lookup).
    m.move_tolerance = 0.1
    return m


def test_clean_move_completes_successfully():
    """A move that immediately reaches target finishes the status as success."""
    m = make_motor()
    # Seam stubs: report we are at target right away.
    m._read_position = lambda: 5.0
    m._motion_complete = lambda: True
    puts = []
    m._issue_setpoint = lambda pos: puts.append(pos)

    status = m.move(5.0, wait=True)

    assert status.done
    assert status.success
    assert puts == [5.0]


def test_stall_then_recovers_on_reput():
    """Motor never reports done and stops progressing (stall, target 5.0),
    then a recovery re-put gets it to target. Readback is coupled to the
    number of setpoint puts so the scenario is deterministic: it stays at
    2.0 until the 2nd put (the first recovery re-put), then reaches 5.0."""
    m = make_motor()
    state = {"puts": 0}
    m._issue_setpoint = lambda pos: state.__setitem__("puts", state["puts"] + 1)
    m._read_position = lambda: 5.0 if state["puts"] >= 2 else 2.0
    m._motion_complete = lambda: False  # never "done" => stall path

    status = m.move(5.0, wait=True)

    assert status.success
    assert state["puts"] >= 2  # initial move + >=1 recovery re-put


def test_settled_off_target_then_recovers():
    """Motor reports done but short of target (settled-off-target), then a
    recovery re-put reaches target. Coupled to put count for determinism:
    reads 3.0 until the 2nd put, then 5.0."""
    m = make_motor()
    state = {"puts": 0}
    m._issue_setpoint = lambda pos: state.__setitem__("puts", state["puts"] + 1)
    m._read_position = lambda: 5.0 if state["puts"] >= 2 else 3.0
    m._motion_complete = lambda: True  # settled; off-target until recovery

    status = m.move(5.0, wait=True)

    assert status.success
    assert state["puts"] >= 2


def test_exhaustion_fails_when_user_declines():
    """If every attempt jams and the user declines (placeholder returns
    False), the status errors after max_attempts re-puts."""
    m = make_motor()
    m._read_position = lambda: 2.0       # never reaches target 5.0
    m._motion_complete = lambda: True    # settled-off-target every time
    puts = []
    m._issue_setpoint = lambda pos: puts.append(pos)
    m._prompt_user_continue = lambda attempts, position: False

    status = m.move(5.0, wait=False)
    with pytest.raises(RuntimeError):
        status.wait(timeout=5)  # let the background thread finish; raises on error

    assert status.done and not status.success
    # 1 initial attempt + max_attempts recovery re-puts
    assert len(puts) == 1 + m.max_attempts


def test_continue_choice_runs_another_batch():
    """When the user chooses 'continue', another batch of attempts runs.
    First batch jams; second batch reaches target."""
    m = make_motor()
    m.max_attempts = 2
    box = {"calls": 0}

    def read():
        # After the user is prompted once, start reporting target reached.
        return 5.0 if box["calls"] else 2.0
    m._read_position = read
    m._motion_complete = lambda: True
    m._issue_setpoint = lambda pos: None

    def prompt(attempts, position):
        box["calls"] += 1
        return True  # keep retrying
    m._prompt_user_continue = prompt

    status = m.move(5.0, wait=False)
    status.wait(timeout=5)

    assert status.success
    assert box["calls"] == 1  # prompted once, then recovered next batch


def test_headless_fails_and_logs_critical(caplog):
    """With no QApplication, _prompt_user_continue returns False and logs
    CRITICAL, so the move fails."""
    m = make_motor()
    m.max_attempts = 1
    m._read_position = lambda: 2.0
    m._motion_complete = lambda: True
    m._issue_setpoint = lambda pos: None

    # Force the no-GUI branch regardless of the test environment.
    import PySide6.QtWidgets as qtw
    original = qtw.QApplication.instance
    qtw.QApplication.instance = staticmethod(lambda: None)
    try:
        with caplog.at_level(logging.CRITICAL):
            status = m.move(5.0, wait=False)
            with pytest.raises(RuntimeError):
                status.wait(timeout=5)
    finally:
        qtw.QApplication.instance = original

    assert status.done and not status.success
    assert any(r.levelno >= logging.CRITICAL for r in caplog.records)


def test_stop_interrupts_recovery_and_errors_status():
    """Calling stop() during a jam recovery halts the loop and errors the
    status (does not hang)."""
    import threading as _t
    m = make_motor()
    m.stall_timeout = 10.0     # ensure it would otherwise loop a long time
    m.settle_timeout = 10.0
    m._read_position = lambda: 2.0      # never reaches 5.0
    m._motion_complete = lambda: False  # stalled, slow path
    m._issue_setpoint = lambda pos: None

    status = m.move(5.0, wait=False)
    # Give the worker a moment to enter the watch loop, then stop.
    _t.Timer(0.05, m.stop).start()
    with pytest.raises(RuntimeError):
        status.wait(timeout=5)

    assert status.done and not status.success


def test_setpoint_put_failure_errors_status():
    """If issuing the setpoint raises, the move fails rather than hanging."""
    m = make_motor()
    m._read_position = lambda: 0.0
    m._motion_complete = lambda: False

    def boom(pos):
        raise RuntimeError("PV disconnected")
    m._issue_setpoint = boom

    status = m.move(5.0, wait=False)
    with pytest.raises(RuntimeError):
        status.wait(timeout=5)

    assert status.done and not status.success


def test_exported_from_devices_package():
    from lightfall_endstation_7011 import devices
    assert devices.JamRecoveryEpicsMotor is JamRecoveryEpicsMotor


def test_subdeadband_move_short_circuits():
    """Inherited DeadbandEpicsMotor behavior: a move within tolerance of the
    current position completes immediately without spawning recovery."""
    m = make_motor()
    m._read_position = lambda: 5.0
    m._motion_complete = lambda: True
    puts = []
    m._issue_setpoint = lambda pos: puts.append(pos)

    # current position ~5.0; request 5.05 with tolerance 0.1 => null move.
    m.user_readback.sim_put(5.0)  # FakeEpicsSignal: set readback for .position
    status = m.move(5.05, wait=True)

    assert status.success
    assert puts == [5.05]
