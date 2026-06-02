"""Convergence tracking for the reflection-alignment refinement loop."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConvergenceTracker:
    """Track per-cycle (lift, theta) positions and report double convergence.

    Converged once the most recent ``stable_required`` consecutive
    cycle-to-cycle comparisons all agree within tolerance — i.e.
    ``stable_required + 1`` cycles whose lift and theta each stay within
    ``lift_tol`` / ``theta_tol`` of the previous cycle.
    """

    lift_tol: float = 10.0      # microns
    theta_tol: float = 0.25     # degrees
    stable_required: int = 2    # consecutive agreeing pairwise comparisons
    history: list[tuple[float, float]] = field(default_factory=list)

    def record(self, lift: float, theta: float) -> None:
        self.history.append((float(lift), float(theta)))

    def _agrees(self, a: tuple[float, float], b: tuple[float, float]) -> bool:
        return abs(a[0] - b[0]) <= self.lift_tol and abs(a[1] - b[1]) <= self.theta_tol

    @property
    def converged(self) -> bool:
        if len(self.history) < self.stable_required + 1:
            return False
        recent = self.history[-(self.stable_required + 1):]
        return all(self._agrees(recent[i], recent[i + 1]) for i in range(len(recent) - 1))
