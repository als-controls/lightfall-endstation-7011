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


def test_exhaustion_fails_when_user_declines(monkeypatch):
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
