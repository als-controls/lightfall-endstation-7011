"""Plugin manifest for 7.0.1.1 endstation.

Defines all LUCID plugins provided by this package:
- Controller plugins for Andor and PIMTE cameras
- Controller plugin for detector diodes
"""

from lucid.plugins.manifest import PluginEntry, PluginManifest

manifest = PluginManifest(
    name="lucid-endstation-7011",
    version="0.1.0",
    description="LUCID plugins for ALS Beamline 7.0.1.1 endstation",
    plugins=[
        # Controller plugins for camera widgets
        PluginEntry(
            type_name="controller",
            name="andor_camera",
            import_path="lucid_endstation_7011.widgets.andor:AndorControllerPlugin",
            metadata={"priority": 150},
        ),
        PluginEntry(
            type_name="controller",
            name="pimte_camera",
            import_path="lucid_endstation_7011.widgets.pimte:PIMTEControllerPlugin",
            metadata={"priority": 150},
        ),
        # Controller plugin for detector diodes
        PluginEntry(
            type_name="controller",
            name="detector_diode",
            import_path="lucid_endstation_7011.widgets.diode:DiodeControllerPlugin",
            metadata={"priority": 100},
        ),
    ],
)
