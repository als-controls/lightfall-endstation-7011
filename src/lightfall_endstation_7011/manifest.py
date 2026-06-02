"""Plugin manifest for 7.0.1.1 endstation.

Defines all LUCID plugins provided by this package:
- Controller plugins for Andor and PIMTE cameras
- Controller plugin for detector diodes
- Agent plugin for the Blackfly observer skill (discover_blackfly_cameras + workflow prompt)
- Agent plugin for the reflection-alignment skill (check_beam + fit_lift_halfcut + fit_theta_peak)
- Agent plugin for the endstation user-support skill (detector-no-signal triage)
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
        # Agent plugin: Blackfly observer skill (discover + panel-creation workflow)
        PluginEntry(
            type_name="agent",
            name="blackfly",
            import_path="lucid_endstation_7011.observers.blackfly.skill:BlackflyAgent",
            metadata={"priority": 30},
        ),
        # Agent plugin: endstation user-support triage skill
        PluginEntry(
            type_name="agent",
            name="endstation_support",
            import_path="lucid_endstation_7011.support.skill:EndstationSupportAgent",
            metadata={"priority": 20},
        ),
        # Agent plugin: reflection-geometry sample alignment skill
        PluginEntry(
            type_name="agent",
            name="reflection_alignment",
            import_path="lucid_endstation_7011.alignment.skill:ReflectionAlignmentAgent",
            metadata={"priority": 30},
        ),
    ],
)
