"""Motor device classes for 7.0.1.1 endstation.

Includes DeadbandEpicsMotor which handles moves smaller than the motor's
deadband without hanging.
"""

from __future__ import annotations

import logging
import threading
import time

from ophyd import Component as Cpt
from ophyd import EpicsMotor
from ophyd.positioner import PositionerBase
from ophyd.signal import EpicsSignalRO
from ophyd.status import Status
from ophyd.status import wait as status_wait

logger = logging.getLogger(__name__)


class DeadbandEpicsMotor(EpicsMotor):
    """EpicsMotor subclass that handles moves smaller than the motor's deadband.

    Some EPICS motor controllers don't report motion (``.MOVN`` never goes
    True, ``.DMOV`` never goes False) when the requested position is very
    close to the current position. This causes Bluesky to wait indefinitely
    for motion completion.

    This subclass detects that condition *before* issuing the move and
    immediately marks the move as successful, while still writing the
    setpoint to the motor record for consistency.

    The tolerance for detecting a "null move" defaults to the motor record's
    retry deadband (``.RDBD``). Set the ``move_tolerance`` class or instance
    attribute to override with a fixed value.

    Examples
    --------
    Use as a drop-in replacement for ``EpicsMotor``::

        motor = DeadbandEpicsMotor("XF:00-MOT{MC:00-Ax:00}", name="motor")

    Override the tolerance on an instance::

        motor.move_tolerance = 0.002

    Or via subclass::

        class MyMotor(DeadbandEpicsMotor):
            move_tolerance = 0.005
    """

    retry_deadband = Cpt(EpicsSignalRO, ".RDBD", kind="config", auto_monitor=True)

    # If set to a positive number, overrides retry_deadband for the
    # null-move check. ``None`` means "use the motor record's .RDBD".
    move_tolerance: float | None = None

    def _get_move_tolerance(self) -> float:
        """Return the tolerance below which a move is considered a no-op."""
        if self.move_tolerance is not None:
            return self.move_tolerance
        try:
            rdbd = self.retry_deadband.get()
            if rdbd is not None and rdbd > 0:
                return rdbd
        except Exception:
            pass
        # Fallback when RDBD is unavailable or zero.
        return 0.001

    def move(self, position, wait=True, **kwargs):
        """Move to *position*, completing immediately if within the deadband.

        If the distance to *position* is less than or equal to the move
        tolerance (see `_get_move_tolerance`), the motor record is unlikely
        to start an actual move. In that case the setpoint is still written
        but the returned `MoveStatus` is marked done right away so that
        Bluesky does not hang.

        All other moves are delegated to the standard `EpicsMotor.move`.
        """
        current = self.position
        tolerance = self._get_move_tolerance()

        if current is not None and abs(position - current) <= tolerance:
            logger.info(
                "%s: requested move to %s is within tolerance (%s) of "
                "current position %s; completing immediately.",
                self.name,
                position,
                tolerance,
                current,
            )
            self._started_moving = False

            # Create the MoveStatus via PositionerBase (skip EpicsMotor.move
            # so we can control when _done_moving fires).
            status = PositionerBase.move(self, position, **kwargs)

            # Still write the setpoint for bookkeeping.
            self.user_setpoint.put(position, wait=False)

            # Signal completion immediately.
            self._done_moving(success=True)

            try:
                if wait:
                    status_wait(status)
            except KeyboardInterrupt:
                self.stop()
                raise

            return status

        return super().move(position, wait=wait, **kwargs)


