"""Media Player platform for Sinilink Amplifier."""
import logging
from typing import Any, List, Optional

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaType,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed,
)
import datetime

from .bluetooth import SinilinkAmplifierBluetooth
from .const import (
    DOMAIN,
    INPUT_AUX,
    INPUT_BLUETOOTH,
    INPUT_SOUNDCARD,
    INPUT_USB,
    INPUT_SOURCE_MAP,
    INPUT_CODE_MAP,
)

_LOGGER = logging.getLogger(__name__)

# Update interval for the media player state (can be longer now with notifications)
SCAN_INTERVAL = datetime.timedelta(seconds=30) # Increased as notifications will provide real-time updates

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Sinilink Amplifier media player from a config entry."""
    mac_address = config_entry.data["mac_address"]
    name = config_entry.data.get("name", "Sinilink Amplifier")

    bluetooth_handler = SinilinkAmplifierBluetooth(hass, mac_address)

    # Create a coordinator to manage updates
    async def async_update_data():
        """Fetch data from the amplifier. This will primarily trigger a read to refresh notification data."""
        try:
            # Ensure connection is established. Notifications will be started here.
            if not bluetooth_handler.is_connected:
                await bluetooth_handler._ensure_connected() 
            
            # Request a status update from the device.
            # The notification callback will update the internal state of bluetooth_handler.
            # We don't need to disconnect here, as the connection is persistent.
            
            # Explicitly trigger a read on CHARACTERISTIC_UUID_1 to ensure the device sends status notifications
            # This is crucial if the device doesn't send unsolicited notifications.
            await bluetooth_handler.get_volume() # This will trigger a read and wait for notification
            await bluetooth_handler.get_input() # This will trigger a read and wait for notification

            return {
                "volume": bluetooth_handler._volume_level,
                "input_code": bluetooth_handler._input_code
            }
        except Exception as err:
            _LOGGER.error("Error during coordinator update for %s: %s", name, err)
            # If connection fails, try to disconnect and allow _ensure_connected to re-establish
            await bluetooth_handler.disconnect() 
            raise UpdateFailed(f"Error communicating with device: {err}") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name="Sinilink Amplifier",
        update_method=async_update_data,
        update_interval=SCAN_INTERVAL,
    )

    # Fetch initial data so we have something to start with
    await coordinator.async_config_entry_first_refresh()

    # Add the media player entity
    entity = SinilinkAmplifierMediaPlayer(name, bluetooth_handler, coordinator)
    async_add_entities([entity], True)

    # Register the notification callback with the bluetooth handler
    bluetooth_handler.set_notification_callback(entity.async_on_bluetooth_notification)


class SinilinkAmplifierMediaPlayer(CoordinatorEntity, MediaPlayerEntity):
    """Representation of a Sinilink Amplifier media player."""

    _attr_media_type = MediaType.MUSIC
    _attr_supported_features = (
        MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(self, name: str, bluetooth_handler: SinilinkAmplifierBluetooth, coordinator: DataUpdateCoordinator):
        """Initialize the Sinilink Amplifier media player."""
        super().__init__(coordinator)
        self._name = name
        self._bluetooth_handler = bluetooth_handler
        self._attr_unique_id = f"{bluetooth_handler._mac_address}_media_player"
        self._state = None
        self._volume_level: Optional[float] = None # 0.0 to 1.0
        self._current_input: Optional[str] = None

    @property
    def name(self) -> str:
        """Return the name of the device."""
        return self._name

    @property
    def state(self) -> Optional[str]:
        """Return the state of the device."""
        # If we have volume or input, assume it's 'on'
        if self._volume_level is not None or self._current_input is not None:
            return "on"
        return "off"

    @property
    def volume_level(self) -> Optional[float]:
        """Volume level of the media player (0.0-1.0)."""
        return self._volume_level

    @property
    def source(self) -> Optional[str]:
        """Return the current input source."""
        return self._current_input

    @property
    def source_list(self) -> List[str]:
        """List of available input sources."""
        return [INPUT_AUX, INPUT_BLUETOOTH, INPUT_SOUNDCARD, INPUT_USB]

    async def async_added_to_hass(self) -> None:
        """When entity is added to hass."""
        await super().async_added_to_hass()
        # Initial update from coordinator data
        self._handle_coordinator_update()

    @callback
    def async_on_bluetooth_notification(self, volume: Optional[int], input_code: Optional[int]) -> None:
        """Callback for Bluetooth notifications."""
        _LOGGER.debug("Received Bluetooth notification in media player: volume=%s, input_code=%s", volume, input_code)
        if volume is not None:
            self._volume_level = volume / 31.0
        if input_code is not None:
            self._current_input = INPUT_SOURCE_MAP.get(input_code, "Unknown")
        
        # Mark the entity as available if we receive data
        self._attr_available = True
        self.async_write_ha_state()

    def _handle_coordinator_update(self) -> None:
        """Handle updated data from the coordinator."""
        volume = self.coordinator.data.get("volume")
        input_code = self.coordinator.data.get("input_code")

        if volume is not None:
            self._volume_level = volume / 31.0
        else:
            self._volume_level = None

        if input_code is not None:
            self._current_input = INPUT_SOURCE_MAP.get(input_code, "Unknown")
        else:
            self._current_input = None
        
        # If we successfully got data, the device is available
        self._attr_available = (volume is not None or input_code is not None)
        self.async_write_ha_state()

    async def async_update(self) -> None:
        """Update the state of the media player. Handled by coordinator."""
        # This method is now effectively handled by the coordinator.
        # The coordinator's update_method will call the bluetooth handler.
        pass

    async def async_set_volume_level(self, volume: float) -> None:
        """Set volume level, range 0.0-1.0."""
        amp_volume = max(1, min(31, round(volume * 31)))
        _LOGGER.debug("Sending set volume command for %d (HA: %f)", amp_volume, volume)
        if await self._bluetooth_handler.set_volume(amp_volume):
            # Volume will be updated via notification, no need to set _volume_level directly here
            # Request a refresh to ensure the coordinator picks up the latest state if notification is delayed
            await self.coordinator.async_request_refresh() 
        else:
            _LOGGER.warning("Failed to send volume set command.")

    async def async_volume_up(self) -> None:
        """Volume up media player."""
        if self._volume_level is not None:
            new_volume = min(1.0, self._volume_level + (1 / 31.0))
            await self.async_set_volume_level(new_volume)

    async def async_volume_down(self) -> None:
        """Volume down media player."""
        if self._volume_level is not None:
            new_volume = max(0.0, self._volume_level - (1 / 31.0))
            await self.async_set_volume_level(new_volume)

    async def async_select_source(self, source: str) -> None:
        """Select input source."""
        input_code = INPUT_CODE_MAP.get(source)
        if input_code is None:
            _LOGGER.warning("Invalid input source: %s", source)
            return

        _LOGGER.debug("Sending select input source command: %s (code: %s)", source, hex(input_code))
        if await self._bluetooth_handler.set_input(input_code):
            # Input will be updated via notification, no need to set _current_input directly here
            # Request a refresh to ensure the coordinator picks up the latest state if notification is delayed
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Failed to send input select command.")

    async def async_turn_on(self) -> None:
        """Turn the media player on by ensuring connection."""
        _LOGGER.info("Attempting to turn on Sinilink Amplifier by connecting.")
        if await self._bluetooth_handler._ensure_connected():
            # State will be updated by coordinator refresh or notification
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.warning("Failed to turn on Sinilink Amplifier.")
            self._attr_available = False
            self.async_write_ha_state()

    async def async_turn_off(self) -> None:
        """Turn the media player off by disconnecting."""
        _LOGGER.info("Attempting to turn off Sinilink Amplifier by disconnecting.")
        await self._bluetooth_handler.disconnect()
        self._attr_available = False
        self._volume_level = None
        self._current_input = None
        self.async_write_ha_state()
        # No need for coordinator refresh as we've explicitly set state to off/unavailable
