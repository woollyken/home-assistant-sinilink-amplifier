"""Bluetooth communication for Sinilink Amplifier, using Home Assistant's Bluetooth integration."""
import asyncio
import logging
from typing import Optional, Callable

from bleak import BleakClient
from bleak.backends.device import BLEDevice
from homeassistant.core import HomeAssistant
from homeassistant.components import bluetooth
from homeassistant.components.bluetooth import BluetoothServiceInfoBleak

_LOGGER = logging.getLogger(__name__)

# BLE settings
SERVICE_UUID = "0000ae00-0000-1000-8000-00805f9b34fb"
CHARACTERISTIC_UUID_1 = "0000ae10-0000-1000-8000-00805f9b34fb" # Write characteristic
CHARACTERISTIC_UUID_2 = "0000ae04-0000-1000-8000-00805f9b34fb" # Notify characteristic for status

class SinilinkAmplifierBluetooth:
    """Manages BLE communication with the Sinilink Amplifier."""

    def __init__(self, hass: HomeAssistant, mac_address: str):
        """Initialize the Bluetooth handler."""
        self.hass = hass
        self._mac_address = mac_address
        self._client: Optional[BleakClient] = None
        self._volume_level: Optional[int] = None
        self._input_code: Optional[int] = None # Store current input
        self._is_connecting = False
        self._lock = asyncio.Lock() # To ensure only one BLE operation runs at a time
        self._notification_callback: Optional[Callable[[int, int], None]] = None # Callback for volume/input updates

    def set_notification_callback(self, callback: Callable[[int, int], None]):
        """Set a callback to be called when volume or input changes via notification."""
        self._notification_callback = callback

    async def connect(self) -> bool:
        """Public method to connect to the device."""
        return await self._ensure_connected()

    async def _ensure_connected(self) -> bool:
        """Ensures a connection to the BLE device is established."""
        async with self._lock: # Acquire lock for connection management
            if self._client and self._client.is_connected:
                return True

            if self._is_connecting:
                _LOGGER.debug("Already connecting to %s, waiting for it to complete.", self._mac_address)
                # Wait for the current connection attempt to finish
                # This is a simple wait, a more robust solution might use an Event
                await asyncio.sleep(0.5) # Give some time for the other connection attempt
                if self._client and self._client.is_connected:
                    return True
                return False # Still not connected after waiting

            self._is_connecting = True
            try:
                ble_device = bluetooth.async_ble_device_from_address(self.hass, self._mac_address)
                if not ble_device:
                    _LOGGER.warning("BLE device %s not found via Home Assistant Bluetooth.", self._mac_address)
                    return False
                
                # If client exists but is not connected, ensure it's cleaned up
                if self._client and not self._client.is_connected:
                    try:
                        await self._client.disconnect()
                    except Exception as e:
                        _LOGGER.debug("Error during old client cleanup disconnect: %s", e)
                    self._client = None

                if not self._client:
                    self._client = BleakClient(ble_device)

                _LOGGER.debug("Attempting to connect to BLE device: %s", self._mac_address)
                await self._client.connect()
                _LOGGER.debug("Connected to BLE device: %s", self._mac_address)

                # Start persistent notifications once connected
                await self._start_notifications()
                return True
            except Exception as e:
                _LOGGER.error("Failed to connect to %s: %s", self._mac_address, e)
                if self._client:
                    try:
                        await self._client.disconnect()
                    except Exception as e_disc:
                        _LOGGER.debug("Error during client cleanup disconnect: %s", e_disc)
                self._client = None
                return False
            finally:
                self._is_connecting = False

    async def disconnect(self):
        """Disconnect from the BLE device."""
        async with self._lock:
            if self._client and self._client.is_connected:
                _LOGGER.debug("Disconnecting from BLE device: %s", self._mac_address)
                try:
                    await self._stop_notifications()
                    await self._client.disconnect()
                except Exception as e:
                    _LOGGER.warning("Error during disconnect: %s", e)
            self._client = None
            self._volume_level = None
            self._input_code = None

    @property
    def is_connected(self) -> bool:
        """Return true if the device is connected."""
        return self._client and self._client.is_connected

    async def _start_notifications(self):
        """Start listening for notifications from the amplifier."""
        if not self._client or not self._client.is_connected:
            _LOGGER.warning("Cannot start notifications: not connected.")
            return

        try:
            await self._client.start_notify(CHARACTERISTIC_UUID_2, self._handle_notification)
            _LOGGER.debug("Notifications enabled for CHARACTERISTIC_UUID_2")
            # Trigger an initial read to get current status if device requires it
            await self._client.read_gatt_char(CHARACTERISTIC_UUID_1)
            _LOGGER.debug("Triggered initial read on CHARACTERISTIC_UUID_1 for status update.")
        except Exception as e:
            _LOGGER.error("Failed to start notifications: %s", e)

    async def _stop_notifications(self):
        """Stop listening for notifications."""
        if not self._client or not self._client.is_connected:
            return
        try:
            await self._client.stop_notify(CHARACTERISTIC_UUID_2)
            _LOGGER.debug("Notifications stopped for CHARACTERISTIC_UUID_2")
        except Exception as e:
            _LOGGER.warning("Error stopping notifications: %s", e)

    def _handle_notification(self, sender, data):
        """Handle incoming BLE notifications."""
        _LOGGER.debug("Notification received from %s: %s", sender, ' '.join(format(x, '02X') for x in data))
        
        # Example: Assuming data format for volume and input
        # This part needs to be confirmed with actual device behavior
        # Based on get_volume and get_input, it seems volume is data[5] and input is data[4]
        # However, notifications might send a different packet structure.
        # We'll assume a combined status packet for now.
        
        new_volume = None
        new_input_code = None

        if len(data) > 5: # Assuming volume is at index 5
            new_volume = data[5]
            if self._volume_level != new_volume:
                self._volume_level = new_volume
                _LOGGER.debug("Updated volume level from notification: %d", self._volume_level)
        
        if len(data) > 4: # Assuming input is at index 4
            new_input_code = data[4]
            if self._input_code != new_input_code:
                self._input_code = new_input_code
                _LOGGER.debug("Updated input code from notification: %d", self._input_code)

        if self._notification_callback and (new_volume is not None or new_input_code is not None):
            # Call the registered callback to inform the media player
            self._notification_callback(self._volume_level, self._input_code)


    async def set_volume(self, volume: int) -> bool:
        """Set the amplifier volume."""
        if not (1 <= volume <= 31):
            _LOGGER.error("Volume must be between 1 and 31.")
            return False

        if not await self._ensure_connected():
            return False

        async with self._lock: # Lock for BLE operation
            try:
                data = bytearray([0x7e, 0x0f, 0x1d, volume, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
                checksum = sum(data) & 0xFF
                data.append(checksum)
                _LOGGER.debug("Sending volume command: %s", ' '.join(format(x, '02X') for x in data))
                await self._client.write_gatt_char(CHARACTERISTIC_UUID_1, data, response=True)
                _LOGGER.info("Volume set command sent: %d", volume)
                # Do not update _volume_level here directly, wait for notification
                return True
            except Exception as e:
                _LOGGER.error("Failed to send set volume command: %s", e)
                return False

    async def get_volume(self) -> Optional[int]:
        """Get the current volume level. Relies on stored notification data."""
        if not await self._ensure_connected():
            return None
        
        # Request an update from the device to ensure _volume_level is fresh
        async with self._lock:
            try:
                # Reading CHARACTERISTIC_UUID_1 seems to trigger status updates
                await self._client.read_gatt_char(CHARACTERISTIC_UUID_1)
                _LOGGER.debug("Requested status update from CHARACTERISTIC_UUID_1 to refresh volume.")
                # Give a short moment for notification to arrive and update _volume_level
                await asyncio.sleep(0.5) 
            except Exception as e:
                _LOGGER.warning("Failed to trigger status update for volume: %s", e)
        
        return self._volume_level

    async def get_input(self) -> Optional[int]:
        """Get the current input type. Relies on stored notification data."""
        if not await self._ensure_connected():
            return None

        # Request an update from the device to ensure _input_code is fresh
        async with self._lock:
            try:
                # Reading CHARACTERISTIC_UUID_1 seems to trigger status updates
                await self._client.read_gatt_char(CHARACTERISTIC_UUID_1)
                _LOGGER.debug("Requested status update from CHARACTERISTIC_UUID_1 to refresh input.")
                # Give a short moment for notification to arrive and update _input_code
                await asyncio.sleep(0.5)
            except Exception as e:
                _LOGGER.warning("Failed to trigger status update for input: %s", e)

        return self._input_code

    async def set_input(self, input_code: int) -> bool:
        """Set the amplifier input."""
        if not await self._ensure_connected():
            return False

        async with self._lock: # Lock for BLE operation
            try:
                data = bytearray([0x7e, 0x05, input_code, 0x00])
                checksum = sum(data) & 0xFF
                data.append(checksum)
                _LOGGER.debug("Sending input switch command: %s", ' '.join(format(x, '02X') for x in data))
                await self._client.write_gatt_char(CHARACTERISTIC_UUID_1, data, response=True)
                _LOGGER.info("Input switch command sent: %d (hex: %s)", input_code, hex(input_code))
                # Do not update _input_code here directly, wait for notification
                return True
            except Exception as e:
                _LOGGER.error("Failed to send set input command: %s", e)
                return False

    @staticmethod
    async def discover_devices(hass: HomeAssistant) -> list[BLEDevice]:
        """Scan for BLE devices using Home Assistant's Bluetooth integration."""
        _LOGGER.debug("Scanning for BLE devices via Home Assistant Bluetooth...")
        
        discovered_service_info = bluetooth.async_discovered_service_info(hass)
        
        unique_devices = {}
        for service_info in discovered_service_info:
            if SERVICE_UUID in service_info.service_uuids:
                _LOGGER.debug("Found potential Sinilink Amplifier: %s (%s)", service_info.name, service_info.address)
                if service_info.address not in unique_devices:
                    # Create a mock BLEDevice object for compatibility
                    unique_devices[service_info.address] = type('BLEDevice', (object,), {
                        'address': service_info.address,
                        'name': service_info.name or "Sinilink Amplifier"
                    })()
        
        _LOGGER.debug("Found %d unique BLE devices via HA Bluetooth.", len(unique_devices))
        return list(unique_devices.values())