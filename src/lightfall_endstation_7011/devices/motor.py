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
from ophyd.signal import EpicsSignal
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

    retry_deadband = Cpt(EpicsSignal, ".RDBD", kind="config", auto_monitor=True)

    # If set to a positive number, overrides retry_deadband for the
    # null-move check. ``None`` means "use the motor record's .RDBD".
    move_tolerance: float | None = None

    def __init__(self, *args, deadband: float | None = None, **kwargs):
        self._init_deadband = deadband
        self._deadband_applied = False
        super().__init__(*args, **kwargs)
        if deadband is not None:
            # Put on init. If the PV is already connected (e.g. fakes, or a
            # warm CA connection) apply inline; otherwise wait for the connect
            # transition via a meta subscription (fires once, guarded).
            if self.retry_deadband.connected:
                self._apply_init_deadband(connected=True)
            else:
                self.retry_deadband.subscribe(
                    self._apply_init_deadband,
                    event_type=self.retry_deadband.SUB_META,
                    run=False,
                )

    def _apply_init_deadband(self, *args, connected: bool = False, **kwargs) -> None:
        """Write the requested deadband to ``.RDBD`` once, when connected.

        Idempotent: only the first successful apply writes. A failed put is
        logged at WARNING and swallowed so device init / scans never break.
        Skips the put when ``.RDBD`` already matches the requested value.
        """
        if self._deadband_applied or not connected or self._init_deadband is None:
            return
        try:
            current = self.retry_deadband.get()
            if current is None or abs(current - self._init_deadband) > 1e-9:
                self.retry_deadband.put(self._init_deadband)
            self._deadband_applied = True
        except Exception:
            logger.warning(
                "%s: could not apply init deadband %s to .RDBD.",
                self.name,
                self._init_deadband,
                exc_info=True,
            )

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

    Known trade-offs
    ----------------
    * ``move()`` returns a generic ``ophyd.status.Status``, not a
      ``MoveStatus``, so per-move progress (``status.watch()``) is not
      reported to the RunEngine.
    * ``stop()`` errors any in-flight move; the RunEngine calls ``stop`` on
      pause/suspend as well as abort, though in practice the move's status is
      already done by then.
    """

    max_attempts: int = 5
    stall_timeout: float = 3.0
    settle_timeout: float = 60.0
    poll_interval: float = 0.1
    start_timeout: float = 1.0

    # -- seam methods (mocked in tests; real impls touch EPICS) --------------
    def _read_position(self) -> float:
        return self.user_readback.get()

    def _motion_complete(self) -> bool:
        return bool(self.motor_done_move.get())

    def _issue_setpoint(self, position: float) -> None:
        self.user_setpoint.put(position, wait=False)

    # -- dialog seam ----------------------------------------------------------
    def _prompt_user_continue(self, attempts: int, position: float) -> bool:
        """Ask the user whether to keep retrying. Returns True to continue.

        Blocks the calling (recovery) thread until the GUI thread answers.
        With no running QApplication, logs CRITICAL and fails the move.
        """
        try:
            from PySide6.QtWidgets import QApplication
        except Exception:
            QApplication = None

        if QApplication is None or QApplication.instance() is None:
            logger.critical(
                "%s: jammed and failed to reach %s after %d recovery "
                "attempt(s); no GUI available to prompt, failing the move.",
                self.name, position, attempts,
            )
            return False

        from lightfall.utils.threads import invoke_in_main_thread

        event = threading.Event()
        result = {"continue": False}
        self._prompt_event = event
        try:
            invoke_in_main_thread(
                self._show_jam_dialog, attempts, position, result, event
            )
            event.wait()
        finally:
            self._prompt_event = None
        if self._jam_abort.is_set():
            return False
        return result["continue"]

    def _show_jam_dialog(self, attempts, position, result, event):
        from lightfall.utils.crash_diagnostics import gui_thread_only

        @gui_thread_only
        def _run():
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox()
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle("Beamline energy jam")
            box.setText(
                f"{self.name} could not reach {position} after "
                f"{attempts} recovery attempt(s)."
            )
            box.setInformativeText(
                "Keep retrying (another batch of recovery attempts), "
                "or treat this move as a failure?"
            )
            retry_btn = box.addButton(
                "Keep retrying", QMessageBox.ButtonRole.AcceptRole
            )
            box.addButton("Fail move", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            result["continue"] = box.clickedButton() is retry_btn

        try:
            _run()
        finally:
            event.set()

    # -- recovery internals --------------------------------------------------
    def _attempt_and_watch(self, position: float, status: Status) -> str:
        """Issue the setpoint and watch until target reached or a jam.

        Returns 'reached' or 'jammed'. Honors status.done (timeout/abort).

        A ``start_timeout`` grace window is allowed after the setpoint put
        before DMOV=1 (idle) is treated as a settled-off-target jam. On real
        EPICS the motor record can briefly remain DMOV=1 after a ``.VAL`` put
        before motion begins; treating that as jammed would trigger a spurious
        recovery re-put on every healthy move.
        """
        tolerance = self._get_move_tolerance()
        self._issue_setpoint(position)
        last_pos = self._read_position()
        loop_start = time.monotonic()
        last_progress = loop_start
        start_deadline = loop_start + self.start_timeout
        deadline = loop_start + self.settle_timeout
        started = False  # True once DMOV=0 (moving) is observed

        while not status.done and not self._jam_abort.is_set():
            time.sleep(self.poll_interval)
            now = time.monotonic()
            pos = self._read_position()

            # Target reached — success regardless of DMOV.
            if abs(position - pos) <= tolerance:
                return "reached"

            moving = not self._motion_complete()
            if moving:
                started = True
                if abs(pos - last_pos) > tolerance:
                    last_pos = pos
                    last_progress = now
                if now - last_progress >= self.stall_timeout:
                    return "jammed"  # no progress while still "moving"
            else:
                # DMOV=1 (idle): only diagnose as settled-off-target once
                # motion has been observed OR the start-grace window is over.
                if started or now >= start_deadline:
                    return "jammed"  # settled short of target

            if now >= deadline:
                return "jammed"  # overall settle ceiling

        if self._jam_abort.is_set() and not status.done:
            status.set_exception(RuntimeError(f"{self.name}: move aborted."))
        return "jammed"

    def _run_recovery(self, position: float, status: Status, moved_cb) -> None:
        try:
            attempts = 0
            while not status.done and not self._jam_abort.is_set():
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

                for _ in range(self.max_attempts):
                    if status.done or self._jam_abort.is_set():
                        break
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

                if self._jam_abort.is_set():
                    break

                # Batch exhausted: ask the user whether to keep retrying.
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

            if not status.done:
                status.set_exception(RuntimeError(f"{self.name}: move aborted."))
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
            self._jam_abort = threading.Event()
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

    def stop(self, *, success: bool = False):
        """Stop the motor and halt any in-progress jam recovery."""
        abort = getattr(self, "_jam_abort", None)
        if abort is not None:
            abort.set()
        prompt_event = getattr(self, "_prompt_event", None)
        if prompt_event is not None:
            prompt_event.set()
        try:
            super().stop(success=success)
        except Exception:
            logger.exception("%s: error while stopping underlying motor.", self.name)
