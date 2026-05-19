"""Per-pixel variance over a time-series of detector images.

A trivial pipeline used as both a proof-of-concept and a coherent-speckle
diagnostic: pixels whose intensity varies far from Poisson statistics are
candidate speckle locations. The computation itself is one line of numpy;
the value is the round-trip (Tiled read -> compute -> Tiled write with
access_blob inheritance) for the beamline's own data.
"""
from __future__ import annotations

from lucid_pipelines.plugin import PipelinePlugin


class VariancePipeline(PipelinePlugin):
    """Compute per-pixel variance across the time axis of an image stream.

    Inputs:
        The pipeline reads ``LUCID_INPUT_RUN_UID`` from the executor env
        and pulls the configured image stream from Tiled. The stream is
        expected to be a 3-D array of shape ``(N_frames, H, W)``.

    Outputs:
        A single new run is written back to Tiled containing one image of
        shape ``(H, W)`` named ``variance``. The run inherits the input
        access blob so the result is visible to the same proposal /
        participants as the original data.

    Parameters:
        stream:
            Name of the Tiled child under the input run that holds the
            image array. Defaults to ``primary``.
        dtype:
            numpy dtype string for the output. Defaults to ``float32``.
            ``float64`` is also reasonable when downstream consumers want
            it; cast happens before the write.
    """

    name = "variance"
    description = (
        "Per-pixel variance across the time axis of an image stream. "
        "Useful as a quick speckle / hot-pixel diagnostic."
    )
    display_name = "Image-stream variance"
    parameters_schema = {
        "stream": {
            "type": "string",
            "default": "primary",
            "description": "Tiled child name for the image array",
        },
        "dtype": {
            "type": "string",
            "default": "float32",
            "description": "Output dtype (numpy string)",
        },
    }
    output_tags = ["variance"]
    notebook = "pipelines/notebooks/compute_variance.ipynb"
    package_name = "lucid_endstation_7011"
    inherit_input_access_blob = True
    store_executed_notebook = True
    timeout_seconds = 600
