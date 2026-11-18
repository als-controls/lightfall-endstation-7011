"""Princeton PIMTE3 camera device class.

Provides an ophyd device for Princeton Instruments MTE3 cameras with
HDF5 file writing and SWMR support.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import PurePosixPath

from ophyd import (
    Component as Cpt,
    DetectorBase,
    EpicsSignalRO,
    EpicsSignalWithRBV,
    HDF5Plugin,
    ImagePlugin,
    Kind,
    SingleTrigger,
    Staged,
)
from ophyd.areadetector.base import ADBase, ADComponent as C
from ophyd.areadetector.cam import AreaDetectorCam
from ophyd.areadetector.filestore_mixins import (
    FileStoreHDF5IterativeWrite,
    FileStorePluginBase,
    resource_factory,
)
from ophyd.areadetector.plugins import ROIStatNPlugin_V23, TransformPlugin
from ophyd.utils import set_and_wait

from .paths import BeamlineDataComponent

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
    """Complete HDF5 plugin with file store and SWMR.

    Forces POSIX path semantics throughout so that IOC-side Linux paths
    (e.g. /data/) work correctly when the client runs on Windows.

    Upstream ophyd uses platform-aware PurePath in several places
    (reg_root, read_path_template, _generate_resource) which breaks
    cross-platform usage.  See: https://github.com/bluesky/ophyd/issues/XXXX
    """

    path_semantics = "posix"

    @FileStorePluginBase.reg_root.setter
    def reg_root(self, val):
        if val is None:
            val = "/"
        root = PurePosixPath(val)
        if not root.is_absolute():
            raise ValueError(
                f"The root part of the path must be absolute not {root=}."
            )
        self._root = root

    @property
    def read_path_template(self):
        if self._read_path_template is None:
            ret = PurePosixPath(self.write_path_template)
        else:
            ret = PurePosixPath(self._read_path_template)
        ret = self._ensure_absolute_under_root(ret)
        return str(ret) + "/"

    @read_path_template.setter
    def read_path_template(self, val):
        if val is not None:
            val = str(PurePosixPath(val)) + "/"
        self._read_path_template = val

    def _generate_resource(self, resource_kwargs):
        """Override to use PurePosixPath for fn.relative_to()."""
        fn = PurePosixPath(self._fn).relative_to(self.reg_root)
        resource, self._datum_factory = resource_factory(
            spec=self.filestore_spec,
            root=str(self.reg_root),
            resource_path=str(fn),
            resource_kwargs=resource_kwargs,
            path_semantics=self.path_semantics,
        )

        if self._reg is not None:
            self._resource_uid = self._reg.register_resource(
                rpath=resource["resource_path"],
                rkwargs=resource["resource_kwargs"],
                root=resource["root"],
                spec=resource["spec"],
                path_semantics=resource["path_semantics"],
            )
            resource["uid"] = self._resource_uid

        self._resource_uid = resource["uid"]
        self._asset_docs_cache.append(("resource", resource))


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
    # Override the generic CamBase `shutter_mode` (which targets the
    # "ShutterMode" PV) so the device-panel dropdown drives the PIMTE's
    # "ShutterTimingMode" PV instead.
    shutter_mode = Cpt(EpicsSignalWithRBV, "ShutterTimingMode")

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
        pimte.hdf5.write_path_template = "/data/<beamline>/pimte/%Y/%m/%d/"
        pimte.hdf5.root = "/data/"

        # Take acquisition
        RE(count([pimte]))
    """

    _default_read_attrs = ["hdf5", "cam", "roi_stat1"]

    cam = C(PIMTE3Cam, "cam1:")
    image1 = C(ImagePlugin, "image1:")
    trans1 = C(TransformPlugin, "Trans1:")
    roi_stat1 = C(ROIStatNPlugin_V23, "ROIStat1:1:")
    hdf5 = BeamlineDataComponent(
        HDF5PluginWithFileStore,
        "HDF1:",
        detector="pimte",
        write_path_template="/data/pimte/%Y/%m/%d/",
        root="/data/",
        reg=None,  # placeholder to be set on instance
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Do not assert an image mode on stage. Each plan is responsible
        # for selecting the camera image mode it needs at its start
        # (e.g. "Multiple" for step scans, "Continuous" for
        # continuous_acquire). Dropping the default stage sig keeps staging
        # from clobbering the mode the plan deliberately set.
        self.stage_sigs.pop("cam.image_mode", None)
        # Surface the full-frame summed counts (ROIStat total) as the
        # hinted field so live plots / best-effort callbacks default to it.
        self.roi_stat1.total.kind = Kind.hinted
        # Make ROI/binning/size settings visible in configuration documents.
        # AreaDetectorCam._default_read_attrs=() strips Kind.normal from all cam signals
        # at instantiation. Use _set_kind for flat signals, and update _component_kinds
        # directly for DDC containers BEFORE accessing them.

        # Flat cam signals
        for attr in ['min_x', 'min_y', 'bin_x', 'bin_y']:
            self.cam._set_kind(attr, Kind.config)

        # DDC containers: update _component_kinds so kind persists if re-instantiated,
        # AND set the instance kind directly (the DDC is pre-instantiated by super().__init__
        # with Kind.omitted before this fix code runs).
        self.cam._component_kinds['size'] = Kind.config
        self.cam._component_kinds['max_size'] = Kind.config
        self.cam.size.kind = Kind.config
        self.cam.max_size.kind = Kind.config
        # Fix sub-signal kinds within each DDC
        self.cam.size._set_kind('size_x', Kind.config)
        self.cam.size._set_kind('size_y', Kind.config)
        self.cam.max_size._set_kind('max_size_x', Kind.config)
        self.cam.max_size._set_kind('max_size_y', Kind.config)

    def stage(self):
        if self._staged == Staged.yes:
            self.unstage()
        return super().stage()

    def stop(self, *, success=False):
        self._acquisition_signal.put(0)
        logger.info("%s: stopping acquisition", self.name)
        return super().stop()
