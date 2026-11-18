"""Unique IDs of the entities.

Up to version 1.2 the unique IDs were built from the name a unit has in the
Ambientika app. Two units with the same name therefore shared their IDs, and
Home Assistant set up the entities of only one of them; renaming a unit in the
app left the old entities behind and created new ones.

Since version 1.3 the unique IDs are built from the serial number, which never
changes. The entries Home Assistant already has in its entity registry are
moved to the new IDs when the integration starts, so entity IDs, names, areas
and the history stay as they are.

When the integration is deleted, Home Assistant keeps the entries of its
entities for a while and gives an entity its entity ID and its settings back
once it is set up again under the same unique ID. Entries of a setup made with
version 1.2 or older carry an ID in the old form; they are brought back under
that ID first and then moved like any other entry.

This module has no Home Assistant imports; ``plan_migration`` and
``plan_restore`` only decide which entries are moved or brought back.
"""

from __future__ import annotations

from collections.abc import Container, Iterable, Mapping
from typing import NamedTuple

# Entities every unit has, by entity domain.
ENTITY_KEYS: dict[str, tuple[str, ...]] = {
    "binary_sensor": ("humidity_alarm", "night_alarm"),
    "button": ("filter_reset",),
    "climate": ("climate",),
    "sensor": ("air_quality", "filter_status"),
}


def unique_id(serial_number: str, key: str) -> str:
    """Return the unique ID of an entity of a unit."""
    return f"{serial_number}_{key}"


def legacy_unique_id(name: str, serial_number: str, key: str) -> str:
    """Return the unique ID the same entity had up to version 1.2."""
    if key == "climate":
        return f"climate_{name}_{serial_number}"
    return f"{name}_{key}"


def _legacy_key(domain: str, old_unique_id: str, serial_number: str) -> str | None:
    """Return the key of an entity whose unique ID has the form used up to 1.2."""
    if domain == "climate":
        head, tail = "climate_", f"_{serial_number}"
        if (
            old_unique_id.startswith(head)
            and old_unique_id.endswith(tail)
            and len(old_unique_id) >= len(head) + len(tail)
        ):
            return "climate"
        return None
    for key in ENTITY_KEYS.get(domain, ()):
        if old_unique_id.endswith(f"_{key}"):
            return key
    return None


class RegisteredEntity(NamedTuple):
    """An entry of the entity registry, reduced to what the migration needs."""

    entity_id: str
    domain: str
    unique_id: str
    # Serial number of the device the entry is attached to, if known.
    serial_number: str | None
    # When the entry was created (timestamp); newer entries win a tie.
    created: float = 0.0


def plan_migration(
    entities: Iterable[RegisteredEntity],
    names: Mapping[str, str],
    taken: Container[tuple[str, str]],
) -> dict[str, str]:
    """Return ``{entity_id: new unique ID}`` for the entries that have to be moved.

    ``entities`` are the registry entries of the config entry, ``names`` maps the
    serial number of every unit of the account to its current name, and ``taken``
    answers whether the registry already has a ``(domain, unique ID)`` pair for
    this integration.

    An entry is moved to the serial number of the device it is attached to. This
    is what keeps two units with the same name apart: the entry belongs to
    exactly one of them. Entries of devices that are no longer part of the
    account, entries in an unknown form and entries whose new ID is already in
    use are left untouched.

    After a unit was renamed, the registry can hold entries under the old and
    under the new name for the same entity. Then the entry under the current
    name is moved, otherwise the newest one; the others stay as they are.
    """
    candidates: dict[tuple[str, str], list[tuple[bool, float, str]]] = {}
    for entity in entities:
        serial_number = entity.serial_number
        if serial_number is None or serial_number not in names:
            continue
        key = _legacy_key(entity.domain, entity.unique_id, serial_number)
        if key is None:
            continue
        if any(entity.unique_id == unique_id(other, key) for other in names):
            # Already in the form used since 1.3.
            continue
        new_unique_id = unique_id(serial_number, key)
        is_current = entity.unique_id == legacy_unique_id(
            names[serial_number], serial_number, key
        )
        candidates.setdefault((entity.domain, new_unique_id), []).append(
            (is_current, entity.created, entity.entity_id)
        )

    plan: dict[str, str] = {}
    for (domain, new_unique_id), found in candidates.items():
        if (domain, new_unique_id) in taken:
            continue
        found.sort(key=lambda item: (not item[0], -item[1], item[2]))
        plan[found[0][2]] = new_unique_id
    return plan


def plan_restore(
    names: Mapping[str, str],
    deleted: Container[tuple[str, str]],
    active: Container[tuple[str, str]],
) -> list[tuple[str, str, str]]:
    """Return ``(domain, old unique ID, new unique ID)`` for the entries to bring back.

    ``names`` maps the serial number of every unit of the account to its current
    name. ``deleted`` answers whether Home Assistant still keeps the entry of a
    deleted entity under a ``(domain, unique ID)`` pair of this integration,
    ``active`` whether the registry has a live entry under it.

    An entry in the form used up to 1.2 is brought back when the unit still
    carries the name the entry was made with. Two units with the same name
    shared their old IDs, so their entries cannot be told apart and are left
    alone; the climate entity always had the serial number in its ID and is
    brought back in any case. Nothing is planned for an entity that already has
    an entry, live or deleted, under its ID in the form used since 1.3: Home
    Assistant brings that one back by itself.
    """
    all_names = list(names.values())
    plan: list[tuple[str, str, str]] = []
    for serial_number, name in names.items():
        for domain, keys in ENTITY_KEYS.items():
            for key in keys:
                new_unique_id = unique_id(serial_number, key)
                if (domain, new_unique_id) in active or (
                    domain,
                    new_unique_id,
                ) in deleted:
                    continue
                old_unique_id = legacy_unique_id(name, serial_number, key)
                if any(old_unique_id == unique_id(other, key) for other in names):
                    # The name of this unit reads like the ID of another one.
                    continue
                if (domain, old_unique_id) in active or (
                    domain,
                    old_unique_id,
                ) not in deleted:
                    continue
                if key != "climate" and all_names.count(name) > 1:
                    continue
                plan.append((domain, old_unique_id, new_unique_id))
    return plan
