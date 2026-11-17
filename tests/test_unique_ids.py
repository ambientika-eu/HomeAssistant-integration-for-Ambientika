"""Tests for the move of the unique IDs from the unit's name to its serial number.

The module under test has no Home Assistant imports, so these tests run with
nothing but pytest: ``python -m pytest tests``.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import random
import sys

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "ambientika"
    / "unique_ids.py"
)
_spec = importlib.util.spec_from_file_location("ambientika_unique_ids", MODULE_PATH)
unique_ids = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = unique_ids
_spec.loader.exec_module(unique_ids)

RegisteredEntity = unique_ids.RegisteredEntity
plan_migration = unique_ids.plan_migration
legacy_unique_id = unique_ids.legacy_unique_id
unique_id = unique_ids.unique_id

KEYS = [
    (domain, key) for domain, keys in unique_ids.ENTITY_KEYS.items() for key in keys
]
ENTITY_ID_BY_KEY = {
    "climate": "climate.{slug}",
    "filter_reset": "button.{slug}",
    "humidity_alarm": "binary_sensor.{slug}_humidity_alarm",
    "night_alarm": "binary_sensor.{slug}_night_alarm",
    "air_quality": "sensor.{slug}_air_quality",
    "filter_status": "sensor.{slug}_filter_status",
}


def legacy_entries(
    name, serial, *, attached_to=None, slug=None, created=0.0, keys=None
):
    """Return the registry entries version 1.2 created for a unit."""
    slug = slug or name.lower().replace(" ", "_").replace("-", "_") or "unit"
    return [
        RegisteredEntity(
            entity_id=ENTITY_ID_BY_KEY[key].format(slug=slug),
            domain=domain,
            unique_id=legacy_unique_id(name, serial, key),
            serial_number=attached_to or serial,
            created=created,
        )
        for domain, key in KEYS
        if keys is None or key in keys
    ]


def taken_of(entries):
    """Return the ``(domain, unique ID)`` pairs of registry entries."""
    return {(entry.domain, entry.unique_id) for entry in entries}


def apply(entries, plan):
    """Return the entries as the registry holds them after the plan was applied."""
    return [
        entry._replace(unique_id=plan.get(entry.entity_id, entry.unique_id))
        for entry in entries
    ]


def assert_consistent(entries, names):
    """Plan, apply and check what must hold for every registry."""
    before = taken_of(entries)
    assert len(before) == len(entries), "test set-up: duplicate unique IDs"
    plan = plan_migration(entries, names, before)
    after = apply(entries, plan)
    # the registry never ends up with the same unique ID twice in a domain
    assert len(taken_of(after)) == len(after)
    by_id = {entry.entity_id: entry for entry in entries}
    for entity_id, new_unique_id in plan.items():
        entry = by_id[entity_id]
        assert entry.serial_number in names
        assert new_unique_id.startswith(f"{entry.serial_number}_")
        assert new_unique_id != entry.unique_id
        assert (entry.domain, new_unique_id) not in before
    # a second run finds nothing left to do
    assert plan_migration(after, names, taken_of(after)) == {}
    return plan, after


def test_every_entity_of_every_unit_is_moved():
    """A plain installation: all six entities of all units get the new IDs."""
    units = {
        "A0B1C2D3E402": "Flur",
        "A0B1C2D3E401": "Wohnzimmer",
        "A0B1C2D3E403": "Kinderzimmer",
        "A0B1C2D3E405": "Gäste-Bad",
        "A0B1C2D3E404": "Schlafzimmer Eltern",
    }
    entries = [
        e for serial, name in units.items() for e in legacy_entries(name, serial)
    ]
    plan, after = assert_consistent(entries, units)
    assert len(plan) == 30
    assert taken_of(after) == {
        (domain, unique_id(serial, key)) for serial in units for domain, key in KEYS
    }
    # every entry stays with its own unit and keeps its entity ID
    for before, moved in zip(entries, after, strict=True):
        assert moved.entity_id == before.entity_id
        assert moved.unique_id.startswith(before.serial_number)


def test_ids_have_the_documented_form():
    """The new IDs are serial number plus a fixed key."""
    assert unique_id("A0B1C2D3E402", "air_quality") == "A0B1C2D3E402_air_quality"
    assert unique_id("A0B1C2D3E402", "climate") == "A0B1C2D3E402_climate"
    assert (
        legacy_unique_id("Flur", "A0B1C2D3E402", "filter_reset") == "Flur_filter_reset"
    )
    assert (
        legacy_unique_id("Flur", "A0B1C2D3E402", "climate")
        == "climate_Flur_A0B1C2D3E402"
    )


def test_two_units_with_the_same_name():
    """Version 1.2 gave only one of two units with the same name its entities."""
    names = {"S1": "Flur", "S2": "Flur", "S3": "Bad"}
    entries = (
        legacy_entries("Flur", "S1")  # the unit that got the entities
        + legacy_entries("Flur", "S2", keys={"climate"}, slug="flur_2")
        + legacy_entries("Bad", "S3")
    )
    plan, after = assert_consistent(entries, names)
    assert len(plan) == 13
    ids = taken_of(after)
    assert {(d, unique_id("S1", k)) for d, k in KEYS} <= ids
    assert ("climate", "S2_climate") in ids
    # nothing of the first unit was handed to the second one
    assert not any(uid.startswith("S2_") and uid != "S2_climate" for _, uid in ids)


def test_same_name_entities_follow_the_device_they_are_attached_to():
    """The registry says which of the two units an entry belongs to."""
    names = {"S1": "Flur", "S2": "Flur"}
    entries = legacy_entries(
        "Flur", "S1", attached_to="S2", keys={"air_quality", "filter_reset"}
    ) + legacy_entries("Flur", "S1", keys={"climate"})
    plan, _ = assert_consistent(entries, names)
    assert plan == {
        "sensor.flur_air_quality": "S2_air_quality",
        "button.flur": "S2_filter_reset",
        "climate.flur": "S1_climate",
    }


def test_renamed_unit_after_a_restart_under_the_old_version():
    """Old and new name are both in the registry: the current name wins."""
    names = {"S1": "Gang"}
    old = legacy_entries("Flur", "S1", created=100.0)
    current = legacy_entries("Gang", "S1", created=200.0)
    plan, after = assert_consistent(old + current, names)
    assert set(plan) == {entry.entity_id for entry in current}
    # the entries under the old name stay exactly as they were
    assert [e for e in after if e.entity_id in {o.entity_id for o in old}] == old


def test_current_name_wins_even_if_it_is_the_older_entry():
    """A unit renamed and renamed back: the entries under today's name are kept."""
    names = {"S1": "Flur"}
    current = legacy_entries("Flur", "S1", created=100.0)
    other = legacy_entries("Gang", "S1", created=200.0)
    plan, _ = assert_consistent(other + current, names)
    assert set(plan) == {entry.entity_id for entry in current}


