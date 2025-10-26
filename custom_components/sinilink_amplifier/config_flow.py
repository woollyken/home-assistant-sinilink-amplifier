"""Config flow for Sinilink Amplifier integration."""
import logging
import re  # Added for MAC validation
from typing import Any, Dict, Optional

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult

from .bluetooth import SinilinkAmplifierBluetooth
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Schema for manual entry, used if no devices are discovered or user chooses manual
MANUAL_DATA_SCHEMA = vol.Schema(
    {
        vol.Required("mac_address"): str,
        vol.Optional("name", default="Sinilink Amplifier"): str,
    }
)

# MAC address validation schema
MAC_REGEX = re.compile(r'^([0-9A-Fa-f]{2}:?){6}$')
def validate_mac(value: str) -> str:
    if not MAC_REGEX.match(value.replace("-", ":")):
        raise vol.Invalid("Invalid MAC address format. Use AA:BB:CC:DD:EE:FF.")
    return value.upper().replace("-", ":")

class SinilinkAmplifierConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Sinilink Amplifier."""

    VERSION = 1

    def __init__(self):
        """Initialize the config flow."""
        self._discovered_devices: Dict[str, str] = {} # mac_address: name
        self._mac_address_from_discovery: Optional[str] = None # Store MAC from Bluetooth discovery

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: Dict[str, str] = {}

        if user_input is not None:
            mac_address = validate_mac(user_input["mac_address"])  # Validate MAC
            name = user_input.get("name")

            await self.async_set_unique_id(mac_address)
            self._abort_if_unique_id_configured()

            # Attempt to connect to validate the MAC address
            # Pass self.hass to the bluetooth handler
            bluetooth_handler = SinilinkAmplifierBluetooth(self.hass, mac_address)
            if await bluetooth_handler.connect():
                await bluetooth_handler.disconnect() # Disconnect after successful test
                return self.async_create_entry(title=name, data=user_input)
            else:
                errors["base"] = "cannot_connect"

        # Discover devices for selection using HA's bluetooth integration
        discovered_ble_devices = await SinilinkAmplifierBluetooth.discover_devices(self.hass)
        self._discovered_devices = {
            device.address: device.name or "Unknown Device"
            for device in discovered_ble_devices
        }

        # Prepare a list for the dropdown
        devices_for_selection = {
            addr: f"{name} ({addr})" for addr, name in self._discovered_devices.items()
        }

        # Determine default for mac_address dropdown
        default_mac_address = None
        if self._discovered_devices:
            default_mac_address = list(self._discovered_devices.keys())[0]

        # If no devices are discovered, or if the user wants to enter manually,
        # provide a simple text input for MAC address.
        if not devices_for_selection:
            data_schema = MANUAL_DATA_SCHEMA
        else:
            data_schema = vol.Schema(
                {
                    vol.Required("mac_address", default=default_mac_address): vol.In(devices_for_selection),
                    vol.Optional("name", default="Sinilink Amplifier"): str,
                }
            )

        return self.async_show_form(
            step_id="user",
            data_schema=data_schema,
            errors=errors,
            description_placeholders={
                "discovery_info": "Select a discovered device or enter manually."
            }
        )

    async def async_step_bluetooth(
        self, discovery_info: Dict[str, Any]
    ) -> FlowResult:
        """Handle a flow initiated by Bluetooth discovery."""
        _LOGGER.debug("Bluetooth discovery info: %s", discovery_info)
        mac_address = validate_mac(discovery_info["address"])  # Validate MAC
        name = discovery_info.get("name", "Sinilink Amplifier")

        await self.async_set_unique_id(mac_address)
        self._abort_if_unique_id_configured()

        # Store the MAC address for the next step
        self._mac_address_from_discovery = mac_address

        self.context["title_placeholders"] = {"name": name, "mac_address": mac_address} # Pass mac_address in context
        return await self.async_step_confirm_bluetooth() # No need to pass data here, use context

    async def async_step_confirm_bluetooth(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """Confirm the discovered Bluetooth device."""
       