"""Princeton PIMTE3 camera device class.

Provides an ophyd device for Princeton Instruments MTE3 cameras with
HDF5 file writing and SWMR support.
"""

from __future__ import annotations

import logging
import time

from ophyd import (
    Component as Cpt,
    DetectorBase,
    EpicsSignalRO,
    EpicsSignalWithRBV,
    HDF5Plugin,
    ImagePlugin,
    SingleTrigger,
    Staged,
)
from ophyd.areadetector.base import ADBase, ADComponent as C
from ophyd.areadetector.cam import AreaDetectorCam
from ophyd.areadetector.filestore_mixins import (
    FileStoreHDF5IterativeWrite,
    FileStorePluginBase,
)
from ophyd.areadetector.plugins import ROIStatNPlugin_V23, TransformPlugin
from ophyd.utils import set_and_wait

logger = logging.getLogger(__name__)


class FramesPerPointNumImages(FileStorePluginBase):
    """Mixin that sets frames_per_point from cam.num_images."""

    def get_frames_per_point(self):
        return self.parent.cam.num_images.get()


class HDF5PluginSWMR(HDF5Plugin):
    """HDF5 plugin with SWMR (Single Writer Multiple Reader) support."""

    swmr_active = C(EpicsSignalRO, "SWMRActive_RBV")
    swmr_mode = C(EpicsSignalWithRBV, "SWMRMode")
    swmr_supported = C(EpicsSignalRO, "SWMRSupported_RBV")
    swmr_cb_counter = C(EpicsSignalRO, "SWMRCbCounter_RBV")

    _default_configuration_attrs = HDF5Plugin._default_configuration_attrs + (
        "swmr_active",
        "swmr_mode",
        "swmr_supported",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage_sigs["swmr_mode"] = 1


class HDF5PluginWithFileStore(FramesPerPointNumImages, HDF5PluginSWMR, FileStoreHDF5IterativeWrite):
    """Complete HDF5 plugin with file store and SWMR."""

    pass


class StageOnFirstTrigger(ADBase):
    """Mixin that auto-stages on first trigger if not already staged."""

    def trigger(self):
        if self._staged == Staged.no:
            self.stage()
        return super().trigger()


def _try_set_and_wait(signal, value, attempts: int = 5, **kwargs):
    """Set a signal with retries on timeout."""
    for i in range(attempts):
        try:
            set_and_wait(signal, value, **kwargs)
        except (TimeoutError, IndexError):
            logger.warning("%s not responding; waiting 100ms and trying again...", signal)
            time.sleep(0.1)
        else:
            break
    else:
        raise RuntimeError(f"Unable to set {signal} to {value}")


class KeepOpenClosed(AreaDetectorCam):
    """PIMTE camera mixin with shutter control methods."""

    readout_time = Cpt(EpicsSignalRO, "ReadoutTimeCalc")
    shutter_timing_mode = Cpt(EpicsSignalWithRBV, "ShutterTimingMode")

    _default_configuration_attrs = AreaDetectorCam._default_configuration_attrs + (
        "shutter_timing_mode",
        "readout_time",
    )

    modes = ["normal", "closed", "open"]  # encodes order of modes

    def get_shutter_mode(self) -> tuple[int, str]:
        """Get the current shutter mode as (index, name)."""
        mode = self.shutter_timing_mode.get()
        return mode, self.modes[mode]

    def keep_closed(self):
        """Keep shutter closed (for dark frames)."""
        self.shutter_timing_mode.put(self.modes.index("closed"))
        _try_set_and_wait(self.shutter_timing_mode, self.modes.index("closed"), timeout=1)

    def keep_open(self):
        """Keep shutter open."""
        self.shutter_timing_mode.put(self.modes.index("open"))
        _try_set_and_wait(self.shutter_timing_mode, self.modes.index("open"), timeout=1)

    def shutter_normally(self):
        """Return to normal shutter operation."""
        self.shutter_timing_mode.put(self.modes.index("normal"))
        _try_set_and_wait(self.shutter_timing_mode, self.modes.index("normal"), timeout=1)


class PIMTE3Cam(KeepOpenClosed):
    """Princeton PIMTE3 camera component."""

    pass


class PIMTE3(StageOnFirstTrigger, SingleTrigger, DetectorBase):
    """Princeton PIMTE3 camera device with HDF5 file writing.

    Includes:
    - HDF5 file writing with SWMR support
    - Shutter control (normal/open/closed)
    - ROI statistics plugin
    - Transform plugin

    Example
    -------
    ::

        pimte = PIMTE3("13PIMTE1:", name="pimte")
        pimte.hdf5.reg = db.reg  # set databroker registry

        # Configure file paths
        pimte.hdf5.write_path_template = "/data/pimte/%Y/%m/%d/"
        pimte.hdf5.root = "/data/"

        # Take acquisition
        RE(count([pimte]))
    """

    _default_read_attrs = ["hdf5", "cam", "roi_stat1"]

    cam = C(PIMTE3Cam, "cam1:")
    image1 = C(ImagePlugin, "image1:")
    trans1 = C(TransformPlugin, "Trans1:")
    roi_stat1 = C(ROIStatNPlugin_V23, "ROIStat1:1:")
    hdf5 = C(
        HDF5PluginWithFileStore,
        "HDF1:",
        write_path_template="/data/pimte/%Y/%m/%d/",
        root="/data/",
        reg=None,  # placeholder to be set on instance
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage_sigs.pop("cam.image_mode", None)

    def stage(self):
        if self._staged == Staged.yes:
            self.unstage()
        return super().stage()

    def stop(self, *, success=False):
        self._acquisition_signal.put(0)
        logger.info("%s: stopping acquisition", self.name)
        return super().stop()
