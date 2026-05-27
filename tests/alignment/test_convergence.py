"""Tests for the convergence tracker."""
from __future__ import annotations

from lucid_endstation_7011.alignment.convergence import ConvergenceTracker


def test_requires_three_cycles_to_converge_by_default():
    t = ConvergenceTracker()  # stable_required=2 => needs 3 cycles within tol
    t.record(0.0, 0.0)
    assert not t.converged
    t.record(3.0, 0.1)
    assert not t.converged  # only 2 cycles
    t.record(5.0, 0.2)
    assert t.converged  # 3 cycles; both pairwise comparisons within tol


def test_oscillation_in_lift_blocks_convergence():
    t = ConvergenceTracker()
    t.record(0.0, 0.0)
    t.record(50.0, 0.0)  # 50 um > 10 um tol
    t.record(0.0, 0.0)
    assert not t.converged


def test_theta_drift_blocks_convergence():
    t = ConvergenceTracker()
    t.record(0.0, 0.0)
    t.record(1.0, 1.0)   # 1.0 deg > 0.25 deg tol
    t.record(1.0, 1.0)
    assert not t.converged  # first pairwise comparison disagrees on theta


def test_stable_required_one_needs_two_cycles():
    t = ConvergenceTracker(stable_required=1)
    t.record(0.0, 0.0)
    assert not t.converged
    t.record(2.0, 0.1)
    assert t.converged


def test_history_is_exposed():
    t = ConvergenceTracker()
    t.record(1.0, 2.0)
    t.record(3.0, 4.0)
    assert t.history == [(1.0, 2.0), (3.0, 4.0)]
