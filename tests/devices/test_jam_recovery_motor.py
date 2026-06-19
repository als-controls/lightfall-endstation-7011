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
