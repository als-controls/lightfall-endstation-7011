"""Notebook pipelines for beamline 7.0.1.1.

Each pipeline is a :class:`lucid_pipelines.plugin.PipelinePlugin` subclass
paired with a papermill notebook bundled under ``notebooks/``.

Discovered by the lucid-pipelines executor via the
``lucid_pipelines.pipeline`` entry-point group (declared in pyproject.toml).
"""
from lucid_endstation_7011.pipelines.variance import VariancePipeline

__all__ = ["VariancePipeline"]
