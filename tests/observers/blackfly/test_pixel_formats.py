from __future__ import annotations

import numpy as np
import pytest

from lightfall_endstation_7011.observers.blackfly import pixel_formats, registers


def test_decode_mono8():
    raw = bytes(range(12))
    img = pixel_formats.decode(raw, width=4, height=3, pixel_format=registers.PIXEL_FORMAT_MONO8)
    assert img.shape == (3, 4)
    assert img.dtype == np.uint8
    assert img[0, 0] == 0 and img[2, 3] == 11


def test_decode_mono8_respects_row_major_layout():
    """Rows stored top-to-bottom, pixels within a row left-to-right."""
    raw = bytes([0, 1, 2, 3, 10, 11, 12, 13])
    img = pixel_formats.decode(raw, width=4, height=2, pixel_format=registers.PIXEL_FORMAT_MONO8)
    assert img[0].tolist() == [0, 1, 2, 3]
    assert img[1].tolist() == [10, 11, 12, 13]


def test_decode_mono16_little_endian():
    """FLIR ships Mono16 little-endian per Teledyne tech docs; verify with known bytes."""
    # 12 pixels, each the u16 value 0x0001 stored little-endian
    raw = b"\x01\x00" * 12
    img = pixel_formats.decode(raw, width=4, height=3, pixel_format=registers.PIXEL_FORMAT_MONO16)
    assert img.shape == (3, 4)
    assert img.dtype == np.uint16
    assert (img == 1).all()


def test_decode_mono16_high_value():
    """High-byte value also decodes correctly."""
    # pixel value 0x0100 in little-endian bytes
    raw = b"\x00\x01" * 4
    img = pixel_formats.decode(raw, width=2, height=2, pixel_format=registers.PIXEL_FORMAT_MONO16)
    assert (img == 0x0100).all()


def test_decode_bayer_rg8_returns_raw_mosaic():
    raw = bytes(range(12))
    img = pixel_formats.decode(raw, width=4, height=3, pixel_format=registers.PIXEL_FORMAT_BAYER_RG8)
    # Observation mode: return raw mosaic as u8. Caller can demosaic later.
    assert img.shape == (3, 4)
    assert img.dtype == np.uint8
    assert img[0, 0] == 0
    assert img[2, 3] == 11


@pytest.mark.parametrize("fmt", [
    registers.PIXEL_FORMAT_BAYER_RG8,
    registers.PIXEL_FORMAT_BAYER_GB8,
    registers.PIXEL_FORMAT_BAYER_GR8,
    registers.PIXEL_FORMAT_BAYER_BG8,
])
def test_decode_all_bayer8_variants(fmt):
    raw = bytes(range(16))
    img = pixel_formats.decode(raw, width=4, height=4, pixel_format=fmt)
    assert img.shape == (4, 4)
    assert img.dtype == np.uint8


def test_decode_unknown_format_raises():
    with pytest.raises(ValueError, match="unsupported pixel format"):
        pixel_formats.decode(b"\x00", width=1, height=1, pixel_format=0xDEADBEEF)


def test_decode_truncated_bytes_raises():
    """Fewer bytes than width*height*bpp should fail loudly."""
    with pytest.raises(ValueError):
        # 4x3=12 pixels × 1 byte = 12 bytes needed, but only 8 supplied
        pixel_formats.decode(b"\x00" * 8, width=4, height=3, pixel_format=registers.PIXEL_FORMAT_MONO8)


@pytest.mark.parametrize("width,height", [(0, 4), (4, 0), (-1, 4), (4, -1), (-2, -2)])
def test_decode_rejects_non_positive_dimensions(width, height):
    with pytest.raises(ValueError, match="invalid dimensions"):
        pixel_formats.decode(b"\x00" * 16, width=width, height=height,
                             pixel_format=registers.PIXEL_FORMAT_MONO8)
