"""Constants for the Sinilink Amplifier integration."""

DOMAIN = "sinilink_amplifier"

# Media Player attributes
ATTR_INPUT_SOURCE = "input_source"

# Input source mapping
INPUT_AUX = "aux"
INPUT_BLUETOOTH = "bt"
INPUT_SOUNDCARD = "sndcard"
INPUT_USB = "usb"

INPUT_SOURCE_MAP = {
    0x16: INPUT_AUX,
    0x14: INPUT_BLUETOOTH,
    0x15: INPUT_SOUNDCARD, # Corrected from 0x17 in main.py based on common patterns
    0x04: INPUT_USB,
}

INPUT_CODE_MAP = {
    INPUT_AUX: 0x16,
    INPUT_BLUETOOTH: 0x14,
    INPUT_SOUNDCARD: 0x15,
    INPUT_USB: 0x04,
}

