"""Andor camera device class.

Provides an ophyd device for Andor cameras with HDF5 file writing,
SWMR support, and shutter control.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict

import numpy as np
from ophyd import (
    AndorDetector,
    AndorDetectorCam,
    EpicsSignalRO,
    EpicsSignalWithRBV,
    HDF5Plugin,
    ImagePlugin,
    SingleTrigger,
    Staged,
)
from ophyd.areadetector.base import ADBase, ADComponent as C
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


class AndorWarmupFix(HDF5Plugin):
    """HDF5 plugin mixin that warms up the detector before staging.

    The HDF5 plugin needs to 'see' one acquisition before it's ready to
    capture. This mixin detects if the plugin hasn't been warmed up and
    runs a quick acquisition cycle.
    """

    @property
    def _warmed_up(self) -> bool:
        return np.array(self.array_size.get()).sum() > 0

    def stage(self):
        if not self._warmed_up:
            self.warmup()
        return super().stage()

    def warmup(self):
        """Prime the plugin with one acquisition."""
        set_and_wait(self.enable, 1)
        sigs = OrderedDict(
            [
                (self.parent.cam.array_callbacks, 1),
                (self.parent.cam.image_mode, "Single"),
                (self.parent.cam.trigger_mode, "Internal"),
                (self.parent.cam.acquire_time, 1),
                (self.parent.cam.acquire, 1),
            ]
        )

        original_vals = {sig: sig.get() for sig in sigs}

        for sig, val in sigs.items():
            time.sleep(0.1)
            set_and_wait(sig, val)

        time.sleep(10)  # wait for acquisition

        for sig, val in reversed(list(original_vals.items())):
            time.sleep(0.1)
            set_and_wait(sig, val)


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


class HDF5PluginWithFileStore(
    FramesPerPointNumImages, AndorWarmupFix, HDF5PluginSWMR, FileStoreHDF5IterativeWrite
):
    """Complete HDF5 plugin with file store, warmup fix, and SWMR."""

    pass


class StageOnFirstTrigger(ADBase):
    """Mixin that auto-stages on first trigger if not already staged."""

    def trigger(self):
        if self._staged == Staged.no:
            self.stage()
        return super().trigger()


class KeepOpenClosed(AndorDetectorCam):
    """Andor camera mixin with shutter control methods."""

    modes = ["normal", "open", "closed"]  # encodes order of modes

    def get_shutter_mode(self) -> tuple[int, str]:
        """Get the current shutter mode as (index, name)."""
        mode = self.andor_shutter_mode.get()
        return mode, self.modes[mode]

    def keep_closed(self):
        """Keep shutter closed (for dark frames)."""
        set_and_wait(self.andor_shutter_mode, self.modes.index("closed"))

    def keep_open(self):
        """Keep shutter open."""
        set_and_wait(self.andor_shutter_mode, self.modes.index("open"))

    def shutter_normally(self):
        """Return to normal shutter operation."""
        set_and_wait(self.andor_shutter_mode, self.modes.index("normal"))


class TempfixAndorDetectorCam(KeepOpenClosed):
    """Andor camera with fixed temperature status (force string value)."""

    andor_temp_status = C(EpicsSignalRO, "AndorTempStatus_RBV", string=True)


class Andor(StageOnFirstTrigger, SingleTrigger, AndorDetector):
    """Complete Andor camera device with HDF5 file writing.

    Includes:
    - HDF5 file writing with SWMR support
    - Auto-warmup for HDF5 plugin
    - Shutter control (normal/open/closed)
    - ROI statistics plugin
    - Transform plugin

    Example
    -------
    ::

        andor = Andor("13ANDOR1:", name="andor")
        andor.hdf5.reg = db.reg  # set databroker registry

        # Configure file paths
        andor.hdf5.write_path_template = "/data/andor/%Y/%m/%d/"
        andor.hdf5.root = "/data/"

        # Take acquisition
        RE(count([andor]))
    """

    _default_read_attrs = ["hdf5", "cam", "roi_stat1"]

    cam = C(TempfixAndorDetectorCam, "cam1:")
    trans1 = C(TransformPlugin, "Trans1:")
    image1 = C(ImagePlugin, "image1:")
    roi_stat1 = C(ROIStatNPlugin_V23, "ROIStat1:1:")
    hdf5 = C(
        HDF5PluginWithFileStore,
        "HDF1:",
        write_path_template="/data/andor/%Y/%m/%d/",
        root="/data/",
        reg=None,  # placeholder to be set on instance
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stage_sigs.update({"cam.image_mode": 1})

    def stage(self):
        if self._staged in [Staged.yes, Staged.partially]:
            self.unstage()
        return super().stage()

    def stop(self, *, success=False):
        self._acquisition_signal.put(0)
        logger.info("%s: stopping acquisition", self.name)
        return super().stop()
