"""Binary sensor platform for ambientika."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, LOGGER
from .entity import AmbientikaEntity
from .hub import AmbientikaHub
from .neuracell_ha import neuracell_device_info
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

    cfg = hub.nc_cfg
    wanted = []
    if cfg.radon_configured:
        wanted.append("radon_protection")
        if cfg.radon_source == "signal" and cfg.radon_entities:
            wanted.append("radon_meter_connected")
    if cfg.dewpoint_configured:
        wanted.append("dewpoint_block")
        if cfg.dewpoint_source != "computed":
            wanted += ["dewpoint_controller_connected", "dewpoint_fallback"]
    async_remove_neuracell_entities(hass, entry, "binary_sensor", wanted)
    async_add_entities(NeuraCellBinarySensor(hub, entry, key) for key in wanted)


def async_remove_neuracell_entities(
    hass: HomeAssistant, entry: ConfigEntry, domain: str, wanted: list[str]
) -> None:
    """Remove the NeuraCell-X entities the options no longer call for."""
    registry = er.async_get(hass)
    head = f"{entry.entry_id}_neuracell_"
    keep = {head + key for key in wanted}
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registered.domain == domain
            and registered.unique_id.startswith(head)
            and registered.unique_id not in keep
        ):
            registry.async_remove(registered.entity_id)


NEURACELL_BINARY = {
    # key: (value in the state of NeuraCell-X, device class, icon)
    "radon_protection": (
        "radon_protection",
        BinarySensorDeviceClass.SAFETY,
        "mdi:shield-home",
    ),
    "radon_meter_connected": (
        "radon_meter_connected",
        BinarySensorDeviceClass.CONNECTIVITY,
        "mdi:access-point-network",
    ),
    "dewpoint_controller_connected": (
        "dewpoint_controller_connected",
        BinarySensorDeviceClass.CONNECTIVITY,
        "mdi:access-point-network",
    ),
    "dewpoint_block": ("dewpoint_block", None, "mdi:water-off"),
    "dewpoint_fallback": (
        "dewpoint_fallback",
        BinarySensorDeviceClass.PROBLEM,
        "mdi:fan-alert",
    ),
}


class NeuraCellBinarySensor(CoordinatorEntity[AmbientikaHub], BinarySensorEntity):
    """A part of the state of NeuraCell-X: radon protection and dew-point control."""

    _attr_has_entity_name = True

    def __init__(self, hub: AmbientikaHub, entry: ConfigEntry, key: str) -> None:
        """Initialize the sensor."""
        super().__init__(hub)
        self._hub = hub
        self._key = key
        self._source, self._attr_device_class, self._attr_icon = NEURACELL_BINARY[key]
        self._attr_translation_key = f"neuracell_{key}"
        self._attr_unique_id = f"{entry.entry_id}_neuracell_{key}"
        self._attr_device_info = neuracell_device_info(entry.entry_id)

    @property
    def available(self) -> bool:
        """Return True: the state of NeuraCell-X is known whatever the cloud does."""
        return True

    @property
    def is_on(self) -> bool | None:
        """Return the state; None while a connection is not judged yet."""
        return self._hub.neuracell_state()[self._source]

    @property
    def extra_state_attributes(self) -> dict | None:
        """What radon protection goes by."""
        if self._key != "radon_protection":
            return None
        state = self._hub.neuracell_state()
        return {
            name: state[name]
            for name in (
                "radon_value_alarm",
                "radon_signal_alarm",
                "radon_threshold",
                "radon_sources",
            )
        }


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
        if self._view:
            return self._view["humidity_alarm"] is True


class NightAlarmBinarySensor(BinarySensorBase):
    """The night flag the unit reports in its status.

    Every unit, a Slave too, reports its own. It is no fault: up to 1.5 it was
    named "Night Alarm". The unique ID and so the entity ID stay as they were.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "night_alarm"
    _attr_icon = "mdi:weather-night"

    def __init__(self, device, hub: AmbientikaHub) -> None:
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "night_alarm")
        LOGGER.debug(f"Creating AmbientikaBinarySensor: {self._device.name}")

    @property
    def is_on(self) -> bool | None:
        """Return true if the binary_sensor is on."""
        if self._view:
            return self._view["night_alarm"] is True