class JamRecoveryEpicsMotor(DeadbandEpicsMotor):
    """EpicsMotor that auto-recovers from mid-move jams.

    Some BCS motors (notably ``beamline_energy``) occasionally stall
    mid-motion or settle short of target. This subclass watches each move
    and, on detecting a jam, re-issues the target setpoint up to
    ``max_attempts`` times. If a batch of attempts is exhausted it asks the
    user (via a modal dialog) whether to run another batch or fail the move.
    With no GUI available it fails the move and logs at CRITICAL.

    Recovery runs on a background daemon thread; ``move`` returns a single
    ``Status`` that resolves only when the move ultimately succeeds or fails,
    so Bluesky sees one ordinary (slightly longer) move.
    """

    max_attempts: int = 5
    stall_timeout: float = 3.0
    settle_timeout: float = 60.0
    poll_interval: float = 0.1

    # -- seam methods (mocked in tests; real impls touch EPICS) --------------
    def _read_position(self) -> float:
        return self.user_readback.get()

    def _motion_complete(self) -> bool:
        return bool(self.motor_done_move.get())

    def _issue_setpoint(self, position: float) -> None:
        self.user_setpoint.put(position, wait=False)

    # -- dialog seam (real impl in Task 3) -----------------------------------
    def _prompt_user_continue(self, attempts: int, position: float) -> bool:
        return False

    # -- recovery internals --------------------------------------------------
    def _attempt_and_watch(self, position: float, status: Status) -> str:
        """Issue the setpoint and watch until target reached or a jam.

        Returns 'reached' or 'jammed'. Honors status.done (timeout/abort).
        """
        tolerance = self._get_move_tolerance()
        self._issue_setpoint(position)
        last_pos = self._read_position()
        last_progress = time.monotonic()
        deadline = last_progress + self.settle_timeout

        while not status.done:
            time.sleep(self.poll_interval)
            now = time.monotonic()
            pos = self._read_position()
            if abs(position - pos) <= tolerance:
                return "reached"
            if abs(pos - last_pos) > tolerance:
                last_pos = pos
                last_progress = now
            if self._motion_complete():
                return "jammed"  # settled short of target
            if now - last_progress >= self.stall_timeout:
                return "jammed"  # no progress while still "moving"
            if now >= deadline:
                return "jammed"  # overall settle ceiling
        return "jammed"

    def _run_recovery(self, position: float, status: Status, moved_cb) -> None:
        try:
            attempts = 0
            while not status.done:
                outcome = self._attempt_and_watch(position, status)
                if outcome == "reached":
                    if not status.done:
                        if attempts:
                            logger.info(
                                "%s: recovered to %s after %d attempt(s).",
                                self.name, position, attempts,
                            )
                        status.set_finished()
                    return

                recovered = False
                for _ in range(self.max_attempts):
                    if status.done:
                        return
                    attempts += 1
                    logger.warning(
                        "%s: jam detected; recovery attempt %d/%d "
                        "(re-issuing setpoint %s).",
                        self.name, attempts, self.max_attempts, position,
                    )
                    outcome = self._attempt_and_watch(position, status)
                    if outcome == "reached":
                        if not status.done:
                            logger.info(
                                "%s: recovered to %s after %d attempt(s).",
                                self.name, position, attempts,
                            )
                            status.set_finished()
                        return

                # Batch exhausted: ask the user (Task 3 wires the dialog).
                if self._prompt_user_continue(attempts, position):
                    logger.info(
                        "%s: user chose to keep retrying after %d attempt(s).",
                        self.name, attempts,
                    )
                    continue
                if not status.done:
                    status.set_exception(
                        RuntimeError(
                            f"{self.name}: failed to reach {position} after "
                            f"{attempts} recovery attempt(s)."
                        )
                    )
                return
        except Exception as exc:  # never let the worker thread die silently
            logger.exception("%s: recovery loop crashed.", self.name)
            if not status.done:
                status.set_exception(exc)

    # -- public entry point --------------------------------------------------
    def move(self, position, wait=True, *, timeout=None, moved_cb=None, **kwargs):
        current = self.position
        tolerance = self._get_move_tolerance()
        status = Status(obj=self, timeout=timeout)
        if moved_cb is not None:
            status.add_callback(lambda *a, **k: moved_cb(obj=self))

        if current is not None and abs(position - current) <= tolerance:
            logger.info(
                "%s: requested move to %s within tolerance (%s) of current "
                "position %s; completing immediately.",
                self.name, position, tolerance, current,
            )
            self._issue_setpoint(position)
            status.set_finished()
        else:
            thread = threading.Thread(
                target=self._run_recovery,
                args=(position, status, moved_cb),
                name=f"{self.name}-jam-recovery",
                daemon=True,
            )
            thread.start()

        if wait:
            try:
                status_wait(status)
            except KeyboardInterrupt:
                self.stop()
                raise
        return status
