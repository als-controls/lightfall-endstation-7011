"""Detector diode device class."""

from __future__ import annotations

from ophyd import Component as Cpt
from ophyd import Device
from ophyd.signal import EpicsSignalRO


class DetectorDiode(Device):
    """Simple photodiode detector for beam intensity monitoring.

    Reads the diode current from an EPICS AI record.

    Example
    -------
    ::

        diode = DetectorDiode("7011:diode", name="diode")
        intensity = diode.diode.get()
    """

    diode = Cpt(EpicsSignalRO, ".VAL", kind="hinted")

    _default_read_attrs = ["diode"]
