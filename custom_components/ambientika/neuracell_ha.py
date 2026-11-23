"""NeuraCell-X in Home Assistant: options, the link to the hub, and the inputs.

The controller itself is in neuracell.py. Here its inputs come from Home
Assistant entities instead of MQTT topics:

* radon: one or more sensors with the radon value in Bq/m³, and optionally an
  entity that is "on" while a radon alarm is active;
* dew point: an entity that is "on" while ventilating must be blocked, or the
  entity of the Taupunktsteuerung that is "on" while it ventilates, or four
  sensors (temperature and humidity indoors and outdoors) from which the block
  is worked out.

An entity that becomes unavailable counts like a device whose connection is
lost. Radon meter and Taupunktsteuerung can also be read from the Ambientika
cloud (source "device").
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import time
from typing import Any

from ambientika_py import Device, FanSpeed, OperatingMode

from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, HomeAssistant, State, callback
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_state_report_event,
)

from . import const as c
from .compat import strict_enum_lookup
from .const import DOMAIN, LOGGER
from .neuracell import STARTUP_GRACE_S, NeuraCellXController


def _int(value: Any, low: int, high: int, default: int) -> int:
    try:
        return int(min(high, max(low, float(value))))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number == number else default


def _tokens(value: Any) -> set[str]:
    raw = str(value or "").replace(";", ",")
    return {t.strip().lower() for t in raw.split(",") if t.strip()}


class NeuraCellConfig:
    """The options of NeuraCell-X in the form the controller reads them."""

    # An entity state is the value itself, there is no key to look up.
    radon_value_key = ""
    dewpoint_block_key = ""
    dewpoint_controller_key = "ventilating"

    def __init__(self, options: dict[str, Any], failure_threshold: int) -> None:
        """Take the options, each within its limits."""
        get = options.get
        self.availability_failure_threshold = failure_threshold
        self.neuracell_enabled = bool(get(c.CONF_NEURACELL_ENABLED, True))
        self.radon_source = (
            "device" if get(c.CONF_RADON_SOURCE) == "device" else "signal"
        )
        entities = get(c.CONF_RADON_ENTITIES) or []
        if isinstance(entities, str):
            entities = [entities]
        self.radon_entities: list[str] = [
            e for e in entities if isinstance(e, str) and e
        ]
        self.radon_alarm_entity = str(get(c.CONF_RADON_ALARM_ENTITY) or "")
        self.radon_threshold = _int(get(c.CONF_RADON_THRESHOLD), 50, 1000, 300)
        self.radon_hysteresis = _int(get(c.CONF_RADON_HYSTERESIS), 0, 500, 50)
        self.radon_protection_fan = str(get(c.CONF_RADON_PROTECTION_FAN) or "low")
        self.radon_meter_timeout = _int(get(c.CONF_RADON_METER_TIMEOUT), 0, 1440, 30)
        self.radon_device_serial = str(get(c.CONF_RADON_DEVICE_SERIAL) or "").strip()
        self.radon_device_alarm_field = (
            str(get(c.CONF_RADON_DEVICE_ALARM_FIELD) or "").strip() or "air_quality"
        )
        self.radon_device_alarm_value_set = _tokens(
            get(c.CONF_RADON_DEVICE_ALARM_VALUES)
        )

        self.dewpoint_enabled = bool(get(c.CONF_DEWPOINT_ENABLED, True))
        source = get(c.CONF_DEWPOINT_SOURCE)
        self.dewpoint_source = source if source in ("computed", "device") else "signal"
        self.dewpoint_block_entity = str(get(c.CONF_DEWPOINT_BLOCK_ENTITY) or "")
        self.dewpoint_controller_entity = str(
            get(c.CONF_DEWPOINT_CONTROLLER_ENTITY) or ""
        )
        self.dewpoint_block_device_tokens = _tokens(get(c.CONF_DEWPOINT_BLOCK_DEVICES))
        self.dewpoint_device_serial = str(
            get(c.CONF_DEWPOINT_DEVICE_SERIAL) or ""
        ).strip()
        modes = set()
        for name in (
            str(get(c.CONF_DEWPOINT_DEVICE_BLOCK_MODES) or "")
            .replace(";", ",")
            .split(",")
        ):
            if not name.strip():
                continue
            mode = strict_enum_lookup(OperatingMode, name.strip())
            if mode is None:
                LOGGER.warning(
                    "dewpoint_device_block_modes: %r is no operating mode - ignored",
                    name,
                )
            else:
                modes.add(mode)
        self.dewpoint_device_block_mode_set = modes or {OperatingMode.Off}
        self.dewpoint_sensor_entities = {
            "indoor_t": str(get(c.CONF_DEWPOINT_INDOOR_TEMP) or ""),
            "indoor_rh": str(get(c.CONF_DEWPOINT_INDOOR_HUMIDITY) or ""),
            "outdoor_t": str(get(c.CONF_DEWPOINT_OUTDOOR_TEMP) or ""),
            "outdoor_rh": str(get(c.CONF_DEWPOINT_OUTDOOR_HUMIDITY) or ""),
        }
        self.dewpoint_margin = _float(get(c.CONF_DEWPOINT_MARGIN), 1.0)
        self.dewpoint_hysteresis = _float(get(c.CONF_DEWPOINT_HYSTERESIS), 0.5)
        self.dewpoint_signal_timeout = _int(
            get(c.CONF_DEWPOINT_SIGNAL_TIMEOUT), 0, 1440, 0
        )
        self.dewpoint_controller_timeout = _int(
            get(c.CONF_DEWPOINT_CONTROLLER_TIMEOUT), 0, 1440, 30
        )
        self.dewpoint_fallback_timeout = _int(
            get(c.CONF_DEWPOINT_FALLBACK_TIMEOUT), 0, 1440, 30
        )
        self.dewpoint_fallback_fan = str(get(c.CONF_DEWPOINT_FALLBACK_FAN) or "low")
        action = get(c.CONF_DEWPOINT_LOST_ACTION)
        self.dewpoint_lost_action = action if action in ("release", "block") else "keep"

    @staticmethod
    def _fan(name: str) -> FanSpeed:
        return (
            strict_enum_lookup(FanSpeed, str(name).strip().capitalize()) or FanSpeed.Low
        )

    @property
    def radon_protection_fan_speed(self) -> FanSpeed:
        """Fan speed of the units while radon protection is active."""
        return self._fan(self.radon_protection_fan)

    @property
    def dewpoint_fallback_fan_speed(self) -> FanSpeed:
        """Fan speed of the fallback ventilation."""
        return self._fan(self.dewpoint_fallback_fan)

    @property
    def radon_configured(self) -> bool:
        """Return True if radon protection is on and has a source."""
        if not self.neuracell_enabled:
            return False
        if self.radon_source == "device":
            return bool(self.radon_device_serial)
        return bool(self.radon_entities or self.radon_alarm_entity)

    @property
    def dewpoint_configured(self) -> bool:
        """Return True if dew-point control is on and has a source."""
        if not self.dewpoint_enabled:
            return False
        if self.dewpoint_source == "device":
            return bool(self.dewpoint_device_serial)
        if self.dewpoint_source == "computed":
            return all(self.dewpoint_sensor_entities.values())
        return bool(self.dewpoint_block_entity or self.dewpoint_controller_entity)

    @property
    def configured(self) -> bool:
        """Return True if NeuraCell-X has anything to do."""
        return self.radon_configured or self.dewpoint_configured


class NeuraCellHost:
    """What the controller asks of its surroundings, answered by the hub."""

    def __init__(self, hub: Any, cfg: NeuraCellConfig) -> None:
        """Remember the hub."""
        self._hub = hub
        self.cfg = cfg

    @property
    def devices(self) -> dict[str, Device]:
        """The units NeuraCell-X may control, by serial number."""
        return {device.serial_number: device for device in self._hub.devices}

    async def read_status(self, device: Device):
        """Read the status of a unit now; None if it cannot be read."""
        return await self._hub.async_read_for_neuracell(device)

    async def set_device_mode(
        self, device, operating_mode, fan_speed, humidity_level
    ) -> bool:
        """Set mode, fan speed and humidity level of a unit."""
        return await self._hub.async_set_device_mode(
            device, operating_mode, fan_speed, humidity_level
        )

    def publish_neuracell_state(self) -> None:
        """Let the entities show the new state, and keep the baselines."""
        self._hub.neuracell.persist()
        self._hub.async_update_listeners()

    def publish_known_meters(self) -> None:
        """Nothing to do: the radon meters are the entities named in the options."""

    def mqtt_connected(self) -> bool:
        """Return True: entity states arrive without a broker in between."""
        return True

    def nc_is_meter(self, source: str) -> bool:
        """Return True for an entity named as a radon sensor."""
        return source in self.cfg.radon_entities

    def _zone_master(self, device: Device):
        return self._hub.zone_master(device)

    def nc_load(self) -> dict | None:
        """Return the baselines kept over a restart."""
        data = self._hub.stored.get("neuracell")
        return data if isinstance(data, dict) else None

    def nc_save(self, payload: dict) -> None:
        """Keep the baselines over a restart."""
        self._hub.stored["neuracell"] = payload
        self._hub.save_store()


def _gone(state: State | None) -> bool:
    return state is None or state.state == STATE_UNAVAILABLE


class NeuraCellInputs:
    """Hands the states of the entities named in the options to the controller."""

    def __init__(
        self,
        hass: HomeAssistant,
        controller: NeuraCellXController,
        cfg: NeuraCellConfig,
    ) -> None:
        """Remember the controller."""
        self._hass = hass
        self._nc = controller
        self._cfg = cfg
        self._queue: asyncio.Queue = asyncio.Queue()
        self._unsub: list[Callable[[], None]] = []
        self._worker: asyncio.Task | None = None
        # entity ID -> True while it counts as connected
        self._online: dict[str, bool] = {}

    def _handlers(self) -> dict[str, Callable]:
        cfg = self._cfg
        handlers: dict[str, Callable] = {}
        if cfg.neuracell_enabled and cfg.radon_source == "signal":
            for entity_id in cfg.radon_entities:
                handlers[entity_id] = self._radon_value
            if cfg.radon_alarm_entity:
                handlers[cfg.radon_alarm_entity] = self._radon_alarm
        if cfg.dewpoint_enabled and cfg.dewpoint_source == "signal":
            if cfg.dewpoint_block_entity:
                handlers[cfg.dewpoint_block_entity] = self._dewpoint_block
            if cfg.dewpoint_controller_entity:
                handlers[cfg.dewpoint_controller_entity] = self._dewpoint_controller
        if cfg.dewpoint_enabled and cfg.dewpoint_source == "computed":
            for which, entity_id in cfg.dewpoint_sensor_entities.items():
                if entity_id:
                    handlers[entity_id] = self._dewpoint_sensor(which)
        return handlers

    @callback
    def async_start(self, create_task: Callable) -> None:
        """Read the entities as they are now and follow them from here on."""
        handlers = self._handlers()
        if not handlers:
            return
        self._worker = create_task(self._run())
        waiting = []
        for entity_id, handler in handlers.items():
            if entity_id in self._cfg.radon_entities:
                # Known from the options: watched from now on, like a meter the
                # bridge knows from before a restart.
                self._nc._meter_note_known(entity_id, False)  # noqa: SLF001
            state = self._hass.states.get(entity_id)
            if _gone(state):
                # Right after a start of Home Assistant most entities are not
                # there yet. Only one still missing after a while counts as lost.
                waiting.append(entity_id)
            else:
                self._queue.put_nowait((handler, entity_id, state))

        @callback
        def changed(event: Event) -> None:
            entity_id = event.data["entity_id"]
            self._queue.put_nowait(
                (handlers[entity_id], entity_id, event.data.get("new_state"))
            )

        ids = list(handlers)
        self._unsub.append(async_track_state_change_event(self._hass, ids, changed))
        # A value written again unchanged is a sign of life too.
        self._unsub.append(async_track_state_report_event(self._hass, ids, changed))

        if waiting:

            @callback
            def still_gone(_now) -> None:
                for entity_id in waiting:
                    if entity_id not in self._online and _gone(
                        self._hass.states.get(entity_id)
                    ):
                        self._queue.put_nowait((handlers[entity_id], entity_id, None))

            self._unsub.append(
                async_call_later(self._hass, STARTUP_GRACE_S, still_gone)
            )

    @callback
    def async_stop(self) -> None:
        """Stop following the entities."""
        for unsub in self._unsub:
            unsub()
        self._unsub.clear()
        if self._worker is not None:
            self._worker.cancel()
            self._worker = None

    async def _run(self) -> None:
        """Hand the states over one after the other, in the order they arrived."""
        while True:
            handler, entity_id, state = await self._queue.get()
            try:
                await handler(entity_id, state)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                LOGGER.exception(
                    "NeuraCell-X: the state of %s could not be evaluated", entity_id
                )

    def _connection(self, entity_id: str, state: State | None) -> bool | None:
        """Return True/False when the entity came back or was lost, else None."""
        was = self._online.get(entity_id)
        now = not _gone(state)
        if was is now:
            return None
        self._online[entity_id] = now
        return now

    async def _radon_value(self, entity_id: str, state: State | None) -> None:
        nc = self._nc
        change = self._connection(entity_id, state)
        if change is not None:
            await nc.on_radon_meter_availability(
                entity_id, "online" if change else "offline"
            )
        if _gone(state) or state.state == STATE_UNKNOWN:
            return
        await nc.on_radon_value(state.state, source=entity_id)

    async def _radon_alarm(self, entity_id: str, state: State | None) -> None:
        if _gone(state) or state.state == STATE_UNKNOWN:
            return  # the alarm stays as it is
        await self._nc.on_radon_alarm(state.state)

    async def _dewpoint_block(self, entity_id: str, state: State | None) -> None:
        nc = self._nc
        change = self._connection(entity_id, state)
        if change is not None:
            await nc.on_dewpoint_availability("online" if change else "offline")
        if _gone(state) or state.state == STATE_UNKNOWN:
            return
        await nc.on_dewpoint_block(state.state)

    async def _dewpoint_controller(self, entity_id: str, state: State | None) -> None:
        """Take the state of the Taupunktsteuerung: "on" while it ventilates, "off" = block."""
        nc = self._nc
        change = self._connection(entity_id, state)
        if change is not None:
            await nc.on_dewpoint_availability(
                "online" if change else "offline", native=True
            )
        if _gone(state) or state.state == STATE_UNKNOWN:
            return
        nc._tps_native_alive()  # noqa: SLF001
        await nc._apply_dewpoint_block(state.state != STATE_ON, state.state, False)  # noqa: SLF001

    def _dewpoint_sensor(self, which: str) -> Callable:
        async def handle(_entity_id: str, state: State | None) -> None:
            if _gone(state) or state.state == STATE_UNKNOWN:
                return
            await self._nc.on_dewpoint_sensor(which, state.state)

        return handle


def neuracell_device_info(entry_id: str) -> dict:
    """Return the device the NeuraCell-X entities belong to."""
    return {
        "identifiers": {(DOMAIN, f"neuracell_x_{entry_id}")},
        "name": "NeuraCell-X",
        "manufacturer": "SUEDWIND",
        "model": "NeuraCell-X AI Neural Control System",
    }


def started_recently(controller: NeuraCellXController) -> bool:
    """Return True while the start-up grace of the controller runs."""
    return time.monotonic() - controller._started < STARTUP_GRACE_S  # noqa: SLF001
