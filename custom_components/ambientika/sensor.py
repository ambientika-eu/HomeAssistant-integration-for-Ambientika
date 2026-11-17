"""Sensor platform for ambientika.

References:
 - https://github.com/ludeeus/integration_blueprint/blob/main/custom_components/integration_blueprint/sensor.py
 - https://github.com/home-assistant/example-custom-config/blob/master/custom_components/detailed_hello_world_push/sensor.py
  https://github.com/DeebotUniverse/Deebot-4-Home-Assistant/blob/dev/custom_components/deebot/sensor.py

"""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.components.sensor.const import SensorDeviceClass

from .const import DOMAIN, LOGGER, AirQuality, FilterStatus
from .entity import AmbientikaEntity
from .hub import AmbientikaHub
from .unique_ids import unique_id


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the `sensor` entities for each device."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # TODO: this could be simplified with ENTITY_DESCTIPTIONS, but requires event subscription
    # https://github.com/DeebotUniverse/Deebot-4-Home-Assistant/blob/dev/custom_components/deebot/sensor.py#L79
    # async_add_entities((TemperatureSensor(device, hub) for device in hub.devices), True)
    # async_add_entities((HumiditySensor(device, hub) for device in hub.devices), True)
    # The hub has read the status of every unit before the entities are created.
    async_add_entities(AirQualitySensor(device, hub) for device in hub.devices)
    async_add_entities(FilterStatusSensor(device, hub) for device in hub.devices)


class SensorBase(AmbientikaEntity):
    """Base representation of an Ambientika Sensor."""


class TemperatureSensor(SensorBase):
    """Sensor for the Air Quality status."""

    _attr_has_entity_name = True
    _attr_translation_key = "temperature"
    # _attr_icon = "mdi:thermometer"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_unit_of_measurement = "°C"

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "temperature")

    @property
    def state(self):
        """State of the sensor."""
        if self._status:
            return self._status["temperature"]


class HumiditySensor(SensorBase):
    """Sensor for the Air Quality status."""

    _attr_has_entity_name = True
    _attr_translation_key = "humidity"
    # _attr_icon = "mdi:air-purifier"
    _attr_device_class = SensorDeviceClass.HUMIDITY
    _attr_unit_of_measurement = "%"

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "humidity")

    @property
    def state(self):
        """State of the sensor."""
        if self._status:
            return self._status["humidity"]


class AirQualitySensor(SensorBase):
    """Sensor for the Air Quality status."""

    _attr_has_entity_name = True
    _attr_translation_key = "air_quality"
    _attr_icon = "mdi:air-purifier"

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "air_quality")

    @property
    def state(self):
        """State of the sensor."""
        if self._status:
            value = self._status["air_quality"]
            if not isinstance(value, str) or not value:
                return None
            if value in AirQuality.__members__:
                return AirQuality[value]
            # A value this version does not know yet is shown as reported.
            return value.lower()[:255]


class FilterStatusSensor(SensorBase):
    """Sensor for the Filter Status."""

    _attr_has_entity_name = True
    _attr_translation_key = "filter_status"
    _attr_icon = "mdi:air-filter"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "filter_status")

    @property
    def state(self):
        """State of the sensor."""
        if self._status:
            value = self._status["filters_status"]
            if isinstance(value, str) and value in FilterStatus.__members__:
                return FilterStatus[value]
            # An enum sensor only accepts its listed options.
            LOGGER.debug(
                "Unknown filter status %r for device %s",
                value,
                self._device.serial_number,
            )
            return None

    @property
    def options(self):
        """Return the list of available options."""
        # The state is the enum value ("good"), so the options are the values
        # too; they are also the keys of the translated state names.
        return [member.value for member in FilterStatus]
