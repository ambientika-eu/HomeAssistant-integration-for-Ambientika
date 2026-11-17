"""Platform for climate control integration."""

from __future__ import annotations

from ambientika_py import FanSpeed, HumidityLevel, OperatingMode

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ClimateEntityFeature,
    HVACMode,
    HVACAction,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
# from homeassistant.util.percentage import (
# ordered_list_item_to_percentage,
# percentage_to_ordered_list_item,
# )

from returns.result import Failure, Success

from .compat import is_compat_member, strict_enum_lookup, writable_members
from .const import (
    DOMAIN,
    LOGGER,
    # ORDERED_NAMED_FAN_SPEEDS,
    # ORDERED_NAMED_HUMIDITY_LEVELS,
)
from .entity import AmbientikaEntity
from .hub import AmbientikaHub
from .unique_ids import unique_id

FAN_SPEED_AMBIENTIKA_TO_HVAC = {
    FanSpeed.Low: FAN_LOW,
    FanSpeed.Medium: FAN_MEDIUM,
    FanSpeed.High: FAN_HIGH,
}
FAN_SPEED_HVAC_TO_AMBIENTIKA = {
    value: key for key, value in FAN_SPEED_AMBIENTIKA_TO_HVAC.items()
}
HUMIDITY_LEVEL_TO_INT = {
    HumidityLevel.Dry: 1,
    HumidityLevel.Normal: 2,
    HumidityLevel.Moist: 3,
}
HUMIDITY_INT_TO_LEVEL = {value: key for key, value in HUMIDITY_LEVEL_TO_INT.items()}


def _number(value) -> float | int | None:
    """Return a measured value only if it is a real number."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return value


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the `climate` entities for each device."""
    hub: AmbientikaHub = _hass.data[DOMAIN][entry.entry_id]

    # TODO: should we not mount the slave devices?
    # The hub has read the status of every unit before the entities are created.
    async_add_entities(AmbientikaClimate(device, hub) for device in hub.devices)


