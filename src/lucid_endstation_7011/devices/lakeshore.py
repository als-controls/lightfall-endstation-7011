"""LakeShore temperature controller device classes.

Based on the LakeShore 336 temperature controller.
https://www.lakeshore.com/docs/default-source/product-downloads/336_manual.pdf
"""

from __future__ import annotations

from ophyd import Component as Cpt
from ophyd import Device
from ophyd.areadetector.base import EpicsSignalWithRBV
from ophyd.signal import EpicsSignal, EpicsSignalRO


class LakeShore336(Device):
    """Ophyd Device for the LakeShore 336 temperature controller.

    Based on lakeshore336_ioc:
    https://github.com/lbl-camera/fastccd_support_ioc/blob/lakeshore336/fastccd_support_ioc/lakeshore336_ioc.py

    Provides temperature readback for two channels (A and B) in both
    Celsius and Kelvin, heater output monitoring, temperature limits,
    and setpoint control.

    Example
    -------
    ::

        lakeshore = LakeShore336("7011:LS336:", name="lakeshore")
        print(f"Temperature A: {lakeshore.temp_celsius_A.get()} °C")
        lakeshore.temp_set_point.set(25.0)  # Set to 25°C
    """

    # Read-only temperature sensors
    temp_celsius_A = Cpt(EpicsSignalRO, "TemperatureCelsiusA", kind="hinted")
    temp_kelvin_A = Cpt(EpicsSignalRO, "TemperatureKelvinA", kind="normal")
    temp_celsius_B = Cpt(EpicsSignalRO, "TemperatureCelsiusB", kind="normal")
    temp_kelvin_B = Cpt(EpicsSignalRO, "TemperatureKelvinB", kind="normal")

    # Heater status
    heater_output = Cpt(EpicsSignalRO, "HeaterOutput", kind="normal")

    # Temperature limits (read-write with readback)
    temp_limit_A = Cpt(EpicsSignalWithRBV, "TemperatureLimitA", kind="config")
    temp_limit_B = Cpt(EpicsSignalWithRBV, "TemperatureLimitB", kind="config")

    # Temperature setpoint
    temp_set_point = Cpt(EpicsSignalWithRBV, "TemperatureSetPoint", kind="hinted")

    _default_read_attrs = ["temp_celsius_A", "temp_set_point", "heater_output"]
    _default_configuration_attrs = ["temp_limit_A", "temp_limit_B"]