def test_renamed_unit_without_a_restart_in_between():
    """Only the old name is in the registry: its entries are moved."""
    names = {"S1": "Gang"}
    old = legacy_entries("Flur", "S1")
    plan, _ = assert_consistent(old, names)
    assert plan == {
        entry.entity_id: unique_id("S1", key)
        for entry, (_, key) in zip(old, KEYS, strict=True)
    }


def test_renamed_twice_takes_the_newest_entry():
    """Neither entry carries the current name: the newest one is moved."""
    names = {"S1": "Diele"}
    first = legacy_entries("Flur", "S1", created=100.0, keys={"air_quality"})
    second = legacy_entries("Gang", "S1", created=200.0, keys={"air_quality"})
    plan, _ = assert_consistent(first + second, names)
    assert plan == {"sensor.gang_air_quality": "S1_air_quality"}
    plan, _ = assert_consistent(second + first, names)
    assert plan == {"sensor.gang_air_quality": "S1_air_quality"}


def test_tie_is_decided_the_same_way_every_time():
    """Same age, no current name: the choice does not depend on the order."""
    names = {"S1": "Diele"}
    one = legacy_entries("Flur", "S1", keys={"air_quality"})
    two = legacy_entries("Gang", "S1", keys={"air_quality"})
    assert plan_migration(one + two, names, taken_of(one + two)) == plan_migration(
        two + one, names, taken_of(one + two)
    )


def test_units_that_swapped_their_names():
    """Each entry stays with the unit it is attached to."""
    names = {"S1": "Bad", "S2": "Flur"}  # before: S1 was Flur, S2 was Bad
    entries = legacy_entries("Flur", "S1") + legacy_entries("Bad", "S2")
    plan, after = assert_consistent(entries, names)
    assert len(plan) == 12
    for entry in after:
        assert entry.unique_id.startswith(entry.serial_number)


def test_new_id_already_in_use_is_left_alone():
    """Going back to 1.2 and forward again leaves both forms in the registry."""
    names = {"S1": "Flur"}
    legacy = legacy_entries("Flur", "S1", slug="flur_2")
    moved = [
        RegisteredEntity(
            ENTITY_ID_BY_KEY[key].format(slug="flur"),
            domain,
            unique_id("S1", key),
            "S1",
        )
        for domain, key in KEYS
    ]
    plan, _ = assert_consistent(legacy + moved, names)
    assert plan == {}


def test_id_in_use_by_another_config_entry_is_left_alone():
    """``taken`` also covers entries that do not belong to this config entry."""
    names = {"S1": "Flur"}
    entries = legacy_entries("Flur", "S1")
    taken = taken_of(entries) | {("sensor", "S1_air_quality")}
    plan = plan_migration(entries, names, taken)
    assert "sensor.flur_air_quality" not in plan
    assert len(plan) == 5


def test_unit_no_longer_in_the_account_is_left_alone():
    """Entries of a removed unit are not touched."""
    entries = legacy_entries("Flur", "S1") + legacy_entries("Keller", "S9")
    plan, _ = assert_consistent(entries, {"S1": "Flur"})
    assert len(plan) == 6
    assert not any("keller" in entity_id for entity_id in plan)


