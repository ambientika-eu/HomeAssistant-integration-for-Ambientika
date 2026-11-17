"""Move the entries of the entity registry to the unique IDs used since 1.3."""

from __future__ import annotations

from collections.abc import Iterable

from ambientika_py import Device

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, LOGGER
from .unique_ids import RegisteredEntity, plan_migration


class _TakenUniqueIds:
    """Answers whether the entity registry already has a unique ID."""

    def __init__(self, entity_registry: er.EntityRegistry) -> None:
        """Remember the registry."""
        self._entity_registry = entity_registry

    def __contains__(self, item: tuple[str, str]) -> bool:
        """Return True if ``(domain, unique ID)`` is in use for this integration."""
        domain, unique_id = item
        return (
            self._entity_registry.async_get_entity_id(domain, DOMAIN, unique_id)
            is not None
        )


@callback
def async_migrate_unique_ids(
    hass: HomeAssistant, entry: ConfigEntry, devices: Iterable[Device]
) -> int:
    """Move unique IDs built from the unit's name to the serial number.

    Runs every time the integration starts. It only touches entries that still
    carry an ID in the old form, so it does nothing once they are moved.
    Returns the number of entries moved.
    """
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    names = {device.serial_number: device.name for device in devices}

    entities: list[RegisteredEntity] = []
    for registered in er.async_entries_for_config_entry(
        entity_registry, entry.entry_id
    ):
        if registered.platform != DOMAIN:
            continue
        serial_number = None
        if registered.device_id is not None:
            device = device_registry.async_get(registered.device_id)
            if device is not None:
                serial_number = next(
                    (
                        identifier[1]
                        for identifier in device.identifiers
                        if len(identifier) > 1 and identifier[0] == DOMAIN
                    ),
                    None,
                )
        created = getattr(registered, "created_at", None)
        entities.append(
            RegisteredEntity(
                entity_id=registered.entity_id,
                domain=registered.domain,
                unique_id=registered.unique_id,
                serial_number=serial_number,
                created=created.timestamp() if created is not None else 0.0,
            )
        )

    moved = 0
    plan = plan_migration(entities, names, _TakenUniqueIds(entity_registry))
    for entity_id, new_unique_id in plan.items():
        try:
            entity_registry.async_update_entity(entity_id, new_unique_id=new_unique_id)
        except ValueError as error:
            # Never let the registry keep the integration from starting.
            LOGGER.warning(
                "The unique ID of %s could not be moved to %s: %s",
                entity_id,
                new_unique_id,
                error,
            )
            continue
        moved += 1
    if moved:
        LOGGER.info(
            "Moved the unique IDs of %s entities from the unit's name to its "
            "serial number; entity IDs, names and history are unchanged",
            moved,
        )
    return moved
