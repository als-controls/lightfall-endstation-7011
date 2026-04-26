from __future__ import annotations

from lucid_endstation_7011.observers.blackfly import registers


def test_register_addresses_unique():
    """All REG_* constants should have distinct addresses."""
    reg_attrs = {n: getattr(registers, n) for n in dir(registers) if n.startswith("REG_")}
    addrs = list(reg_attrs.values())
    dupes = [(n, v) for n, v in reg_attrs.items() if addrs.count(v) > 1]
    assert not dupes, f"duplicate register addresses: {dupes}"


def test_all_registers_in_u32_range():
    for n in dir(registers):
        if not n.startswith("REG_"):
            continue
        v = getattr(registers, n)
        assert 0 <= v <= 0xFFFFFFFF, f"{n} = 0x{v:X} out of u32 range"


def test_pixel_format_constants_well_formed():
    # Layout: component_count | bits_per_pixel | pixel_format_id (low 2 bytes)
    assert (registers.PIXEL_FORMAT_MONO8 >> 16) & 0xFF == 0x08  # 8 bits
    assert (registers.PIXEL_FORMAT_MONO16 >> 16) & 0xFF == 0x10  # 16 bits
    assert (registers.PIXEL_FORMAT_BAYER_RG8 >> 16) & 0xFF == 0x08


def test_bootstrap_registers_match_spec():
    # Values from GigE Vision 1.2 spec (aravis arvgvcpprivate.h ARV_GVBS_*_OFFSET)
    assert registers.REG_MANUFACTURER_NAME == 0x0048
    assert registers.REG_MODEL_NAME == 0x0068
    assert registers.REG_DEVICE_VERSION == 0x0088
    assert registers.REG_SERIAL_NUMBER == 0x00D8
    assert registers.REG_CCP == 0x0A00
    assert registers.REG_HEARTBEAT_TIMEOUT == 0x0938
    assert registers.REG_SC0_PORT_HOST == 0x0D00
    assert registers.REG_SC0_PACKET_SIZE == 0x0D04
    assert registers.REG_SC0_DEST_ADDR == 0x0D18


def test_ccp_values():
    assert registers.CCP_NONE == 0
    assert registers.CCP_EXCLUSIVE == 1
    assert registers.CCP_CONTROL == 2


def test_acquisition_mode_values():
    assert registers.ACQUISITION_MODE_CONTINUOUS == 0
    assert registers.ACQUISITION_MODE_SINGLE_FRAME == 1
    assert registers.ACQUISITION_MODE_MULTI_FRAME == 2


def test_blackfly_s_addresses_match_research_file():
    """Every Blackfly S feature REG_* must match what's in research/blackfly_s_registers.txt.

    Guards against the common "edit one, forget the other" drift between the
    camera-extracted table and the transcribed constants.
    """
    from pathlib import Path
    import re

    research = Path(__file__).parent.parent / "research" / "blackfly_s_registers.txt"
    text = research.read_text(encoding="utf-8")

    # Maps constant name -> feature name in research file
    expected = {
        "REG_WIDTH": "Width",
        "REG_HEIGHT": "Height",
        "REG_WIDTH_MAX": "WidthMax",
        "REG_HEIGHT_MAX": "HeightMax",
        "REG_OFFSET_X": "OffsetX",
        "REG_OFFSET_Y": "OffsetY",
        "REG_PIXEL_FORMAT": "PixelFormat",
        "REG_ACQUISITION_MODE": "AcquisitionMode",
        "REG_ACQUISITION_START": "AcquisitionStart",
        "REG_ACQUISITION_STOP": "AcquisitionStop",
    }
    for const_name, feature in expected.items():
        m = re.search(rf"^{feature}\s+=\s+(0x[0-9A-Fa-f]+)", text, re.M)
        assert m, f"{feature!r} not found in research file"
        file_addr = int(m.group(1), 16)
        const_addr = getattr(registers, const_name)
        assert const_addr == file_addr, (
            f"{const_name}=0x{const_addr:08X} disagrees with research file "
            f"entry {feature}=0x{file_addr:08X}"
        )
