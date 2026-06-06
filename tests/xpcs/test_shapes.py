import pytest

from lightfall_endstation_7011.xpcs.shapes import RectShape


def test_round_trip():
    r = RectShape(x=10.0, y=20.0, w=64.0, h=32.0)
    d = r.to_dict()
    assert d == {"type": "rect", "x": 10.0, "y": 20.0, "w": 64.0, "h": 32.0}
    assert RectShape.from_dict(d) == r


def test_from_dict_rejects_unknown_type():
    with pytest.raises(ValueError):
        RectShape.from_dict({"type": "ellipse", "x": 0, "y": 0, "w": 1, "h": 1})


def test_from_roi_state():
    # pyqtgraph RectROI state: pos = (x, y) bottom-left in data coords, size = (w, h)
    r = RectShape.from_pos_size((5.0, 7.0), (10.0, 12.0))
    assert (r.x, r.y, r.w, r.h) == (5.0, 7.0, 10.0, 12.0)
