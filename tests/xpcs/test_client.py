from lightfall_endstation_7011.xpcs.client import XPCSClient
from lightfall_endstation_7011.xpcs.shapes import RectShape


def test_actions_map_to_subjects(fake_ipc):
    c = XPCSClient(ipc=fake_ipc)
    fake_ipc.replies = {s: {"status": "ok"} for s in (
        "xpcs.processing.enable", "xpcs.processing.disable", "xpcs.reset",
        "xpcs.roi.set", "xpcs.roi.remove", "xpcs.roi.clear",
        "xpcs.mask.set", "xpcs.mask.clear",
    )}
    c.enable(); c.disable(); c.reset()
    c.set_roi("r1", RectShape(0, 0, 2, 2))
    c.remove_roi("r1"); c.clear_rois()
    c.set_mask([RectShape(0, 0, 1, 1)]); c.clear_mask()
    subjects = [s for s, _ in fake_ipc.requests]
    assert subjects == [
        "xpcs.processing.enable", "xpcs.processing.disable", "xpcs.reset",
        "xpcs.roi.set", "xpcs.roi.remove", "xpcs.roi.clear",
        "xpcs.mask.set", "xpcs.mask.clear",
    ]
    roi_payload = fake_ipc.requests[3][1]
    assert roi_payload == {"roi_id": "r1",
                           "shape": {"type": "rect", "x": 0.0, "y": 0.0, "w": 2.0, "h": 2.0}}
    mask_payload = fake_ipc.requests[6][1]
    assert mask_payload == {"shapes": [{"type": "rect", "x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}]}


def test_bind_and_stop_publish(fake_ipc):
    c = XPCSClient(ipc=fake_ipc)
    c.bind_run("uid1", tiled_url="http://t", tiled_api_key="k")
    c.run_stop("uid1")
    assert fake_ipc.published[0] == ("xpcs.run.bind",
        {"run_uid": "uid1", "tiled_url": "http://t", "tiled_api_key": "k"})
    assert fake_ipc.published[1] == ("xpcs.run.stop", {"run_uid": "uid1"})


def test_status_and_sections(fake_ipc):
    fake_ipc.replies["xpcs.status"] = {"status": "ok", "state": "Idle", "rois": {}}
    fake_ipc.replies["xpcs.sections.get"] = {"status": "ok", "sections": [], "total": 0}
    c = XPCSClient(ipc=fake_ipc)
    assert c.status()["state"] == "Idle"
    assert c.get_sections(from_section=0, limit=10)["total"] == 0
    assert fake_ipc.requests[1][1] == {"from_section": 0, "limit": 10}


def test_event_signals(fake_ipc, qtbot):
    c = XPCSClient(ipc=fake_ipc)
    got = {}
    c.g2Updated.connect(lambda p: got.setdefault("g2", p))
    c.sectionCompleted.connect(lambda p: got.setdefault("sec", p))
    c.stateChanged.connect(lambda p: got.setdefault("state", p))
    c.errorReceived.connect(lambda p: got.setdefault("err", p))
    fake_ipc.emit("xpcs.g2.updated", {"seq": 1})
    fake_ipc.emit("xpcs.section.completed", {"index": 1})
    fake_ipc.emit("xpcs.state", {"state": "Processing"})
    fake_ipc.emit("xpcs.error", {"message": "boom"})
    assert got["g2"]["seq"] == 1
    assert got["sec"]["index"] == 1
    assert got["state"]["state"] == "Processing"
    assert got["err"]["message"] == "boom"


def test_discover(fake_ipc):
    fake_ipc.replies["_xpcs.discover"] = {"prefix": "xpcs", "state": "Idle"}
    c = XPCSClient(ipc=fake_ipc)
    assert c.discover()["prefix"] == "xpcs"


def test_no_ipc_is_safe():
    c = XPCSClient(ipc=None)
    assert c.discover() is None
    assert c.status() is None
    c.enable()  # no raise
