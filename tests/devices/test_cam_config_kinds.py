"""Cam ROI/binning/size signals must land in the configuration document."""
from __future__ import annotations

import pytest
from ophyd.sim import make_fake_device

from lightfall_endstation_7011.devices.andor import Andor
from lightfall_endstation_7011.devices.pimte3 import PIMTE3

EXPECTED = {
    "det_cam_min_x",
    "det_cam_min_y",
    "det_cam_bin_x",
    "det_cam_bin_y",
    "det_cam_size_size_x",
    "det_cam_size_size_y",
    "det_cam_max_size_max_size_x",
    "det_cam_max_size_max_size_y",
}


@pytest.mark.parametrize("cls", [Andor, PIMTE3])
def test_cam_roi_signals_in_configuration(cls):
    det = make_fake_device(cls)("FAKE:", name="det")
    missing = EXPECTED - set(det.read_configuration())
    assert not missing, f"{cls.__name__} configuration lacks {sorted(missing)}"
