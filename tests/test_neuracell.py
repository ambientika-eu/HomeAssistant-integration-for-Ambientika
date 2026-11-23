"""Tests for NeuraCell-X: radon protection, dew-point block, restoring the modes.

The controller is the one of the Ambientika MQTT Bridge; these tests drive it
the way the integration does, with entity IDs as sources. They need pytest and
ambientika_py: ``python -m pytest tests``.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import sys
import types

import pytest

pytest.importorskip("ambientika_py")

COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "ambientika"
# The modules are loaded as a package of their own, without the __init__ of the
# integration (which needs Home Assistant).
_pkg = types.ModuleType("ambientika_nc")
_pkg.__path__ = [str(COMPONENT)]
sys.modules.setdefault("ambientika_nc", _pkg)
nc_mod = importlib.import_module("ambientika_nc.neuracell")

from ambientika_py import FanSpeed, HumidityLevel, OperatingMode  # noqa: E402

RADON = "sensor.radon"


class Cfg:
    """The options with their defaults."""

    neuracell_enabled = True
    dewpoint_enabled = True
    dewpoint_source = "signal"
    radon_threshold = 300
    radon_hysteresis = 50
    radon_protection_fan_speed = FanSpeed.Low
    radon_meter_timeout = 30
    radon_value_key = ""
    radon_device_alarm_field = "air_quality"
    radon_device_alarm_value_set = {"bad"}
    dewpoint_block_key = ""
    dewpoint_controller_key = "ventilating"
    dewpoint_block_device_tokens: set = set()
    dewpoint_device_block_mode_set = {OperatingMode.Off}
    dewpoint_margin = 1.0
    dewpoint_hysteresis = 0.5
    dewpoint_signal_timeout = 0
    dewpoint_controller_timeout = 30
    dewpoint_fallback_timeout = 30
    dewpoint_fallback_fan = "low"
    dewpoint_fallback_fan_speed = FanSpeed.Low
    dewpoint_lost_action = "keep"
    availability_failure_threshold = 3


class Unit:
    """A unit as the cloud holds it."""

    def __init__(self, serial, name, mode=OperatingMode.Smart, role="Master"):
        """Start in a mode, at fan step medium."""
        self.serial_number, self.name, self.role, self.zone_index = (
            serial,
            name,
            role,
            0,
        )
        self.status = {
            "operating_mode": mode,
            "fan_speed": FanSpeed.Medium,
            "humidity_level": HumidityLevel.Normal,
            "last_operating_mode": OperatingMode.Auto,
        }
        self.reachable = True


class Host:
    """Stands in for the hub."""

    def __init__(self, units):
        """Remember the units and what is sent to them."""
        self.devices = {u.serial_number: u for u in units}
        self.sent = []
        self.stored = None

    async def read_status(self, device):
        """Return the status, None for a unit that cannot be reached."""
        return dict(device.status) if device.reachable else None

    async def set_device_mode(self, device, mode, fan, hum):
        """Set a unit, unless it cannot be reached."""
        if not device.reachable:
            return False
        self.sent.append((device.serial_number, mode.name, fan.name))
        device.status.update(operating_mode=mode, fan_speed=fan, humidity_level=hum)
        return True

    def publish_neuracell_state(self):
        """Nothing to show in a test."""

    def publish_known_meters(self):
        """Nothing to keep in a test."""

    def mqtt_connected(self):
        """Return True: states arrive without a broker."""
        return True

    def nc_is_meter(self, source):
        """Return True for the radon sensor of the test."""
        return source == RADON

    def _zone_master(self, device):
        return None

    def nc_load(self):
        """Return what was kept."""
        return self.stored

    def nc_save(self, payload):
        """Keep the baselines."""
        self.stored = payload


def setup(cfg=None):
    """Return two units, the host and the controller."""
    units = [Unit("S1", "Flur"), Unit("S2", "Bad", OperatingMode.Night)]
    host = Host(units)
    return units, host, nc_mod.NeuraCellXController(host, cfg or Cfg())


def modes(units):
    """Return the operating modes of the units."""
    return [u.status["operating_mode"].name for u in units]


def run(coro):
    """Run a test to its end."""
    return asyncio.run(coro)


def test_radon_above_threshold_and_back() -> None:
    """Above the threshold every unit runs Intake/Low; below threshold minus hysteresis they get their modes back."""

    async def go():
        units, host, nc = setup()
        await nc.on_radon_value("120", source=RADON)
        assert not nc.radon_active and not host.sent
        await nc.on_radon_value("350", source=RADON)
        assert nc.radon_active and modes(units) == ["Intake", "Intake"]
        assert {s[2] for s in host.sent} == {"Low"}
        await nc.on_radon_value("270", source=RADON)  # within the hysteresis
        assert nc.radon_active
        await nc.on_radon_value("250", source=RADON)
        assert not nc.radon_active and modes(units) == ["Smart", "Night"]
        assert units[0].status["fan_speed"] == FanSpeed.Medium

    run(go())


def test_dewpoint_block_and_radon_priority() -> None:
    """The block switches off; radon takes over; when radon clears the block holds again."""

    async def go():
        units, host, nc = setup()
        await nc.on_dewpoint_block("on")
        assert modes(units) == ["Off", "Off"]
        await nc.on_radon_alarm("on")
        assert modes(units) == ["Intake", "Intake"]
        await nc.on_radon_alarm("off")
        assert modes(units) == ["Off", "Off"]
        await nc.on_dewpoint_block("off")
        assert modes(units) == ["Smart", "Night"]

    run(go())


def test_block_limited_to_named_units() -> None:
    """A block for named units leaves the others alone, radon still takes all."""

    class Limited(Cfg):
        dewpoint_block_device_tokens = {"bad"}

    async def go():
        units, host, nc = setup(Limited())
        await nc.on_dewpoint_block("on")
        assert modes(units) == ["Smart", "Off"]
        await nc.on_radon_alarm("on")
        assert modes(units) == ["Intake", "Intake"]
        await nc.on_radon_alarm("off")
        assert modes(units) == ["Smart", "Off"]

    run(go())


def test_unreachable_unit_is_restored_later() -> None:
    """A unit that cannot be reached keeps its baseline until the restore works."""

    async def go():
        units, host, nc = setup()
        await nc.on_radon_alarm("on")
        units[1].reachable = False
        await nc.on_radon_alarm("off")
        assert modes(units) == ["Smart", "Intake"] and "S2" in nc._saved_modes
        units[1].reachable = True
        await nc.enforce()
        assert modes(units) == ["Smart", "Night"] and not nc._saved_modes

    run(go())


def test_timed_extraction_is_no_baseline() -> None:
    """A unit in its timed extraction gets back the mode the extraction returns to."""

    async def go():
        units, host, nc = setup()
        units[0].status["operating_mode"] = OperatingMode.TimedExpulsion
        await nc.on_radon_alarm("on")
        await nc.on_radon_alarm("off")
        assert modes(units)[0] == "Auto"

    run(go())


def test_baselines_survive_a_restart(monkeypatch) -> None:
    """Baselines kept over a restart are given back after the start-up grace."""

    async def go():
        units, host, nc = setup()
        await nc.on_radon_alarm("on")
        nc.persist()
        assert host.stored and set(host.stored["saved_modes"]) == {"S1", "S2"}
        again = nc_mod.NeuraCellXController(host, Cfg())
        again.load_persisted()
        assert set(again._saved_modes) == {"S1", "S2"}
        await again.enforce()  # within the grace: nothing is restored yet
        assert modes(units) == ["Intake", "Intake"]
        again._started -= nc_mod.STARTUP_GRACE_S + 1
        await again.enforce()
        assert modes(units) == ["Smart", "Night"]
        # a unit changed by hand while protection was kept is left alone
        await nc.on_radon_alarm("off")

    run(go())


def test_unavailable_sensor_counts_as_disconnected() -> None:
    """An entity that becomes unavailable is a lost radon meter; its value is dropped."""

    async def go():
        units, host, nc = setup()
        nc._meter_note_known(RADON, False)
        await nc.on_radon_meter_availability(RADON, "online")
        await nc.on_radon_value("120", source=RADON)
        assert nc.radon_meter_connected() is True
        await nc.on_radon_meter_availability(RADON, "offline")
        assert nc.radon_meter_connected() is False and nc._effective_radon() is None

    run(go())


def test_computed_dew_point() -> None:
    """Outdoor air that would bring moisture in blocks; clearly drier air releases."""

    async def go():
        units, host, nc = setup()
        for which, value in (
            ("indoor_t", "20"),
            ("indoor_rh", "50"),
            ("outdoor_t", "25"),
            ("outdoor_rh", "80"),
        ):
            await nc.on_dewpoint_sensor(which, value)
        assert nc.dewpoint_block and modes(units) == ["Off", "Off"]
        assert round(nc.indoor_dew_point, 1) == 9.3
        await nc.on_dewpoint_sensor("outdoor_rh", "20")
        assert not nc.dewpoint_block and modes(units) == ["Smart", "Night"]

    run(go())


def test_radon_meter_read_from_the_cloud() -> None:
    """Source "device": a status field of the radon meter decides."""

    async def go():
        units, host, nc = setup()
        meter = Unit("R1", "Radon")
        meter.status["air_quality"] = "Good"
        await nc.poll_radon_device(meter)
        assert not nc.radon_active
        meter.status["air_quality"] = "Bad"
        await nc.poll_radon_device(meter)
        assert nc.radon_active and modes(units) == ["Intake", "Intake"]
        meter.reachable = False
        await nc.poll_radon_device(meter)  # unreachable: the state is kept
        assert nc.radon_active
        meter.reachable = True
        meter.status["air_quality"] = "Good"
        await nc.poll_radon_device(meter)
        assert not nc.radon_active and modes(units) == ["Smart", "Night"]

    run(go())
