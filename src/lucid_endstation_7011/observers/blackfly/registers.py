"""Register addresses.

Bootstrap registers are from the GigE Vision 1.2 spec (see aravis arvgvcpprivate.h).
Blackfly S feature registers were extracted from the camera's own GenApi XML via
scripts/extract_registers.py (see research/blackfly_s_registers.txt).

Only features whose backing register is directly addressable on Blackfly S are
exposed here. See the inline note after the feature block for the list of
omitted features and why.
"""
from __future__ import annotations

from typing import Final

# --- GigE Vision bootstrap (1.2 spec, offsets in camera register space) ---
REG_VERSION: Final[int] = 0x0000
REG_DEVICE_MODE: Final[int] = 0x0004
REG_CURRENT_IP: Final[int] = 0x0024
REG_MANUFACTURER_NAME: Final[int] = 0x0048
REG_MODEL_NAME: Final[int] = 0x0068
REG_DEVICE_VERSION: Final[int] = 0x0088
REG_SERIAL_NUMBER: Final[int] = 0x00D8
REG_USER_DEFINED_NAME: Final[int] = 0x00E8
REG_FIRST_URL: Final[int] = 0x0200
REG_SECOND_URL: Final[int] = 0x0400
REG_CCP: Final[int] = 0x0A00              # Control Channel Privilege
REG_HEARTBEAT_TIMEOUT: Final[int] = 0x0938

# Stream Channel 0 (GigE Vision 1.2 bootstrap; ref: aravis arvgvcpprivate.h + live-camera probe)
REG_SC0_PORT_HOST: Final[int] = 0x0D00    # lower 16 bits = UDP port on host
REG_SC0_PACKET_SIZE: Final[int] = 0x0D04  # lower 16 bits = packet size; bits 29/30/31 = big-endian / do-not-fragment / fire-test flags
REG_SC0_DEST_ADDR: Final[int] = 0x0D18    # IPv4 destination for stream channel 0

# --- Blackfly S feature registers (from research/blackfly_s_registers.txt) ---
# Each address below must match research/blackfly_s_registers.txt (enforced by test_blackfly_s_addresses_match_research_file).
REG_WIDTH: Final[int] = 0x00081084
REG_HEIGHT: Final[int] = 0x00081064
REG_WIDTH_MAX: Final[int] = 0x00080084
REG_HEIGHT_MAX: Final[int] = 0x000800A4
REG_OFFSET_X: Final[int] = 0x00081044
REG_OFFSET_Y: Final[int] = 0x00081024
REG_PIXEL_FORMAT: Final[int] = 0x00086008
REG_ACQUISITION_MODE: Final[int] = 0x000C00C8
REG_ACQUISITION_START: Final[int] = 0x000C0004
REG_ACQUISITION_STOP: Final[int] = 0x000C0024

# Note: ExposureTime, Gain, TriggerMode, TriggerSoftware, and DeviceTemperature
# are intentionally omitted -- on Blackfly S these are either selector-addressed
# (need a *Selector register set first) or computed via GenICam expressions from
# raw registers. Not needed for MVP observation (camera is pre-configured).
# If you need them, consult research/blackfly_s_registers.txt for the *_Val and
# *Selector_Val entries and handle the selector dance in Camera.

# --- GenICam SFNC pixel format enum values ---
# These are standard across all GigE Vision vendors, not Blackfly S specific.
# Layout: component_count(1B) | bits_per_pixel(1B) | pixel_format_id(2B)
PIXEL_FORMAT_MONO8: Final[int] = 0x01080001
PIXEL_FORMAT_MONO16: Final[int] = 0x01100007
PIXEL_FORMAT_BAYER_GR8: Final[int] = 0x01080008
PIXEL_FORMAT_BAYER_RG8: Final[int] = 0x01080009
PIXEL_FORMAT_BAYER_GB8: Final[int] = 0x0108000A
PIXEL_FORMAT_BAYER_BG8: Final[int] = 0x0108000B

# --- AcquisitionMode enum values ---
ACQUISITION_MODE_CONTINUOUS: Final[int] = 0
ACQUISITION_MODE_SINGLE_FRAME: Final[int] = 1
ACQUISITION_MODE_MULTI_FRAME: Final[int] = 2

# GigE Vision CCP register bit values (ref: aravis arvgvcpprivate.h)
CCP_NONE: Final[int] = 0
CCP_EXCLUSIVE: Final[int] = 1 << 0      # bit 0 — forcibly evicts other controllers
CCP_CONTROL: Final[int] = 1 << 1        # bit 1 — normal control, what this widget uses
