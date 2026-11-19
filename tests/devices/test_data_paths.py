"""Detector files are written under /data/<beamline>/<detector>/..."""
from __future__ import annotations

import logging

import pytest
from ophyd.sim import make_fake_device

from lightfall_endstation_7011.devices import paths
from lightfall_endstation_7011.devices.andor import Andor
from lightfall_endstation_7011.devices.pimte3 import PIMTE3


@pytest.fixture(autouse=True)
def _reset_warned():
    paths._warned = False
    yield
    paths._warned = False


def _set_beamline(monkeypatch, value):
    monkeypatch.setattr(paths, "_beamline", lambda: value)


def test_template_with_beamline(monkeypatch):
    _set_beamline(monkeypatch, "7.0.1.1")
    assert paths.data_write_template("andor") == "/data/7.0.1.1/andor/%Y/%m/%d/"


def test_template_without_beamline_falls_back(monkeypatch, caplog):
    _set_beamline(monkeypatch, "")
    with caplog.at_level(logging.WARNING, logger=paths.logger.name):
        assert paths.data_write_template("andor") == "/data/andor/%Y/%m/%d/"
        paths.data_write_template("pimte")
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1  # once only


@pytest.mark.parametrize("cls,det", [(Andor, "andor"), (PIMTE3, "pimte")])
def test_andor_and_pimte_use_template(monkeypatch, cls, det):
    _set_beamline(monkeypatch, "7.0.1.1")
    dev = make_fake_device(cls)("FAKE:", name="det")
    assert dev.hdf5.write_path_template == f"/data/7.0.1.1/{det}/%Y/%m/%d/".rstrip("/")
    assert str(dev.hdf5.reg_root).replace("\\", "/") == "/data"
    if cls is PIMTE3:
        assert dev.hdf5.read_path_template.rstrip("/") == f"/data/7.0.1.1/{det}/%Y/%m/%d/".rstrip("/")
