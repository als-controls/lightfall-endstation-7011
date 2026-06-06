"""ROI/mask geometry, mirroring xpcs_live/shapes.py wire schema.

Convention: x = column, y = row, origin top-left of the detector array.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RectShape:
    x: float
    y: float
    w: float
    h: float

    def to_dict(self) -> dict:
        return {"type": "rect", "x": self.x, "y": self.y, "w": self.w, "h": self.h}

    @classmethod
    def from_dict(cls, d: dict) -> "RectShape":
        if d.get("type") != "rect":
            raise ValueError(f"Unsupported shape type: {d.get('type')!r}")
        return cls(x=float(d["x"]), y=float(d["y"]), w=float(d["w"]), h=float(d["h"]))

    @classmethod
    def from_pos_size(cls, pos, size) -> "RectShape":
        return cls(x=float(pos[0]), y=float(pos[1]), w=float(size[0]), h=float(size[1]))
