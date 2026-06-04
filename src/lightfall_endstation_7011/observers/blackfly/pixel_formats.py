"""Pixel format decoders: GVSP payload bytes -> numpy array.

For an observation widget, Bayer formats are returned as raw mosaic (u8 2D
array). pyqtgraph ImageView displays the mosaic as grayscale, which is a
usable live preview without the cost of demosaicing each frame. If a user
wants a color image they can demosaic outside this module.
"""
from __future__ import annotations

import numpy as np

from . import registers


_BAYER8_FORMATS = frozenset({
    registers.PIXEL_FORMAT_BAYER_RG8,
    registers.PIXEL_FORMAT_BAYER_GB8,
    registers.PIXEL_FORMAT_BAYER_GR8,
    registers.PIXEL_FORMAT_BAYER_BG8,
})


def decode(raw: bytes, width: int, height: int, pixel_format: int) -> np.ndarray:
    """Decode a GVSP payload to a 2D numpy array of shape (height, width).

    Raises ValueError on unsupported formats or when `raw` is too short.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"invalid dimensions: {width}x{height}")
    npx = width * height
    if pixel_format == registers.PIXEL_FORMAT_MONO8 or pixel_format in _BAYER8_FORMATS:
        if len(raw) < npx:
            raise ValueError(f"truncated Mono8/Bayer8 payload: {len(raw)}B for {npx} pixels")
        return np.frombuffer(raw, dtype=np.uint8, count=npx).reshape(height, width)
    if pixel_format == registers.PIXEL_FORMAT_MONO16:
        # FLIR Blackfly S ships Mono16 little-endian on the wire per Teledyne docs.
        # Revisit if a live capture shows otherwise.
        if len(raw) < npx * 2:
            raise ValueError(f"truncated Mono16 payload: {len(raw)}B for {npx} pixels")
        return np.frombuffer(raw, dtype="<u2", count=npx).reshape(height, width)
    raise ValueError(f"unsupported pixel format 0x{pixel_format:08x}")