class AmbientikaClimate(AmbientikaEntity, ClimateEntity):
    """Representation of an Ambientika device."""

    _attr_has_entity_name = True
    _attr_name = None
    _attr_translation_key = "climate"
    _attr_max_humidity = 3
    _attr_min_humidity = 1
    # _attr_icon = "mdi:air-conditioner"
    # _attr_device_class = "climate"

    @property
    def unique_id(self) -> str:
        """Return a unique ID."""
        return unique_id(self._device.serial_number, "climate")

    @property
    def supported_features(self) -> ClimateEntityFeature:
        """Returns the features supported by this device."""
        return (
            ClimateEntityFeature.TARGET_HUMIDITY
            | ClimateEntityFeature.FAN_MODE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )

    @property
    def is_on(self) -> bool | None:
        """Return true if device is on."""
        if not self._status:
            return

        return self._status["operating_mode"] != OperatingMode.Off

    @property
    def fan_modes(self) -> list[str] | None:
        """Return the list of available fan modes."""
        # Night and Turbo are chosen by the unit itself and cannot be set.
        return [
            FAN_SPEED_AMBIENTIKA_TO_HVAC[fan_speed]
            for fan_speed in writable_members(FanSpeed)
            if fan_speed in FAN_SPEED_AMBIENTIKA_TO_HVAC
        ]

    @property
    def fan_mode(self) -> str | None:
        """Returns the current fan mode."""
        if not self._status:
            return

        fan_speed = self._status["fan_speed"]
        if fan_speed in FAN_SPEED_AMBIENTIKA_TO_HVAC:
            return FAN_SPEED_AMBIENTIKA_TO_HVAC[fan_speed]
        # Read-only step reported by the unit (night, turbo).
        name = getattr(fan_speed, "name", None)
        return name.lower() if name and name != "None" else None

    @property
    def native_temperature_unit(self) -> str:
        """Return the temperature unit."""
        return UnitOfTemperature.CELSIUS

    # Home Assistant up to 2026 reads temperature_unit and current_temperature,
    # newer versions read the native_ names. The entity provides both.
    temperature_unit = native_temperature_unit

    @property
    def preset_modes(self) -> list[str] | None:
        """Returns the list of available operating modes."""
        return [mode.name for mode in writable_members(OperatingMode)]

    @property
    def preset_mode(self) -> str | None:
        """Returns the current operating mode."""
        if not self._status:
            return

        mode = self._status["operating_mode"]
        if is_compat_member(OperatingMode, mode) and mode.name == "None":
            return None
        return mode.name

    @property
    def current_humidity(self) -> int | None:
        """Return the current humidity."""
        if not self._status:
            return

        return _number(self._status["humidity"])

    @property
    def target_humidity(self) -> int | None:
        """Return the target humidity."""
        if not self._status:
            return

        return HUMIDITY_LEVEL_TO_INT.get(self._status["humidity_level"])

    @property
    def native_current_temperature(self) -> float | None:
        """Return the current temperature."""
        if not self._status:
            return

        return _number(self._status["temperature"])

    current_temperature = native_current_temperature

    @property
    def hvac_modes(self) -> list[HVACMode]:
        """List the valid HVAC modes."""
        return [HVACMode.OFF, HVACMode.FAN_ONLY]

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Set the HVAC operating mode. Does nothing for this device."""
        if not self._status:
            return

        if self._status["operating_mode"] == OperatingMode.Off:
            return HVACMode.OFF
        else:
            return HVACMode.FAN_ONLY

    @property
    def hvac_action(self) -> str | None:
        """Return the current HVAC action."""
        if not self._status:
            return

        if self._status["operating_mode"] == OperatingMode.Off:
            return HVACAction.OFF
        else:
            return HVACAction.FAN

    async def async_set_hvac_mode(self, hvac_mode: HVACMode):
        """Set the new HVAC Mode."""
        if not self._status:
            return

        if hvac_mode == HVACMode.OFF:
            await self.async_set_device(operation_mode="Off")
        else:
            await self.async_set_device(operation_mode="Auto")

    async def async_set_preset_mode(self, preset_mode: str):
        """Set the fan operation mode."""
        if not self._status:
            return

        if strict_enum_lookup(OperatingMode, preset_mode) is None:
            LOGGER.error("Unknown operating mode %r", preset_mode)
            return

        await self.async_set_device(operation_mode=preset_mode)

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set the fan mode."""
        if not self._status:
            return

        await self.async_set_device(fan_mode=fan_mode)
        # self.async_write_ha_state()

    async def async_set_humidity(self, humidity: int) -> None:
        """Set the humidity."""
        if not self._status:
            return

        await self.async_set_device(humidity=humidity)

    async def async_turn_on(self) -> None:
        """Turn device on."""
        if not self._status:
            return

        await self.async_set_hvac_mode(hvac_mode=HVACMode.FAN_ONLY)

    async def async_turn_off(self) -> None:
        """Turn device off."""
        if not self._status:
            return

        await self.async_set_hvac_mode(hvac_mode=HVACMode.OFF)

    async def async_set_device(
        self,
        fan_mode: str | None = None,
        operation_mode: str | None = None,
        humidity: int | None = None,
    ) -> None:
        """Make changes to the device."""
        if not self._status:
            return

        # Only values the API accepts are sent back. Read-only values reported
        # by the unit (fan speed Night/Turbo, unknown values) fall back to a
        # default. Note: Smart (0), Low (0) and Dry (0) are valid values, hence
        # the explicit None checks.
        current_mode = self._status["operating_mode"]
        current_speed = self._status["fan_speed"]
        current_humidity = self._status["humidity_level"]

        if operation_mode:
            target_mode = strict_enum_lookup(OperatingMode, operation_mode)
            if target_mode is None:
                LOGGER.error("Unknown operating mode %r", operation_mode)
                return
        elif current_mode is not None and not is_compat_member(
            OperatingMode, current_mode
        ):
            target_mode = current_mode
        else:
            target_mode = OperatingMode.Auto

        if fan_mode:
            if fan_mode not in FAN_SPEED_HVAC_TO_AMBIENTIKA:
                LOGGER.error("Unknown fan mode %r", fan_mode)
                return
            target_speed = FAN_SPEED_HVAC_TO_AMBIENTIKA[fan_mode]
        elif current_speed is not None and not is_compat_member(
            FanSpeed, current_speed
        ):
            target_speed = current_speed
        else:
            target_speed = FanSpeed.Low

        if humidity:
            target_humidity = HUMIDITY_INT_TO_LEVEL.get(int(humidity))
            if target_humidity is None:
                LOGGER.error("Unknown humidity level %r", humidity)
                return
        elif current_humidity is not None and not is_compat_member(
            HumidityLevel, current_humidity
        ):
            target_humidity = current_humidity
        else:
            target_humidity = HumidityLevel.Normal

        LOGGER.debug(
            "Writing to device %s: mode %s, fan speed %s, humidity_level %s",
            self._device.serial_number,
            target_mode,
            target_speed,
            target_humidity,
        )
        status = await self._hub.async_request(
            self._device,
            lambda: self._device.change_mode(
                {
                    "operating_mode": target_mode,
                    "fan_speed": target_speed,
                    "humidity_level": target_humidity,
                }
            ),
        )
        match status:
            case Success(_):
                LOGGER.debug(
                    "Writing to device %s: success", self._device.serial_number
                )
            case Failure(error):
                LOGGER.error(
                    "Writing to device %s: failed. %s",
                    self._device.serial_number,
                    error,
                )
        # Home Assistant reads a polling entity again after a service call, but
        # not an entity that shares its status. So the unit is read here, and all
        # its entities show what it reports after the command.
        await self._hub.async_refresh_unit(self._device, join=False)
