"""What the entities of a unit have in common."""

from __future__ import annotations

from ambientika_py import Device, DeviceStatus

from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .hub import AmbientikaHub


def device_info(device: Device) -> dict:
    """Return the information that links an entity with its unit."""
    return {
        "identifiers": {(DOMAIN, device.serial_number)},
        "name": device.name,
        "manufacturer": "SUEDWIND",
        "model": "Ambientika",
        "serial_number": device.serial_number,
    }


class AmbientikaEntity(CoordinatorEntity[AmbientikaHub]):
    """An entity that shows a part of the status of a unit.

    The status is read by the hub, once per unit, and shared by all entities of
    that unit.
    """

    def __init__(self, device: Device, hub: AmbientikaHub) -> None:
        """Initialize the entity."""
        super().__init__(hub)
        self._device = device
        self._hub = hub

    @property
    def _status(self) -> DeviceStatus | None:
        """The status of the unit as it was last read, None without one."""
        return self._hub.status(self._device.serial_number)

    @property
    def _view(self) -> dict | None:
        """What is shown for the unit, None while it has no status."""
        return self._hub.view(self._device.serial_number)

    @property
    def device_info(self) -> dict:
        """Return information to link this entity with the correct device."""
        return device_info(self._device)

    @property
    def available(self) -> bool:
        """Return False while the status of the unit cannot be read."""
        return self._view is not None

    async def async_update(self) -> None:
        """Read the status of the unit now (service homeassistant.update_entity)."""
        await self._hub.async_refresh_unit(self._device)
