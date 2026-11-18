"""Move the entries of the entity registry to the unique IDs used since 1.3.

And bring back the entries Home Assistant kept from a setup that was deleted,
so that setting the integration up again continues where the earlier setup
stopped.
"""

from __future__ import annotations

from collections.abc import Iterable

from ambientika_py import Device

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import DOMAIN, LOGGER
from .unique_ids import RegisteredEntity, plan_migration, plan_restore


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


class _DeletedUniqueIds:
    """Answers whether Home Assistant keeps the entry of a deleted entity."""

    def __init__(self, entity_registry: er.EntityRegistry) -> None:
        """Remember the entries Home Assistant kept."""
        self._deleted = getattr(entity_registry, "deleted_entities", {})

    def __contains__(self, item: tuple[str, str]) -> bool:
        """Return True if a deleted entry has ``(domain, unique ID)`` for this integration."""
        domain, unique_id = item
        return (domain, DOMAIN, unique_id) in self._deleted


@callback
def async_restore_deleted_entities(
    hass: HomeAssistant, entry: ConfigEntry, devices: Iterable[Device]
) -> int:
    """Bring back the entity entries of a setup that was deleted.

    Home Assistant keeps the entries of deleted entities for 30 days. An entity
    that is set up again under the same unique ID gets its entity ID back, and
    in current versions its name, icon, area, labels and whether it is hidden
    or disabled. A setup made with version 1.2 or older left entries under
    unique IDs built from the unit's name, which no entity uses any more. They
    are brought back under that ID here and moved to the serial number right
    away, so the entity that is created next takes them over.

    Runs every time the integration starts and does nothing unless such
    entries exist. Returns the number of entries brought back. Whatever goes
    wrong here is logged and never keeps the integration from starting; the
    entities are then created like in a new installation.
    """
    try:
        restored = _restore_deleted_entities(hass, entry, devices)
    except Exception as error:  # noqa: BLE001
        LOGGER.warning(
            "The entries of an earlier setup could not be brought back: %s", error
        )
        return 0
    if restored:
        LOGGER.info(
            "Brought back %s entities of an earlier setup with their entity IDs "
            "and settings",
            restored,
        )
    return restored


def _restore_deleted_entities(
    hass: HomeAssistant, entry: ConfigEntry, devices: Iterable[Device]
) -> int:
    """Bring the entries back and return how many there were."""
    entity_registry = er.async_get(hass)
    names = {device.serial_number: device.name for device in devices}
    kept = getattr(entity_registry, "deleted_entities", {})
    plan = plan_restore(
        names, _DeletedUniqueIds(entity_registry), _TakenUniqueIds(entity_registry)
    )

    restored = 0
    for domain, old_unique_id, new_unique_id in plan:
        # Current Home Assistant versions give the entry its entity ID back by
        # themselves; older ones build a new one, so it is asked for by name.
        entity_id = getattr(kept.get((domain, DOMAIN, old_unique_id)), "entity_id", "")
        try:
            registered = entity_registry.async_get_or_create(
                domain,
                DOMAIN,
                old_unique_id,
                config_entry=entry,
                suggested_object_id=entity_id.partition(".")[2] or None,
            )
        except Exception as error:  # noqa: BLE001
            LOGGER.warning(
                "The entry of an earlier setup for %s could not be brought back: %s",
                new_unique_id,
                error,
            )
            continue
        try:
            entity_registry.async_update_entity(
                registered.entity_id, new_unique_id=new_unique_id
            )
        except Exception as error:  # noqa: BLE001
            LOGGER.warning(
                "The unique ID of %s could not be moved to %s: %s",
                registered.entity_id,
                new_unique_id,
                error,
            )
            # Back to where it was; Home Assistant keeps it as a deleted entry.
            entity_registry.async_remove(registered.entity_id)
            continue
        restored += 1
    return restored


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