def test_entry_without_a_device_is_left_alone():
    """Without a device there is no serial number to move the entry to."""
    entries = [
        RegisteredEntity("sensor.flur_air_quality", "sensor", "Flur_air_quality", None)
    ]
    assert plan_migration(entries, {"S1": "Flur"}, taken_of(entries)) == {}


def test_unknown_forms_are_left_alone():
    """Entities the integration no longer provides and foreign IDs stay."""
    names = {"S1": "Flur"}
    entries = [
        RegisteredEntity("sensor.flur_temperature", "sensor", "Flur_temperature", "S1"),
        RegisteredEntity("sensor.flur_humidity", "sensor", "Flur_humidity", "S1"),
        RegisteredEntity("fan.flur", "fan", "Flur_fan", "S1"),
        RegisteredEntity("switch.flur", "switch", "Flur_air_quality", "S1"),
        RegisteredEntity("climate.flur", "climate", "climate_Flur_S2", "S1"),
        RegisteredEntity("climate.flur_2", "climate", "Flur_climate", "S1"),
        RegisteredEntity("button.flur", "button", "Flur", "S1"),
        RegisteredEntity("binary_sensor.flur", "binary_sensor", "night_alarm", "S1"),
    ]
    assert plan_migration(entries, names, taken_of(entries)) == {}


def test_already_moved_entries_are_left_alone():
    """The second start after the update finds nothing to do."""
    names = {"S1": "Flur"}
    entries = [
        RegisteredEntity("x." + key, domain, unique_id("S1", key), "S1")
        for domain, key in KEYS
    ]
    assert plan_migration(entries, names, taken_of(entries)) == {}


def test_names_that_look_like_parts_of_an_id():
    """Underscores, key-like endings, an empty name, a name equal to the serial."""
    names = {
        "S1": "Bad_humidity",
        "S2": "x_night",
        "S3": "",
        "S4": "S4",
        "S5": "climate_S5",
        "S6": "Büro 2. OG / Süd_air",
        "S7": "A_filter",
    }
    entries = [
        entry._replace(entity_id=f"{entry.domain}.u{index}")
        for index, entry in enumerate(
            e for serial, name in names.items() for e in legacy_entries(name, serial)
        )
    ]
    plan, after = assert_consistent(entries, names)
    assert taken_of(after) == {
        (domain, unique_id(serial, key)) for serial in names for domain, key in KEYS
    }
    # a name equal to the serial number already gives the new ID (not the climate)
    assert len(plan) == 7 * 6 - 5


def expected_target(entry, names):
    """Say independently of the module where an entry has to end up, if anywhere."""
    serial = entry.serial_number
    if serial not in names:
        return None
    if entry.domain == "climate":
        uid = entry.unique_id
        if uid.startswith("climate_") and uid.endswith("_" + serial):
            return ("climate", serial + "_climate")
        return None
    for domain, key in KEYS:
        if domain != entry.domain or not entry.unique_id.endswith("_" + key):
            continue
        if entry.unique_id in {other + "_" + key for other in names}:
            return None
        return (domain, serial + "_" + key)
    return None


def test_random_registries():
    """Many random registries: renames, same names, leftovers, partial moves."""
    rng = random.Random(20261117)
    pool = ["Flur", "Bad", "Bad_humidity", "x_night", "", "Gang", "Büro", "S1", "a_b"]
    moved_total = 0
    for _ in range(4000):
        serials = [f"S{n}" for n in range(1, rng.randint(1, 5) + 1)]
        names = {serial: rng.choice(pool) for serial in serials}
        entries, seen, counter = [], set(), 0
        for serial in [*serials, "S8", "S9"]:  # S8, S9: units no longer in the account
            for name in {names.get(serial, "Alt"), rng.choice(pool), rng.choice(pool)}:
                for domain, key in KEYS:
                    if rng.random() < 0.35:
                        continue
                    form = rng.random()
                    if form < 0.7:
                        uid = legacy_unique_id(name, serial, key)
                    elif form < 0.85:
                        uid = unique_id(serial, key)
                    else:
                        uid = f"{name}_{rng.choice(['temperature', 'fan', 'x'])}"
                    if (domain, uid) in seen:
                        continue
                    seen.add((domain, uid))
                    counter += 1
                    attached = (
                        serial if rng.random() < 0.9 else rng.choice([None, *serials])
                    )
                    entries.append(
                        RegisteredEntity(
                            f"{domain}.e{counter}",
                            domain,
                            uid,
                            attached,
                            float(rng.randint(0, 3)),
                        )
                    )
        before = taken_of(entries)
        plan, after = assert_consistent(entries, names)
        moved_total += len(plan)
        ids = taken_of(after)
        targets = set()
        for entry in entries:
            target = expected_target(entry, names)
            if target is None:
                # not an entry of the old form of a unit of the account
                assert entry.entity_id not in plan
                continue
            # the ID it has to end up at exists afterwards ...
            assert target in ids
            if entry.entity_id in plan:
                assert (entry.domain, plan[entry.entity_id]) == target
            targets.add(target)
        # ... and exactly one entry was moved for every ID that was still free
        assert len(plan) == len(targets - before)
    assert moved_total > 10000
