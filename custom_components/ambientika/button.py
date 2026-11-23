"""Button platform for integration_ambientika.

References:
 - https://developers.home-assistant.io/docs/core/entity/button
 - [BUG](https://github.com/wingertge/ambientika-py/issues/2)
 - https://app.ambientika.eu:4521/swagger/index.html#operations-Device-get_Device_device_status

"""

from __future__ import annotations

from ambientika_py import Device

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, LOGGER
from .entity import device_info
from .hub import AmbientikaHub
from .unique_ids import unique_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create the `button` entities for each device."""
    hub: AmbientikaHub = hass.data[DOMAIN][entry.entry_id]

    # TODO: should we not mount the slave devices?
    async_add_entities(FilterResetButton(device, hub) for device in hub.devices)


class FilterResetButton(CoordinatorEntity[AmbientikaHub], ButtonEntity):
    """The filter reset.

    The reset goes to the unit and to the Master of its zone, and the status of
    the unit is read again afterwards to see whether it took (sensor Filter
    Reset Status). The attributes say what a press does on this unit: a coupled
    Slave keeps its own counter.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "filter_reset"

    def __init__(self, device: Device, hub: AmbientikaHub) -> None:
        """Initialize the button."""
        super().__init__(hub)
        self._device = device
        self._hub = hub
        self._attr_unique_id = unique_id(self._device.serial_number, "filter_reset")

    @property
    def device_info(self):
        """Return information to link this entity with the correct device."""
        return device_info(self._device)

    @property
    def available(self) -> bool:
        """Return True: the button can be pressed whatever the last reading was."""
        return True

    @property
    def extra_state_attributes(self):
        """What a filter reset does on this unit, and the last one pressed."""
        return self._hub.reset_info(self._device)

    async def async_press(self) -> None:
        """Handle the button press."""
        LOGGER.debug("[%s] pressed %s.", "button", self._device.serial_number)
        self._hub.async_start_filter_reset(self._device)
