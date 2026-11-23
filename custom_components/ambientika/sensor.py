"""Sensor platform for ambientika.

The same sensors as the Ambientika MQTT Bridge publishes: measured values,
their averages, the effective and the unit's own filter status and operating
mode, the values a unit is set to, numbers next to the text values for
long-term statistics, and diagnostics.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ambientika_py import Device, HumidityLevel, LightSensorLevel, OperatingMode

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .compat import enum_number
from .const import DOMAIN, LOGGER, AirQuality, FilterStatus
from .binary_sensor import async_remove_neuracell_entities
from .entity import AmbientikaEntity, device_info
from .hub import AmbientikaHub
from .logic import air_quality_to_num, filter_status_to_num, snake
from .neuracell_ha import neuracell_device_info
from .unique_ids import unique_id

MODE_OPTIONS = [snake(mode.name) for mode in OperatingMode if mode.value < 900]
FAN_OPTIONS = ["low", "medium", "high", "night", "turbo"]
HUMIDITY_LEVEL_OPTIONS = [
    snake(level.name) for level in HumidityLevel if level.value < 900
]
LIGHT_OPTIONS = [snake(level.name) for level in LightSensorLevel if level.value < 900]
ROLE_OPTIONS = [
    "master",
    "slave_equal_master",
    "slave_opposite_master",
    "not_configured",
]
RESET_OPTIONS = ["idle", "running", "confirmed", "acknowledged", "unconfirmed"]
FILTER_OPTIONS = [member.value for member in FilterStatus]


def _enum_state(options: list[str]) -> Callable[[Any], str | None]:
    """Return the state key of a value of the cloud, None if it is not listed."""

    def convert(value: Any) -> str | None:
        key = snake(getattr(value, "name", value))
        return key if key in options else None

    return convert


def _filter_state(value: Any) -> str | None:
    if isinstance(value, str) and value in FilterStatus.__members__:
        return FilterStatus[value].value
    return None


def _fan_number(value: Any) -> int | None:
    number = enum_number(value)
    return number + 1 if number is not None else None


@dataclass(frozen=True, kw_only=True)
class AmbientikaSensorDescription(SensorEntityDescription):
    """A sensor that shows one value of the unit."""

    source: str
    convert: Callable[[Any], Any] = lambda value: value


def _text(
    key, source, options, icon, *, diagnostic=False
) -> AmbientikaSensorDescription:
    return AmbientikaSensorDescription(
        key=key,
        translation_key=key,
        source=source,
        device_class=SensorDeviceClass.ENUM,
        options=options,
        icon=icon,
        convert=_filter_state if options is FILTER_OPTIONS else _enum_state(options),
        entity_category=EntityCategory.DIAGNOSTIC if diagnostic else None,
    )


def _number(key, source, convert) -> AmbientikaSensorDescription:
    """Return a number next to a text value, for long-term statistics."""
    return AmbientikaSensorDescription(
        key=key,
        translation_key=key,
        source=source,
        convert=convert,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:numeric",
    )


def _measured(key, device_class, unit) -> AmbientikaSensorDescription:
    return AmbientikaSensorDescription(
        key=key,
        translation_key=key,
        source=key,
        device_class=device_class,
        native_unit_of_measurement=unit,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1 if key.endswith("_avg") else None,
    )


SENSORS: tuple[AmbientikaSensorDescription, ...] = (
    _measured("temperature", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS),
    _measured("humidity", SensorDeviceClass.HUMIDITY, PERCENTAGE),
    _measured(
        "temperature_avg", SensorDeviceClass.TEMPERATURE, UnitOfTemperature.CELSIUS
    ),
    _measured("humidity_avg", SensorDeviceClass.HUMIDITY, PERCENTAGE),
    _text(
        "filter_status_raw",
        "filters_status_raw",
        FILTER_OPTIONS,
        "mdi:air-filter",
        diagnostic=True,
    ),
    _text("operating_mode", "operating_mode", MODE_OPTIONS, "mdi:fan"),
    _text(
        "operating_mode_raw",
        "operating_mode_raw",
        MODE_OPTIONS,
        "mdi:fan",
        diagnostic=True,
    ),
    _text("fan_speed", "fan_speed", FAN_OPTIONS, "mdi:speedometer"),
    _text(
        "humidity_level", "humidity_level", HUMIDITY_LEVEL_OPTIONS, "mdi:water-percent"
    ),
    _text(
        "light_sensor_level", "light_sensor_level", LIGHT_OPTIONS, "mdi:brightness-5"
    ),
    _text(
        "device_role", "device_role", ROLE_OPTIONS, "mdi:information", diagnostic=True
    ),
    _text("last_operating_mode", "last_operating_mode", MODE_OPTIONS, "mdi:fan-clock"),
    AmbientikaSensorDescription(
        key="zone_index",
        translation_key="zone_index",
        source="zone_index",
        icon="mdi:home-group",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    _number("air_quality_num", "air_quality", air_quality_to_num),
    _number("filter_status_num", "filters_status", filter_status_to_num),
    _number("filter_status_raw_num", "filters_status_raw", filter_status_to_num),
    _number("operating_mode_num", "operating_mode", enum_number),
    _number("operating_mode_raw_num", "operating_mode_raw", enum_number),
    _number("last_operating_mode_num", "last_operating_mode", enum_number),
    _number("fan_speed_num", "fan_speed", _fan_number),
    _number("humidity_level_num", "humidity_level", enum_number),
    _number("light_sensor_level_num", "light_sensor_level", enum_number),
    # The unit's humidity reading as reported, also above 100 % while its
    # sensor is saturated (Humidity shows 100 % then). No long-term statistics.
    AmbientikaSensorDescription(
        key="humidity_raw",
        translation_key="humidity_raw",
        source="humidity_raw",
        native_unit_of_measurement=PERCENTAGE,
        icon="mdi:water-percent-alert",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

# Only with the option slave_filter_soft_reset.
ACK_SENSORS: tuple[AmbientikaSensorDescription, ...] = (
    AmbientikaSensorDescription(
        key="filter_ack_since",
        translation_key="filter_ack_since",
        source="filter_ack_since",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-check",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    AmbientikaSensorDescription(
        key="filter_ack_until",
        translation_key="filter_ack_until",
        source="filter_ack_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-clock",
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the `sensor` entities for each device."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # The hub has read the status of every unit before the entities are created.
    entities: list[SensorEntity] = []
    for device in hub.devices:
        entities.append(AirQualitySensor(device, hub))
        entities.append(FilterStatusSensor(device, hub))
        entities.extend(
            ValueSensor(device, hub, description) for description in SENSORS
        )
        entities.append(FilterResetStatusSensor(device, hub))
        entities.append(LastStatusSensor(device, hub))
        entities.append(FailedReadingsSensor(device, hub))
        if hub.soft_reset:
            entities.extend(
                ValueSensor(device, hub, description) for description in ACK_SENSORS
            )
    if not hub.soft_reset:
        # The option was switched off again: its two sensors go.
        registry = er.async_get(hass)
        for device in hub.devices:
            for description in ACK_SENSORS:
                entity_id = registry.async_get_entity_id(
                    "sensor", DOMAIN, unique_id(device.serial_number, description.key)
                )
                if entity_id:
                    registry.async_remove(entity_id)
    cfg = hub.nc_cfg
    wanted = []
    if cfg.radon_configured and (cfg.radon_source == "signal" and cfg.radon_entities):
        wanted.append("radon_level")
    if cfg.dewpoint_configured and cfg.dewpoint_source == "computed":
        wanted += ["dewpoint_indoor", "dewpoint_outdoor"]
    async_remove_neuracell_entities(hass, entry, "sensor", wanted)
    entities.extend(NeuraCellSensor(hub, entry, key) for key in wanted)
    async_add_entities(entities)


class SensorBase(AmbientikaEntity, SensorEntity):
    """Base representation of an Ambientika Sensor."""

    _attr_has_entity_name = True


class ValueSensor(SensorBase):
    """A sensor that shows one value of the unit."""

    entity_description: AmbientikaSensorDescription

    def __init__(
        self,
        device: Device,
        hub: AmbientikaHub,
        description: AmbientikaSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(device, hub)
        self.entity_description = description
        self._attr_unique_id = unique_id(device.serial_number, description.key)

    @property
    def native_value(self):
        """State of the sensor."""
        view = self._view
        if view is None:
            return None
        try:
            return self.entity_description.convert(
                view.get(self.entity_description.source)
            )
        except Exception:  # noqa: BLE001
            return None


class AirQualitySensor(SensorBase):
    """Sensor for the Air Quality status."""

    _attr_translation_key = "air_quality"
    _attr_icon = "mdi:air-purifier"

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "air_quality")

    @property
    def native_value(self):
        """State of the sensor."""
        if self._view:
            value = self._view["air_quality"]
            if not isinstance(value, str) or not value:
                return None
            if value in AirQuality.__members__:
                return AirQuality[value].value
            # A value this version does not know yet is shown as reported.
            return value.lower()[:255]


class FilterStatusSensor(SensorBase):
    """Sensor for the Filter Status.

    With the option slave_filter_soft_reset a Slave whose maintenance was
    acknowledged reads "good"; the unit's own value is the raw filter status.
    """

    _attr_translation_key = "filter_status"
    _attr_icon = "mdi:air-filter"
    _attr_device_class = SensorDeviceClass.ENUM
    # The state is the enum value ("good"), so the options are the values too;
    # they are also the keys of the translated state names.
    _attr_options = FILTER_OPTIONS

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(self._device.serial_number, "filter_status")

    @property
    def native_value(self):
        """State of the sensor."""
        if self._view:
            value = self._view["filters_status"]
            state = _filter_state(value)
            if state is None:
                # An enum sensor only accepts its listed options.
                LOGGER.debug(
                    "Unknown filter status %r for device %s",
                    value,
                    self._device.serial_number,
                )
            return state


class FilterResetStatusSensor(SensorBase):
    """Whether the last filter reset really took.

    The cloud accepts a filter reset without saying whether the unit carried it
    out. This sensor says what the status of the unit showed afterwards.
    """

    _attr_translation_key = "filter_reset_status"
    _attr_icon = "mdi:air-filter"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = RESET_OPTIONS
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(device.serial_number, "reset_state")

    @property
    def native_value(self):
        """State of the sensor."""
        return self._hub.reset_state(self._device.serial_number)

    @property
    def extra_state_attributes(self):
        """What a filter reset does on this unit, and the last one pressed."""
        return self._hub.reset_info(self._device)


class DiagnosticSensor(SensorBase):
    """A sensor that stays readable while the unit itself is unavailable."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def available(self) -> bool:
        """Return True: these values are what is needed when a unit goes silent."""
        return True

    @property
    def device_info(self) -> dict:
        """Return information to link this entity with the correct device."""
        return device_info(self._device)


