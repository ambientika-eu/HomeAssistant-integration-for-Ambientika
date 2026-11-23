"""Tests for the values shown for a unit: plausibility, averages, per-unit options.

These tests run with nothing but pytest: ``python -m pytest tests``.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
import sys

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "ambientika"
_spec = importlib.util.spec_from_file_location(
    "ambientika_logic", COMPONENT / "logic.py"
)
logic = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = logic
_spec.loader.exec_module(logic)

LOG = logging.getLogger("test")
UNIT = "A0B1C2D3E402"


def measurements(**options) -> logic.Measurements:
    """Return the evaluation of one installation with a reading every 30 s."""
    m = logic.Measurements(LOG, 30)
    m.configure(options, {UNIT: "Gäste Bad", "A0B1C2D3E401": "Flur"})
    return m


def test_single_impossible_humidity_is_held_back() -> None:
    """6 % between 55 and 58 % is not shown; the last plausible value stays."""
    m = measurements()
    assert m.plausible(UNIT, "humidity", 55, True, 0) == 55
    assert m.plausible(UNIT, "humidity", 6, True, 30) == 55
    assert m.plausible(UNIT, "humidity", 58, True, 60) == 58


def test_dry_air_is_confirmed_by_its_next_reading() -> None:
    """Really dry air is shown from the second dry reading on."""
    m = measurements()
    assert m.plausible(UNIT, "humidity", 40, True, 0) == 40
    assert m.plausible(UNIT, "humidity", 15, True, 30) == 40
    assert m.plausible(UNIT, "humidity", 14, True, 60) == 14


def test_same_packet_does_not_confirm_itself() -> None:
    """A held-back value read again from the same packet stays held back."""
    m = measurements()
    assert m.plausible(UNIT, "humidity", 55, True, 0) == 55
    assert m.plausible(UNIT, "humidity", 6, True, 30) == 55
    assert m.plausible(UNIT, "humidity", 6, False, 60) == 55
    assert m.plausible(UNIT, "humidity", 6, False, 90) == 55


def test_held_back_value_turns_unknown_after_ten_minutes() -> None:
    """A sensor that only reports impossible values does not look live."""
    m = measurements()
    assert m.plausible(UNIT, "temperature", 21, True, 0) == 21
    assert m.plausible(UNIT, "temperature", 150, True, 30) == 21
    assert m.plausible(UNIT, "temperature", 151, True, 700) is None
    assert m.plausible(UNIT, "temperature", 22, True, 730) == 22


def test_saturated_humidity_sensor_reads_100() -> None:
    """Above 100 % the sensor is saturated: shown as 100 %, never held back."""
    m = measurements()
    assert m.plausible(UNIT, "humidity", 118, True, 0) == 100
    assert m.plausible(UNIT, "humidity", 141, True, 30) == 100


def test_values_that_are_no_numbers() -> None:
    """Text, NaN and booleans are no readings."""
    m = measurements()
    assert m.plausible(UNIT, "temperature", None) is None
    assert m.plausible(UNIT, "temperature", "warm", True, 0) is None
    assert m.plausible(UNIT, "temperature", float("nan"), True, 30) is None
    assert m.plausible(UNIT, "temperature", True, True, 60) is None


def test_average_needs_two_minutes_and_is_time_weighted() -> None:
    """The mean appears once the readings cover two minutes."""
    m = measurements()
    assert m.average(UNIT, "humidity", 50, 0) is None
    assert m.average(UNIT, "humidity", 60, 30) is None
    assert m.average(UNIT, "humidity", 50, 60) is None
    assert m.average(UNIT, "humidity", 60, 90) == 55.0
    # a reading taken right after another one hardly counts
    assert m.average(UNIT, "humidity", 100, 91) == 55.4


def test_average_ignores_gaps_and_ends_without_readings() -> None:
    """Unknown readings do not count; after ten minutes without one the mean is unknown."""
    m = measurements()
    for index in range(6):
        m.average(UNIT, "temperature", 20, index * 30)
    assert m.average(UNIT, "temperature", None, 180) == 20.0
    assert m.average(UNIT, "temperature", None, 180 + 601) is None


def test_average_window_per_unit_and_floor() -> None:
    """A unit's own window, never below twenty reading intervals."""
    m = measurements(
        average_window_min=30, average_window_devices="Gäste-Bad=5, a0b1c2d3e401=60"
    )
    assert m.average_window_s(UNIT) == 600.0  # 5 min set, at least 20 x 30 s
    assert m.average_window_s("A0B1C2D3E401") == 3600.0
    assert m.average_window_s("other") == 1800.0
    m = measurements(temperature_avg_window_min=90)
    assert m.average_window_s(UNIT, "temperature") == 5400.0
    assert m.average_window_s(UNIT, "humidity") == 1800.0


def test_hysteresis_holds_small_changes() -> None:
    """With a hysteresis the mean only moves once it has changed by that much."""
    m = measurements(
        humidity_avg_hysteresis=2, humidity_avg_hysteresis_devices="Flur=0"
    )
    assert m.hold("humidity", UNIT, 55.0) == 55.0
    assert m.hold("humidity", UNIT, 56.5) == 55.0
    assert m.hold("humidity", UNIT, 57.0) == 57.0
    assert m.hold("humidity", UNIT, None) is None
    assert m.hold("humidity", UNIT, 56.0) == 56.0
    assert m.hold("humidity", "A0B1C2D3E401", 55.0) == 55.0
    assert m.hold("humidity", "A0B1C2D3E401", 55.1) == 55.1
    assert m.hold("temperature", UNIT, 21.1) == 21.1


def test_per_unit_entries() -> None:
    """The form "name=value", with units, other separators and mistakes."""
    entries, problems = logic.parse_unit_values(
        "Bad: oben=15 min; Flur:20\nx, Keller=abc, Dach=900",
        logic.AVERAGE_WINDOW_RANGE,
        ("min",),
        "minutes",
        " min",
    )
    assert [(label, value) for _, label, value in entries] == [
        ("Bad: oben", 15.0),
        ("Flur", 20.0),
        ("Dach", 240.0),
    ]
    assert len(problems) == 3
    assert logic.name_key("Gäste-Bad") == logic.name_key("gäste  bad")
    assert logic.parse_bounded_float("0,5", (0, 5), (), "degrees", " °C")[1] is not None
    assert logic.parse_bounded_float(
        "0.5 °C", (0, 5), logic.TEMPERATURE_SUFFIXES, "degrees", " °C"
    ) == (0.5, None)


def test_numbers_and_state_keys() -> None:
    """Numbers next to the text values, and the keys of the translated states."""
    assert [
        logic.air_quality_to_num(v)
        for v in ("VeryGood", "Good", "Medium", "Poor", "Bad", "?")
    ] == [4, 3, 2, 1, 0, None]
    assert [logic.filter_status_to_num(v) for v in ("Good", "Medium", "Bad", None)] == [
        0,
        1,
        2,
        None,
    ]
    assert logic.snake("ManualHeatRecovery") == "manual_heat_recovery"
    assert logic.snake("SlaveEqualMaster") == "slave_equal_master"
    assert logic.snake("NotAvailable") == "not_available"
    assert logic.snake("Off") == "off"
    assert logic.snake(None) is None
    assert logic.raw_number(118) == 118 and logic.raw_number(float("inf")) is None
