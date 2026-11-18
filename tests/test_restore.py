"""Tests for bringing back the entity entries of a setup that was deleted.

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
_spec = importlib.util.spec_from_file_location(
    "ambientika_unique_ids_restore", MODULE_PATH
)
unique_ids = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = unique_ids
_spec.loader.exec_module(unique_ids)

plan_restore = unique_ids.plan_restore
legacy_unique_id = unique_ids.legacy_unique_id
unique_id = unique_ids.unique_id

KEYS = [
    (domain, key) for domain, keys in unique_ids.ENTITY_KEYS.items() for key in keys
]
UNITS = {
    "A0B1C2D3E402": "Flur",
    "A0B1C2D3E401": "Wohnzimmer",
    "A0B1C2D3E403": "Kinderzimmer",
    "A0B1C2D3E405": "Gäste-Bad",
    "A0B1C2D3E404": "Schlafzimmer Eltern",
}


def old_ids(name, serial, keys=None):
    """Return the ``(domain, unique ID)`` pairs version 1.2 used for a unit."""
    return {
        (domain, legacy_unique_id(name, serial, key))
        for domain, key in KEYS
        if keys is None or key in keys
    }


def new_ids(serial, keys=None):
    """Return the ``(domain, unique ID)`` pairs used for a unit since 1.3."""
    return {
        (domain, unique_id(serial, key))
        for domain, key in KEYS
        if keys is None or key in keys
    }


def apply(plan, deleted, active):
    """Return what Home Assistant holds after the plan was carried out."""
    deleted, active = set(deleted), set(active)
    for domain, old_unique_id, new_unique_id in plan:
        deleted.remove((domain, old_unique_id))
        active.add((domain, new_unique_id))
    return deleted, active


def assert_consistent(names, deleted, active):
    """Plan, carry out and check what must hold for every registry."""
    plan = plan_restore(names, deleted, active)
    news = [(domain, new) for domain, _, new in plan]
    olds = [(domain, old) for domain, old, _ in plan]
    # nothing is brought back twice and nothing lands on a used ID
    assert len(set(news)) == len(news)
    assert len(set(olds)) == len(olds)
    for domain, old_unique_id, new_unique_id in plan:
        assert (domain, old_unique_id) in deleted
        assert (domain, old_unique_id) not in active
        assert (domain, new_unique_id) not in active
        assert (domain, new_unique_id) not in deleted
        serial = next(s for s in names if new_unique_id.startswith(f"{s}_"))
        key = new_unique_id[len(serial) + 1 :]
        assert old_unique_id == legacy_unique_id(names[serial], serial, key)
        if key != "climate":
            # only a unit whose name no other unit carries
            assert list(names.values()).count(names[serial]) == 1
    # a second run finds nothing left to do
    after_deleted, after_active = apply(plan, deleted, active)
    assert plan_restore(names, after_deleted, after_active) == []
    return plan


def test_every_entry_of_a_deleted_setup_is_brought_back():
    """A plain installation that was deleted: all six entities of all units."""
    deleted = set().union(*(old_ids(name, serial) for serial, name in UNITS.items()))
    plan = assert_consistent(UNITS, deleted, set())
    assert len(plan) == 30
    assert {(domain, new) for domain, _, new in plan} == set().union(
        *(new_ids(serial) for serial in UNITS)
    )
    assert ("sensor", "Flur_air_quality", "A0B1C2D3E402_air_quality") in plan
    assert ("button", "Gäste-Bad_filter_reset", "A0B1C2D3E405_filter_reset") in plan
    assert (
        "climate",
        "climate_Schlafzimmer Eltern_A0B1C2D3E404",
        "A0B1C2D3E404_climate",
    ) in plan


def test_nothing_is_planned_without_deleted_entries():
    """A first installation, or one deleted more than 30 days ago."""
    assert plan_restore(UNITS, set(), set()) == []
    # entries of other units, or in a form this integration never used
    stray = {("sensor", "Keller_air_quality"), ("sensor", "something else")}
    assert plan_restore(UNITS, stray, set()) == []


def test_only_what_home_assistant_kept_is_brought_back():
    """Some entries are gone already: the others still come back."""
    deleted = old_ids("Flur", "A0B1C2D3E402", keys={"climate", "air_quality"})
    plan = assert_consistent(UNITS, deleted, set())
    assert sorted(plan) == [
        ("climate", "climate_Flur_A0B1C2D3E402", "A0B1C2D3E402_climate"),
        ("sensor", "Flur_air_quality", "A0B1C2D3E402_air_quality"),
    ]


def test_entries_in_the_new_form_are_left_to_home_assistant():
    """A setup made with 1.3 or newer is brought back by Home Assistant itself."""
    deleted = set().union(*(new_ids(serial) for serial in UNITS))
    assert assert_consistent(UNITS, deleted, set()) == []
    # both forms are there for one unit: the newer setup wins, nothing is planned
    deleted |= old_ids("Flur", "A0B1C2D3E402")
    assert assert_consistent(UNITS, deleted, set()) == []


def test_entities_that_exist_are_not_touched():
    """Live entries stay as they are, in the old form and in the new one."""
    deleted = set().union(*(old_ids(name, serial) for serial, name in UNITS.items()))
    # Flur is set up already (new form), Wohnzimmer still has live old entries
    active = new_ids("A0B1C2D3E402") | old_ids("Wohnzimmer", "A0B1C2D3E401")
    plan = assert_consistent(UNITS, deleted, active)
    assert len(plan) == 18
    touched = {new.split("_")[0] for _, _, new in plan}
    assert touched == {"A0B1C2D3E403", "A0B1C2D3E405", "A0B1C2D3E404"}


def test_two_units_with_the_same_name():
    """Their old entries cannot be told apart; only the climate entities return."""
    names = {"S1": "Flur", "S2": "Flur", "S3": "Bad"}
    deleted = (
        old_ids("Flur", "S1")  # one set: both units shared these IDs
        | old_ids("Flur", "S2", keys={"climate"})
        | old_ids("Bad", "S3")
    )
    plan = assert_consistent(names, deleted, set())
    assert len(plan) == 8
    assert ("climate", "climate_Flur_S1", "S1_climate") in plan
    assert ("climate", "climate_Flur_S2", "S2_climate") in plan
    assert not [item for item in plan if item[1] == "Flur_air_quality"]
    assert len([item for item in plan if item[2].startswith("S3_")]) == 6


def test_unit_that_was_renamed_since():
    """Entries made under another name are not guessed at."""
    names = dict(UNITS, A0B1C2D3E403="Arbeitszimmer")
    deleted = set().union(*(old_ids(name, serial) for serial, name in UNITS.items()))
    plan = assert_consistent(names, deleted, set())
    assert len(plan) == 24
    assert not [item for item in plan if item[2].startswith("A0B1C2D3E403_")]


def test_name_that_reads_like_the_id_of_another_unit():
    """A unit named like a serial number never takes over that unit's entry."""
    names = {"S1": "S2", "S2": "Bad"}
    # "S2_air_quality" is the old ID of S1 and the new ID of S2 at once
    deleted = {("sensor", "S2_air_quality")} | old_ids("Bad", "S2", keys={"climate"})
    plan = assert_consistent(names, deleted, set())
    assert plan == [("climate", "climate_Bad_S2", "S2_climate")]


def test_random_registries_stay_consistent():
    """Whatever Home Assistant holds: the plan never produces a conflict."""
    rng = random.Random(20261118)
    pool = ["Flur", "Bad", "Küche", "Büro", "S1", "S2", "Schlafzimmer Eltern", ""]
    for _ in range(2000):
        names = {f"S{i}": rng.choice(pool) for i in range(rng.randint(1, 6))}
        candidates = set()
        for serial, name in names.items():
            candidates |= old_ids(name, serial) | new_ids(serial)
            candidates |= old_ids(rng.choice(pool), serial)
        candidates = sorted(candidates)
        deleted = {pair for pair in candidates if rng.random() < 0.5}
        active = {
            pair for pair in candidates if pair not in deleted and rng.random() < 0.3
        }
        assert_consistent(names, deleted, active)
