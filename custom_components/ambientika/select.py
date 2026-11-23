"""Select platform for ambientika: the values a unit can be set to.

Operating mode, fan speed, humidity level and the sensitivity of the dusk
sensor (light sensor level), like in the Ambientika MQTT Bridge.
"""

from __future__ import annotations

from dataclasses import dataclass

from ambientika_py import (
    Device,
    FanSpeed,
    HumidityLevel,
    LightSensorLevel,
    OperatingMode,
)

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import AmbientikaEntity
from .hub import AmbientikaHub
from .logic import snake
from .unique_ids import unique_id


@dataclass(frozen=True, kw_only=True)
class AmbientikaSelectDescription(SelectEntityDescription):
    """A value of the unit that can be set."""

    enum_cls: type


def _options(enum_cls) -> list[str]:
    """Return the values of the cloud API as state keys, in its order.

    The fan steps Night and Turbo are chosen by the unit itself. They are
    listed so the state can be shown; selecting one sets the nearest step.
    """
    return [snake(member.name) for member in enum_cls if member.value < 900]


SELECTS: tuple[AmbientikaSelectDescription, ...] = (
    AmbientikaSelectDescription(
        key="operating_mode",
        translation_key="operating_mode",
        icon="mdi:fan",
        enum_cls=OperatingMode,
        options=_options(OperatingMode),
    ),
    AmbientikaSelectDescription(
        key="fan_speed",
        translation_key="fan_speed",
        icon="mdi:speedometer",
        enum_cls=FanSpeed,
        options=_options(FanSpeed),
    ),
    AmbientikaSelectDescription(
        key="humidity_level",
        translation_key="humidity_level",
        icon="mdi:water-percent",
        enum_cls=HumidityLevel,
        options=_options(HumidityLevel),
    ),
    AmbientikaSelectDescription(
        key="light_sensor_level",
        translation_key="light_sensor_level",
        icon="mdi:brightness-5",
        enum_cls=LightSensorLevel,
        options=_options(LightSensorLevel),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create the `select` entities for each device."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        AmbientikaSelect(device, hub, description)
        for device in hub.devices
        for description in SELECTS
    )


class AmbientikaSelect(AmbientikaEntity, SelectEntity):
    """A value of the unit that can be set."""

    _attr_has_entity_name = True
    entity_description: AmbientikaSelectDescription

    def __init__(
        self,
        device: Device,
        hub: AmbientikaHub,
        description: AmbientikaSelectDescription,
    ) -> None:
        """Initialize the select."""
        super().__init__(device, hub)
        self.entity_description = description
        self._attr_unique_id = unique_id(
            device.serial_number, f"{description.key}_select"
        )

    @property
    def current_option(self) -> str | None:
        """Return what the unit reports (for a Slave: the mode of its Master)."""
        view = self._view
        if view is None:
            return None
        key = snake(getattr(view.get(self.entity_description.key), "name", None))
        return key if key in self.options else None

    async def async_select_option(self, option: str) -> None:
        """Send the value to the unit."""
        description = self.entity_description
        member = next(
            (
                m
                for m in description.enum_cls
                if snake(m.name) == option and m.value < 900
            ),
            None,
        )
        if member is None:
            raise ServiceValidationError(f"Unknown option {option!r}")
        await self._hub.async_command(self._device, {description.key: member})
