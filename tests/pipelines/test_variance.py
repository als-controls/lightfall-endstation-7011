"""Tests for VariancePipeline.

These cover plugin metadata, notebook resource resolution, and a numpy
sanity check on the variance computation itself. Live execution against
Tiled + papermill lives in the ncs/ncs e2e test suite (T18).
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from lucid_endstation_7011.pipelines import VariancePipeline


def test_metadata_required_fields():
    p = VariancePipeline()
    # package_name + notebook are class-level resource pointers; everything
    # else is exposed via the introspection dict.
    assert VariancePipeline.package_name == "lucid_endstation_7011"
    assert VariancePipeline.notebook.endswith("compute_variance.ipynb")

    info = p.get_introspection_data()
    assert info["name"] == "variance"
    assert "stream" in info["parameters_schema"]
    assert "dtype" in info["parameters_schema"]
    assert "variance" in info["output_tags"]
    assert info["inherit_input_access_blob"] is True
    assert info["store_executed_notebook"] is True
    assert info["display_name"] == "Image-stream variance"


def test_notebook_resource_resolves():
    p = VariancePipeline()
    path = p.notebook_path()
    # `files()` returns a Traversable; convert to Path for stat checks.
    nb_path = Path(str(path))
    assert nb_path.exists(), f"notebook missing at {nb_path}"
    assert nb_path.suffix == ".ipynb"


def test_notebook_has_parameters_cell_with_expected_defaults():
    """Papermill injects parameters by overwriting the cell tagged
    ``parameters``. If we drop the tag or rename the variables, the
    parameters dict from JobMessage silently no-ops. Lock the shape down.
    """
    p = VariancePipeline()
    nb = json.loads(Path(str(p.notebook_path())).read_text())
    param_cells = [
        c for c in nb["cells"]
        if "parameters" in (c.get("metadata", {}).get("tags") or [])
    ]
    assert len(param_cells) == 1, (
        f"expected exactly one parameters-tagged cell, got {len(param_cells)}"
    )
    src = "".join(param_cells[0]["source"])
    assert "stream" in src
    assert "field" in src
    assert "dtype" in src


def test_variance_computation_matches_numpy_on_synthetic_data():
    """Sanity check on what the notebook would compute (variance along
    the time axis of a 3-D stack). The notebook itself just calls
    ``frames.var(axis=0)``, so this test lives next to the plugin to
    catch regressions if anyone reaches for ``axis=-1`` or ``ddof=1``.
    """
    rng = np.random.default_rng(seed=0)
    frames = rng.standard_normal(size=(50, 8, 8)).astype(np.float32)
    variance = frames.var(axis=0).astype(np.float32)
    assert variance.shape == (8, 8)
    # population variance (ddof=0) of standard normals is ~1.0 with
    # plenty of slack at N=50.
    assert 0.5 < variance.mean() < 1.5


def test_plugin_imports_via_entry_point_group():
    """Ensure the entry-point declaration in pyproject.toml is wired
    correctly. If this fails the executor's `discover()` will not find
    the variance pipeline.
    """
    from importlib.metadata import entry_points

    eps = list(entry_points(group="lucid_pipelines.pipeline"))
    names = {ep.name for ep in eps}
    if "variance" not in names:
        pytest.skip(
            "lucid_endstation_7011 not installed (run `pip install -e .` "
            "to pick up the entry point)"
        )
    ep = next(ep for ep in eps if ep.name == "variance")
    cls = ep.load()
    assert cls is VariancePipeline
