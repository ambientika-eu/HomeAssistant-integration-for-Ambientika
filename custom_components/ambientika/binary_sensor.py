"""Binary sensor platform for ambientika."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER
from .entity import AmbientikaEntity
from .hub import AmbientikaHub
from .unique_ids import unique_id


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the `binary_sensor` entities for each device."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # TODO: this could be simplified with ENTITY_DESCTIPTIONS, but requires event subscription
    # https://github.com/DeebotUniverse/Deebot-4-Home-Assistant/blob/dev/custom_components/deebot/sensor.py#L79
    async_add_entities(HumidityAlarmBinarySensor(device, hub) for device in hub.devices)
    async_add_entities(NightAlarmBinarySensor(device, hub) for device in hub.devices)


class BinarySensorBase(AmbientikaEntity, BinarySensorEntity):
    """Base representation of an Ambientika Sensor."""


class HumidityAlarmBinarySensor(BinarySensorBase):
    """Humidity Alarm Binary Sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "humidity_alarm"
    _attr_icon = "mdi:alarm-light"

    def __init__(self, device, hub: AmbientikaHub) -> None:
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "humidity_alarm")
        LOGGER.debug(f"Creating AmbientikaBinarySensor: {self._device.name}")

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary_sensor is on."""
        if self._status:
            return self._status["humidity_alarm"] is True


class NightAlarmBinarySensor(BinarySensorBase):
    """Humidity Alarm Binary Sensor."""

    _attr_has_entity_name = True
    _attr_translation_key = "night_alarm"
    _attr_icon = "mdi:alarm-light"

    def __init__(self, device, hub: AmbientikaHub) -> None:
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "night_alarm")
        LOGGER.debug(f"Creating AmbientikaBinarySensor: {self._device.name}")

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary_sensor is on."""
        if self._status:
            return self._status["night_alarm"] is True
