"""Detector write-path templates keyed on the beamline id.

The data-movement service copies files relative to ``/data``, so writing
to ``/data/<beamline>/<detector>/%Y/%m/%d/`` gives every site distinct,
identical paths on all destinations.

The IOC must find ``/data/<beamline>/<detector>/`` already in place: the
HDF5 plugin stages with ``create_directory = -3``, so the IOC creates only
the ``%Y/%m/%d`` levels, and a missing parent fails staging with
``Path ... does not exist on IOC``.  Create it on the IOC host, owned by
the user the IOC runs as (e.g. ``/data/7.0.1.1/andor`` and
``/data/7.0.1.1/pimte`` on suzume, where ``picam.service`` runs as root),
before setting the ``tiled_beamline`` preference.
"""

from __future__ import annotations

import logging

from ophyd.areadetector.base import ADComponent

logger = logging.getLogger(__name__)

_warned = False


def _beamline() -> str:
    """The ``tiled_beamline`` preference (e.g. ``"7.0.1.1"``), or ``""``."""
    try:
        from lightfall.ui.preferences.manager import PreferencesManager

        return str(PreferencesManager.get_instance().get("tiled_beamline", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def data_write_template(detector: str, root: str = "/data/") -> str:
    """Return ``<root><beamline>/<detector>/%Y/%m/%d/``.

    Falls back to ``<root><detector>/%Y/%m/%d/`` (warning once) when the
    ``tiled_beamline`` preference is unset.
    """
    global _warned
    beamline = _beamline()
    if not beamline:
        if not _warned:
            _warned = True
            logger.warning(
                "tiled_beamline preference is unset; %s files will be written "
                "without a beamline directory under %s",
                detector,
                root,
            )
        return f"{root}{detector}/%Y/%m/%d/"
    return f"{root}{beamline}/{detector}/%Y/%m/%d/"


class BeamlineDataComponent(ADComponent):
    """HDF5-plugin component whose write path carries the beamline id.

    The template is applied when the (lazy) plugin is instantiated, so the
    ``tiled_beamline`` preference is read at use rather than at import, and
    constructing the device does not eagerly instantiate the plugin.
    """

    def __init__(self, cls, suffix=None, *, detector: str, **kwargs):
        super().__init__(cls, suffix, **kwargs)
        self._detector = detector

    def create_component(self, instance):
        plugin = super().create_component(instance)
        plugin.write_path_template = data_write_template(self._detector)
        return plugin