class LastStatusSensor(DiagnosticSensor):
    """When the cloud last handed out a status packet of the unit."""

    _attr_translation_key = "last_status"
    _attr_icon = "mdi:clock-check-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(device.serial_number, "last_status")

    @property
    def native_value(self):
        """State of the sensor."""
        return self._hub.diagnostics(self._device.serial_number)["last_status"]


class FailedReadingsSensor(DiagnosticSensor):
    """How many readings of the unit have failed since the integration started."""

    _attr_translation_key = "poll_failures"
    _attr_icon = "mdi:cloud-alert-outline"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING

    def __init__(self, device, hub: AmbientikaHub):
        """Initialize the sensor."""
        super().__init__(device, hub)
        self._attr_unique_id = unique_id(device.serial_number, "poll_failures")

    @property
    def native_value(self):
        """State of the sensor."""
        return self._hub.diagnostics(self._device.serial_number)["poll_failures_total"]

    @property
    def extra_state_attributes(self):
        """The current run of failures and the most recent outage."""
        diag = dict(self._hub.diagnostics(self._device.serial_number))
        diag.pop("last_status")
        diag.pop("poll_failures_total")
        return diag


NEURACELL_SENSORS = {
    # key: (value in the state of NeuraCell-X, unit, device class, icon)
    "radon_level": ("radon", "Bq/m³", None, "mdi:radioactive"),
    "dewpoint_indoor": (
        "indoor_dew_point",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
        "mdi:thermometer-water",
    ),
    "dewpoint_outdoor": (
        "outdoor_dew_point",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
        "mdi:thermometer-water",
    ),
}


class NeuraCellSensor(CoordinatorEntity[AmbientikaHub], SensorEntity):
    """A value NeuraCell-X goes by: the radon level, the dew points."""

    _attr_has_entity_name = True
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hub: AmbientikaHub, entry: ConfigEntry, key: str) -> None:
        """Initialize the sensor."""
        super().__init__(hub)
        self._hub = hub
        (
            self._source,
            self._attr_native_unit_of_measurement,
            self._attr_device_class,
            self._attr_icon,
        ) = NEURACELL_SENSORS[key]
        self._attr_translation_key = f"neuracell_{key}"
        self._attr_unique_id = f"{entry.entry_id}_neuracell_{key}"
        self._attr_device_info = neuracell_device_info(entry.entry_id)

    @property
    def available(self) -> bool:
        """Return True: the value is known whatever the cloud does."""
        return True

    @property
    def native_value(self):
        """State of the sensor."""
        return self._hub.neuracell_state()[self._source]
