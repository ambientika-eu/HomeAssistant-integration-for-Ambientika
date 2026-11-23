"""NeuraCell-X: radon protection and dew-point control.

Radon protection has priority: while a radon alarm is active every unit runs in
Intake (fresh-air overpressure). A dew-point block switches the selected units
Off while ventilating would bring moisture in. When neither is active any more,
every unit gets back the mode it had before.

The controller below is the one of the Ambientika MQTT Bridge (bridge.py,
class NeuraCellXController), taken over line by line so that both behave the
same and can be compared. Where the bridge speaks of an MQTT topic of a radon
meter or of the Taupunktsteuerung, this integration passes the entity ID of the
Home Assistant entity that delivers the value; "retained" stands for a value
that was not reported live. Only the storage and the logger differ. The file is
built by a script from bridge.py and not edited by hand.

This module has no Home Assistant imports.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from typing import Any, Optional

from ambientika_py import FanSpeed, HumidityLevel, OperatingMode

from .compat import COMPAT_ENUM_MEMBERS, is_compat_member
from .const import LOGGER as log

# "Zuluftmodus" (supply air / fresh-air overpressure) == OperatingMode.Intake.
RADON_PROTECTION_MODE = OperatingMode.Intake
# Dew-point "do not ventilate" == switch the unit off.
DEWPOINT_BLOCK_MODE = OperatingMode.Off
# Fallback ventilation: the dew-point block holds the units Off, but neither the
# Taupunktsteuerung nor the radon meter is connected - nobody watches the room.
DEWPOINT_FALLBACK_MODE = OperatingMode.ManualHeatRecovery
# The 20-minute timed extraction: an action with an end, never a baseline.
TIMED_MODE = OperatingMode.TimedExpulsion
# Baselines kept over a restart are ignored when older than this (seconds), and
# their time stamp is refreshed this often while protection is active.
NEURACELL_STATE_MAX_AGE = 7 * 24 * 3600
NEURACELL_STATE_REFRESH_S = 12 * 3600
# After a start, kept baselines are not restored during this time, so the
# radon and dew-point inputs can report first (seconds).
STARTUP_GRACE_S = 90.0
# After a restore the cloud may report the protection mode for a short while.
RESTORE_STALE_WINDOW_S = 120.0
# A radon value counts for this long (seconds).
RADON_VALUE_MAX_AGE_S = 1800.0
# The Ambientika radon meter reports 0 right after it starts; ignored for this long.
RADON_METER_BOOT_IGNORE_S = 180.0
RADON_METER_FORGET_S = 7 * 24 * 3600.0
RADON_METER_LIST_REFRESH_S = 3600.0


def _truthy(value: str) -> bool:
    """Interpret a free-form MQTT payload as a boolean."""
    return value.strip().lower() in ("on", "true", "1", "yes", "alarm", "active", "block", "blocked")


def _to_float(raw: str) -> Optional[float]:
    try:
        return float(str(raw).strip().replace(",", "."))
    except (TypeError, ValueError):
        return None


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _finite(v) -> Optional[float]:
    try:
        f = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return f if math.isfinite(f) else None


def _payload_number(raw: str, key: str = "", require_key: bool = False) -> Optional[float]:
    """Finite number from an MQTT payload: plain ("62"), JSON number, or a JSON
    object ({"mittelwert": 62}) - by `key`, or (unless require_key) its only
    numeric value. NaN/Infinity and absurd values are rejected (None)."""
    v = _to_float(raw)
    if v is not None:
        return v if math.isfinite(v) else None
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if _is_num(obj):
        return None if require_key else _finite(obj)
    if isinstance(obj, dict):
        if key and key in obj:
            x = obj[key]
            if _is_num(x):
                return _finite(x)
            if isinstance(x, str):
                f = _to_float(x)
                return f if f is not None and math.isfinite(f) else None
            return None
        if require_key:
            return None
        nums = [x for x in obj.values() if _is_num(x)]
        if len(nums) == 1:
            return _finite(nums[0])
    return None


def _payload_truthy(raw: str, key: str = "") -> bool:
    """Boolean from an MQTT payload: ON/OFF/true/1 ... or a JSON object
    ({"block": true}) - by `key`, or its only value."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return _truthy(raw)
    if isinstance(obj, dict):
        if key and key in obj:
            obj = obj[key]
        elif len(obj) == 1:
            obj = next(iter(obj.values()))
        else:
            return False
    if isinstance(obj, bool):
        return obj
    if _is_num(obj):
        return isinstance(obj, int) and obj == 1     # same rule as plain "1"
    if isinstance(obj, str):
        return _truthy(obj)
    return False


def _controller_ventilating(raw: str, key: str) -> Optional[bool]:
    """ventilating flag from a Taupunktsteuerung state message ({"ventilating": true, ...}).
    None = not a JSON object with that key, or no usable value."""
    try:
        obj = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(obj, dict) or key not in obj:
        return None
    v = obj[key]
    if isinstance(v, bool):
        return v
    if _is_num(v):
        return None if v not in (0, 1) else v == 1
    if isinstance(v, str):
        t = v.strip().lower()
        if t in ("true", "1", "on", "yes"):
            return True
        if t in ("false", "0", "off", "no"):
            return False
    return None


def dew_point_c(temp_c: float, rh_pct: float) -> float:
    """Dew point in °C from temperature (°C) and relative humidity (%) - Magnus formula."""
    a, b = 17.625, 243.04
    rh = min(max(rh_pct, 0.01), 100.0)
    gamma = math.log(rh / 100.0) + (a * temp_c) / (b + temp_c)
    return (b * gamma) / (a - gamma)


class NeuraCellXController:
    """Radon protection (priority) + dew-point control, with baseline restore.

    Desired override per device:
        radon_active     -> Intake / protection fan          (priority 1)
        dewpoint_block   -> Off (keep fan/humidity)          (priority 2)
        neither          -> restore pre-protection baseline
    """

    def __init__(self, bridge: "AmbientikaBridge", cfg: BridgeConfig) -> None:
        self.bridge = bridge
        self.cfg = cfg

        # Radon alarm sources are tracked separately and OR-combined, so a
        # value-based alarm (radon_topic / numeric meter field) and an explicit
        # alarm (radon_alarm_topic / text or bool meter field) can never switch
        # each other off. Either one keeps radon protection on (safety first).
        self._radon_value_alarm = False
        self._radon_signal_alarm = False
        self._radon_values: dict = {}    # source -> (value, monotonic time)
        self._meter_online: dict = {}    # meter state topic -> (online bool, monotonic time)
        # Ambientika radon meter connection watch (see BridgeConfig.radon_meter_timeout):
        self._meter_seen: dict = {}      # meter state topic -> monotonic time of its last live message
        self._meter_ref: dict = {}       # meter state topic -> monotonic start of the silence that counts
        self._meter_heard: dict = {}     # meter state topic -> monotonic time of its last message (forgetting)
        self._meter_known: dict = {}     # meter state topic -> epoch time last seen (retained list)
        self._meter_listed: dict = {}    # meter state topic -> epoch time written to that list
        self._meter_state_reported: dict = {}        # meter state topic -> last reported state
        self._meter_connected_reported: Optional[bool] = None
        self._broker_connected_at = time.monotonic()  # last (re)connect to the broker
        # Taupunktsteuerung connection watch (see BridgeConfig.dewpoint_*).
        self._tps_avail: Optional[bool] = None       # from its availability topic (LWT)
        self._tps_last_seen: Optional[float] = None  # last message / successful cloud poll
        self._tps_poll_failures = 0                  # dewpoint_source='device'
        self._tps_polled = False
        self._tps_connected_reported: Optional[bool] = None
        self._tps_watch_start = time.monotonic()
        self._tps_last_block: Optional[bool] = None  # last block decision, also while offline
        self._tps_native_seen: Optional[float] = None  # last live message on the controller's own topics
        self._tps_stored_online = False  # only a stored "online" since the last (re)connect
        # Fallback ventilation (dewpoint_fallback_timeout): nobody watches the room.
        self.dewpoint_fallback = False
        self._blind_since: Optional[float] = None   # monotonic: TPS and radon meter both lost
        self._fallback_changed = False              # reconcile() must force the new state
        self._fallback_from_disk = False            # resumed from the state file (startup grace)
        self.dewpoint_block = False
        self.last_radon: Optional[float] = None

        self.indoor_dew_point: Optional[float] = None
        self.outdoor_dew_point: Optional[float] = None
        self._dp_inputs: dict = {}  # indoor_t, indoor_rh, outdoor_t, outdoor_rh

        self._saved_modes: dict = {}  # serial -> {operating_mode, fan_speed, humidity_level}
        self._pending_manual: dict = {}  # serial -> partial baseline from manual cmds while offline
        self._from_disk: set = set()     # serials whose baseline was loaded from "the storage of Home Assistant"
        # serial -> (operating_mode, fan_speed) protection last applied to the unit;
        # persisted with the baseline so a restart can tell "still in protection"
        # from "changed by hand while the bridge was down".
        self._applied: dict = {}
        self._orphans: dict = {}         # persisted entries of units not discovered at this start
        self._unit_locks: dict = {}      # serial -> asyncio.Lock (per-unit serialisation)
        self._started = time.monotonic()
        self._persisted_at = 0.0
        # serial -> (baseline dict, monotonic time) of the last successful restore.
        # The cloud can report the old (protection) mode for a while after a
        # restore; a partial manual command must not copy that stale mode back.
        self._recent_restore: dict = {}
        self._timed_warned: set = set()  # serials whose TimedExpulsion baseline was named in the log
        self._persisted: Optional[str] = None
        self._persist_warned = False
        self._lock = asyncio.Lock()

    # ----- convenience -----
    @property
    def radon_active(self) -> bool:
        return self._radon_value_alarm or self._radon_signal_alarm

    @property
    def override_active(self) -> bool:
        return self.radon_active or self.dewpoint_block

    def _dewpoint_targets(self, serial: str, device: Any) -> bool:
        """Whether a dew-point block applies to this device.

        Empty selector list -> every device (legacy behaviour). Otherwise the
        device matches if its serial number or its name is in the list
        (case-insensitive).
        """
        tokens = self.cfg.dewpoint_block_device_tokens
        if not tokens:
            return True
        name = (getattr(device, "name", "") or "").strip().lower()
        return serial.strip().lower() in tokens or name in tokens

    def _device_under_control(self, serial: str, device: Any) -> bool:
        """Whether the currently active protection actually controls this device.

        Radon protection always applies to every unit (safety overpressure).
        A dew-point block may be limited to selected units.
        """
        if self.radon_active:
            return True
        if self.dewpoint_block:
            return self._dewpoint_targets(serial, device)
        return False

    # ----- radon signals -----
    def _effective_radon(self) -> Optional[float]:
        """Highest radon value that is still current across all sources."""
        now = time.monotonic()
        fresh = [v for v, t in self._radon_values.values() if now - t <= RADON_VALUE_MAX_AGE_S]
        return max(fresh) if fresh else None

    def _meter_topics(self) -> set:
        return set(self._meter_online) | set(self._meter_seen) | set(self._meter_known)

    def _meter_state(self, state_topic: str) -> Optional[bool]:
        """Connection state of one Ambientika radon meter.

        False as soon as its Last Will said offline, or when nothing (value or
        availability) arrived within radon_meter_timeout. The silence counts
        from the meter's last live message, from the moment it became known
        from the retained list, or from the last (re)connect to the broker if
        that is later (nothing can arrive while the bridge itself is cut off).
        True when it announced itself online or sent a value. None while
        nothing is known yet.
        """
        online = self._meter_online.get(state_topic, (None, 0.0))[0]
        if online is False:
            return False
        limit = self.cfg.radon_meter_timeout * 60
        ref = self._meter_ref.get(state_topic)
        if limit > 0 and ref is not None and time.monotonic() - ref > limit:
            return False
        if online is True or state_topic in self._meter_seen:
            return True
        return None

    def radon_meter_connected(self) -> Optional[bool]:
        """True if every known Ambientika radon meter is connected, False as soon
        as one is offline or silent for too long, None if no meter is known or
        one of them is not judged yet (nothing received, time limit not run out)."""
        topics = self._meter_topics()
        if not topics:
            return None
        if self.cfg.radon_meter_timeout > 0 and not self.bridge.mqtt_connected():
            # Cut off from the broker: nothing can arrive, the time limit must
            # not count. Keep the last known status.
            return self._meter_connected_reported
        states = [self._meter_state(t) for t in sorted(topics)]
        if any(st is False for st in states):
            return False
        if any(st is None for st in states):
            return None
        return True

    def _meter_note_known(self, state_topic: str, seen: bool) -> None:
        """Remember the meter (retained list). `seen` = a live message, i.e. a
        sign of life; a message the broker delivered from its store is not.
        The list on the broker is written when a meter is new and refreshed at
        most every RADON_METER_LIST_REFRESH_S while the meter keeps sending."""
        now, mono = time.time(), time.monotonic()
        new = state_topic not in self._meter_known
        if seen:
            self._meter_seen[state_topic] = mono
            self._meter_ref[state_topic] = mono
            self._meter_heard[state_topic] = mono
            self._meter_known[state_topic] = now
        else:
            self._meter_ref.setdefault(state_topic, mono)
            self._meter_heard.setdefault(state_topic, mono)
            if new:
                self._meter_known[state_topic] = now
        listed = self._meter_listed.get(state_topic)
        if new or (seen and (listed is None or now - listed > RADON_METER_LIST_REFRESH_S)):
            self.bridge.publish_known_meters()

    async def on_known_meters(self, raw: str) -> None:
        """Retained list of the radon meters seen before a restart (own topic).

        A meter from the list is watched from now on: if it sends nothing within
        radon_meter_timeout it counts as disconnected, exactly as if the bridge
        had never restarted. Meters not seen for RADON_METER_FORGET_S are dropped.
        """
        try:
            obj = json.loads(raw)
        except (TypeError, ValueError):
            return
        meters = obj.get("radon_meters") if isinstance(obj, dict) else None
        if not isinstance(meters, dict):
            return
        now, mono = time.time(), time.monotonic()
        added = []
        for topic, last in meters.items():
            if not isinstance(topic, str):
                continue
            if not self.bridge.nc_is_meter(topic):
                continue
            try:
                last = float(last)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(last):
                continue
            last = min(last, now)
            if now - last > RADON_METER_FORGET_S or topic in self._meter_known:
                continue
            self._meter_known[topic] = last
            self._meter_ref.setdefault(topic, mono)
            self._meter_heard.setdefault(topic, mono - (now - last))
            added.append(topic)
        if added:
            log.info("NeuraCell-X: radon meter(s) %s known from before the restart - %s.",
                     ", ".join(added),
                     ("counted as disconnected if nothing arrives within %d min" % self.cfg.radon_meter_timeout)
                     if self.cfg.radon_meter_timeout > 0 else "status unknown until a message arrives")
            self.bridge.publish_neuracell_state()

    def known_meters(self) -> dict:
        """Content of the retained list: meter state topic -> epoch time last seen."""
        now = time.time()
        return {t: round(ts, 1) for t, ts in sorted(self._meter_known.items())
                if now - ts <= RADON_METER_FORGET_S}

    def known_meters_payload(self) -> str:
        return json.dumps({"radon_meters": self.known_meters()}, sort_keys=True)

    def _forget_stale_meters(self) -> list:
        """Drop meters not heard from for RADON_METER_FORGET_S - judged by both
        clocks, so a clock jump at boot cannot drop a live meter."""
        now, mono = time.time(), time.monotonic()
        stale = [t for t, ts in self._meter_known.items()
                 if now - ts > RADON_METER_FORGET_S
                 and mono - self._meter_heard.get(t, mono) > RADON_METER_FORGET_S]
        for t in stale:
            for d in (self._meter_known, self._meter_listed, self._meter_seen, self._meter_ref,
                      self._meter_heard, self._meter_online, self._meter_state_reported,
                      self._radon_values):
                d.pop(t, None)
            log.info("NeuraCell-X: radon meter %s not heard from for %d days - no longer watched.",
                     t, int(RADON_METER_FORGET_S // 86400))
        if stale:
            self.bridge.publish_known_meters()
        return stale

    async def check_radon_meter_link(self, publish: bool = True) -> bool:
        """Judge every known meter and the overall status and log each change
        (called on every meter message and every poll, i.e. also when the time
        limit runs out without a message). Returns True if something changed."""
        if self.cfg.radon_meter_timeout > 0 and not self.bridge.mqtt_connected():
            return False   # cut off from the broker: nothing can arrive, nothing to judge
        changed = bool(self._forget_stale_meters())
        for t in sorted(self._meter_topics()):
            st = self._meter_state(t)
            was = self._meter_state_reported.get(t)
            if st == was:
                continue
            self._meter_state_reported[t] = st
            changed = True
            if st is False:
                self._radon_values.pop(t, None)
                if self._meter_online.get(t, (None, 0.0))[0] is False:
                    log.warning("NeuraCell-X: radon meter %s offline (Last Will) - its last value is "
                                "not used for new decisions (current protection state is kept).", t)
                else:
                    log.warning("NeuraCell-X: radon meter %s sent nothing for %d min - counted as "
                                "disconnected, its last value is not used for new decisions "
                                "(current protection state is kept).", t, self.cfg.radon_meter_timeout)
            elif st is True:
                log.info("NeuraCell-X: radon meter %s connected%s.", t, " again" if was is False else "")
        now = self.radon_meter_connected()
        if now != self._meter_connected_reported:
            self._meter_connected_reported = now
            changed = True
        if changed:
            self._refresh_fallback()
        if changed and publish:
            self.bridge.publish_neuracell_state()
        return changed

    async def on_radon_meter_availability(self, state_topic: str, raw: str,
                                          retained: bool = False) -> None:
        """Availability of an Ambientika radon meter (radon/<id>/availability/state).

        `retained` = delivered from the broker's store on subscribe (after a
        bridge restart or reconnect). A stored "offline" is taken as it is; a
        stored "online" says nothing about the meter being alive now, so the
        meter only counts as known and has to send something within the time
        limit.
        """
        online = self._parse_availability(raw)
        if online is None:
            log.warning("NeuraCell-X: unrecognised radon meter availability %r on %s - ignored.",
                        raw, state_topic)
            return
        if retained and online:
            new = state_topic not in self._meter_topics()
            self._meter_note_known(state_topic, False)
            if new:
                log.info("NeuraCell-X: radon meter %s was online before the restart - waiting for "
                         "a message.", state_topic)
        else:
            self._meter_online[state_topic] = (online, time.monotonic())
            # "online" comes from the meter itself (sign of life); "offline" is
            # the broker publishing its Last Will.
            self._meter_note_known(state_topic, online and not retained)
        await self.check_radon_meter_link(publish=False)
        self.bridge.publish_neuracell_state()
        await self._settle_fallback()

    async def on_radon_value(self, raw: str, source: str = "value", retained: bool = False) -> None:
        if not self.cfg.neuracell_enabled:
            return
        is_meter = source not in ("value", "device")
        v = _payload_number(raw, self.cfg.radon_value_key, require_key=is_meter)
        if v is None:
            log.warning("NeuraCell-X: could not parse radon value %r%s", raw,
                        (" (expected key %r)" % self.cfg.radon_value_key) if is_meter else "")
            return
        if is_meter:
            if retained:
                # From the broker's store (subscribe after a restart): the meter
                # exists, but the value says nothing about it being alive now.
                self._meter_note_known(source, False)
            else:
                # A live value is a sign of life: the meter is there, whatever its
                # Last Will said before (e.g. a stale "offline").
                self._meter_note_known(source, True)
                if self._meter_online.get(source, (None, 0.0))[0] is not True:
                    self._meter_online[source] = (True, time.monotonic())
            await self.check_radon_meter_link(publish=False)
            if retained and self._meter_state(source) is False:
                log.info("NeuraCell-X: stored radon value %r of disconnected meter %s ignored.", raw, source)
                self.bridge.publish_neuracell_state()
                return
            if retained and v == 0:
                log.info("NeuraCell-X: stored start-up value 0 of radon meter %s ignored.", source)
                self.bridge.publish_neuracell_state()
                return
        if v == 0:
            st = self._meter_online.get(source)
            if st is not None and st[0] and time.monotonic() - st[1] < RADON_METER_BOOT_IGNORE_S:
                log.info("NeuraCell-X: radon meter %s just started (0 Bq/m3, no measurement yet) - ignored.",
                         source)
                self.bridge.publish_neuracell_state()
                return
        self._radon_values[source] = (v, time.monotonic())
        value = self._effective_radon()
        if value is None:
            value = v
        self.last_radon = value
        before = self.radon_active
        if not self._radon_value_alarm and value >= self.cfg.radon_threshold:
            log.warning("NeuraCell-X: radon %.0f >= %d Bq/m3 -> radon protection ON.",
                        value, self.cfg.radon_threshold)
            self._radon_value_alarm = True
        elif self._radon_value_alarm and value <= (self.cfg.radon_threshold - self.cfg.radon_hysteresis):
            if self._radon_signal_alarm:
                log.warning("NeuraCell-X: radon %.0f Bq/m3 back to safe, but explicit radon alarm "
                            "still active -> radon protection stays ON.", value)
            else:
                log.warning("NeuraCell-X: radon %.0f Bq/m3 back to safe -> radon protection OFF.", value)
            self._radon_value_alarm = False
        if self.radon_active != before:
            await self.reconcile(force=True)
        else:
            # A current value is what ends the fallback ventilation (see
            # _refresh_fallback) - act on it now, not only with the next poll.
            self._refresh_fallback()
            if self._fallback_changed:
                await self.reconcile()
            else:
                self.bridge.publish_neuracell_state()

    async def on_radon_alarm(self, raw: str) -> None:
        if not self.cfg.neuracell_enabled:
            return
        on = _payload_truthy(raw)
        await self._set_radon_signal_alarm(on, "explicit radon alarm %s." % ("ON" if on else "OFF"))

    async def _set_radon_signal_alarm(self, on: bool, msg: str) -> None:
        """Set the explicit (non-numeric) radon alarm source and reconcile on change."""
        if on == self._radon_signal_alarm:
            self.bridge.publish_neuracell_state()
            return
        before = self.radon_active
        self._radon_signal_alarm = on
        if not on and self._radon_value_alarm:
            msg += " Radon value still above threshold -> radon protection stays ON."
        log.warning("NeuraCell-X: %s", msg)
        if self.radon_active != before:
            await self.reconcile(force=True)
        else:
            self.bridge.publish_neuracell_state()

    async def poll_radon_device(self, device: Any) -> None:
        """Derive the radon alarm from a radon meter's cloud status (source='device').

        Reads one status field of the radon meter via the Ambientika API and
        decides whether radon protection should be active. Numeric fields go
        through the same threshold/hysteresis path as radon_topic; boolean
        fields are used directly; string/enum fields (e.g. air_quality) count as
        an alarm when their value is in cfg.radon_device_alarm_value_set. No hardware.
        """
        if not self.cfg.neuracell_enabled:
            return
        status = await self.bridge.read_status(device)
        if status is None:
            log.warning("NeuraCell-X: radon source device %s unreachable this poll; keeping last state.",
                        getattr(device, "serial_number", "?"))
            return
        field = self.cfg.radon_device_alarm_field
        raw = status.get(field)
        if raw is None:
            log.warning("NeuraCell-X: radon source device %s has no status field %r; keeping last state. "
                        "Available fields: %s",
                        getattr(device, "serial_number", "?"), field,
                        ", ".join(sorted(status.keys())))
            return
        # bool must be checked before int (bool is a subclass of int).
        if isinstance(raw, bool):
            on = raw
        elif isinstance(raw, (int, float)):
            # Numeric field -> reuse the threshold/hysteresis path (does reconcile).
            await self.on_radon_value(str(raw), source="device")
            return
        else:
            value = getattr(raw, "name", raw)   # enum -> its name, else the value itself
            on = str(value).strip().lower() in self.cfg.radon_device_alarm_value_set
        log.debug("NeuraCell-X: radon meter %s %s=%r -> %s",
                  getattr(device, "serial_number", "?"), field, raw, "ALARM" if on else "clear")
        await self._set_radon_signal_alarm(
            on, "radon meter %s -> radon protection %s." % (getattr(device, "serial_number", "?"),
                                                          "ON" if on else "OFF"))

    # ----- Taupunktsteuerung connection watch -----
    def dewpoint_controller_connected(self) -> Optional[bool]:
        """True = TPS connected, False = disconnected, None = not watched / unknown yet."""
        cfg = self.cfg
        if not cfg.dewpoint_enabled or cfg.dewpoint_source == "computed":
            return None
        if cfg.dewpoint_source == "device":
            if not self._tps_polled:
                return None
            return self._tps_poll_failures < max(1, cfg.availability_failure_threshold)
        if self._tps_avail is False:
            return False
        if self._tps_avail is True:
            # The broker publishes the Last Will when the TPS stops answering
            # its keepalive, so a known "online" is current; the time limit only
            # covers controllers without a Last Will.
            return True
        if (self._tps_stored_online and self._tps_native_seen is None
                and cfg.dewpoint_controller_timeout > 0):
            # Known only from the broker's store after a restart / reconnect: a
            # controller that went away while the broker was down has no Last
            # Will coming, so it gets the time limit of the radon meter.
            if not self.bridge.mqtt_connected():
                return self._tps_connected_reported
            if time.monotonic() - self._tps_watch_start > cfg.dewpoint_controller_timeout * 60:
                return False
        if cfg.dewpoint_signal_timeout > 0:
            if not self.bridge.mqtt_connected():
                # The bridge itself is cut off from the broker: nothing can arrive,
                # so the time limit must not count. Keep the last known status.
                return self._tps_connected_reported
            ref = max(x for x in (self._tps_last_seen, self._tps_watch_start) if x is not None)
            if time.monotonic() - ref > cfg.dewpoint_signal_timeout * 60:
                return False
            if self._tps_last_seen is not None:
                return True
        if self._tps_native_seen is not None:
            # A live message on the controller's own topics (state / sensors) is
            # a sign of life even without a time limit - its Last Will reports
            # the next hang.
            return True
        return None

    def on_broker_connected(self) -> None:
        """Called from the MQTT on_connect callback (paho thread), also on reconnects.

        A controller or radon meter that was still counted as connected (or not
        judged yet) gets a full time limit after the bridge is back on the
        broker; one already lost stays lost until it sends again.
        """
        now = time.monotonic()
        self._broker_connected_at = now
        if self._tps_connected_reported is not False:
            self._tps_watch_start = now
        for t in list(self._meter_ref):
            if self._meter_state_reported.get(t) is not False:
                self._meter_ref[t] = now

    @staticmethod
    def _parse_availability(raw: str) -> Optional[bool]:
        """online/offline, true/false, 1/0, on/off, connected/disconnected or a
        JSON object with "state"/"status"/"availability". None = not recognised."""
        val: Any = raw.strip()
        try:
            obj = json.loads(val)
        except (TypeError, ValueError):
            obj = val
        if isinstance(obj, dict):
            for k in ("state", "status", "availability", "online"):
                if k in obj:
                    obj = obj[k]
                    break
        if isinstance(obj, bool):
            return obj
        if _is_num(obj) and obj in (0, 1):
            return obj == 1
        t = str(obj).strip().lower()
        if t in ("online", "true", "1", "on", "connected", "available", "up"):
            return True
        if t in ("offline", "false", "0", "off", "disconnected", "unavailable", "down", "lost"):
            return False
        return None

    def _tps_seen(self) -> None:
        self._tps_last_seen = time.monotonic()

    async def on_dewpoint_availability(self, raw: str, retained: bool = False,
                                       native: bool = False) -> None:
        """Availability of the Taupunktsteuerung (its Last Will topic).

        `retained` = delivered from the broker's store on subscribe. A stored
        "offline" is taken as it is; a stored "online" says nothing about the
        controller being alive now - the status stays unknown until it sends.
        """
        if not self.cfg.dewpoint_enabled:
            return
        avail = self._parse_availability(raw)
        if avail is None:
            log.warning("NeuraCell-X: unrecognised Taupunktsteuerung availability %r - ignored.", raw)
            return
        if retained and avail:
            if self._tps_avail is False:
                # Its Last Will said offline while we were listening; a stored
                # "online" cannot be newer than that. Stays lost until it sends.
                await self.check_dewpoint_link()
                self.bridge.publish_neuracell_state()
                return
            if self._tps_native_seen is not None and self._tps_native_seen <= self._broker_connected_at:
                # A sign of life from before the (re)connect says nothing about now.
                self._tps_native_seen = None
            if self._tps_avail is None and self._tps_native_seen is None and self._tps_last_seen is None:
                log.info("NeuraCell-X: Taupunktsteuerung was online before the restart - waiting for "
                         "a message.")
            elif self._tps_connected_reported is True:
                log.info("NeuraCell-X: Taupunktsteuerung reported online by the broker's store after "
                         "the reconnect - waiting for a message.")
            self._tps_avail = None
            # Time limit after a restart only for the Ambientika controller
            # (its sensor messages count as sign of life); a third-party one
            # may only send on change.
            self._tps_stored_online = native
            await self.check_dewpoint_link()
            self.bridge.publish_neuracell_state()
            await self._settle_fallback()
            return
        was_connected = self._tps_connected_reported
        self._tps_avail = avail
        self._tps_stored_online = False
        self._tps_seen()
        await self.check_dewpoint_link()
        await self._tps_back(was_connected)
        await self._settle_fallback()

    async def _tps_back(self, was_connected: Optional[bool]) -> None:
        """Back in contact after a Last Will "offline" or an expired time limit:
        with release/block its last decision counts again - its state is
        retained / only sent on change, so the value that arrived while it was
        lost is the current one."""
        if (was_connected is False and self._tps_connected_reported is True
                and self.cfg.dewpoint_lost_action != "keep" and self._tps_last_block is not None):
            await self._set_dewpoint_block(self._tps_last_block)

    async def check_dewpoint_link(self) -> None:
        """Log and act on a change of the TPS connection (called on messages and every poll)."""
        now = self.dewpoint_controller_connected()
        prev = self._tps_connected_reported
        if now == prev:
            return
        self._tps_connected_reported = now
        if now is False:
            action = self.cfg.dewpoint_lost_action
            if (self._tps_stored_online and self._tps_native_seen is None
                    and self._tps_avail is None):
                log.warning("NeuraCell-X: Taupunktsteuerung sent nothing for %d min after the "
                            "restart / reconnect - counted as disconnected.",
                            self.cfg.dewpoint_controller_timeout)
            log.warning("NeuraCell-X: Taupunktsteuerung disconnected - dew-point block %s.",
                        {"release": "released (dewpoint_lost_action=release)",
                         "block": "set ON (dewpoint_lost_action=block)"}.get(
                            action, "kept as it is (%s)" % ("ON" if self.dewpoint_block else "OFF")))
            if action in ("release", "block"):
                await self._set_dewpoint_block(action == "block")   # reconciles, fallback included
                return
        elif now is True and prev is False:
            log.info("NeuraCell-X: Taupunktsteuerung connected again.")
        self._refresh_fallback()
        self.bridge.publish_neuracell_state()

    # ----- dew-point signals -----
    async def on_dewpoint_block(self, raw: str, retained: bool = False) -> None:
        """External ON/OFF block signal (source='signal').

        `retained` is the flag the broker sets on a message it delivers from its
        store (on subscribe); a live publish never carries it (MQTT 3.1.1 §3.3.1.3).
        """
        if not self.cfg.dewpoint_enabled:
            return
        await self._apply_dewpoint_block(_payload_truthy(raw, self.cfg.dewpoint_block_key), raw, retained)

    async def on_dewpoint_controller_state(self, raw: str, retained: bool = False) -> None:
        """State message of the Ambientika Taupunktsteuerung (dew-point/<id>/state):
        {"ventilating": true|false, "reason": ...}. ventilating false = block."""
        if not self.cfg.dewpoint_enabled:
            return
        ventilating = _controller_ventilating(raw, self.cfg.dewpoint_controller_key)
        if ventilating is None:
            log.warning("NeuraCell-X: Taupunktsteuerung state without usable %r - ignored: %r",
                        self.cfg.dewpoint_controller_key, raw)
            return
        if not retained:
            self._tps_native_alive()
        await self._apply_dewpoint_block(not ventilating, raw, retained)

    def _tps_native_alive(self) -> None:
        """A live message on the controller's own topics (state / sensors): only
        the controller publishes there, so it is alive - whatever its Last Will
        said before (e.g. a stale "offline" when its "online" was missed)."""
        self._tps_native_seen = time.monotonic()
        self._tps_stored_online = False
        if self._tps_avail is False:
            # Its stale "offline" no longer counts; "online" only when it says
            # so itself (a configured time limit stays in force meanwhile).
            self._tps_avail = None

    async def on_dewpoint_controller_seen(self) -> None:
        """Any other live message of the Taupunktsteuerung (sensor values) - a sign of life."""
        if not self.cfg.dewpoint_enabled:
            return
        was_connected = self._tps_connected_reported
        self._tps_native_alive()
        self._tps_seen()
        await self.check_dewpoint_link()
        await self._tps_back(was_connected)
        await self._settle_fallback()

    async def _apply_dewpoint_block(self, block: bool, raw: str, retained: bool) -> None:
        self._tps_last_block = block
        if retained:
            # From the broker's store (delivered on subscribe, i.e. after a bridge
            # restart or reconnect): says nothing about the controller being alive
            # now. While the controller counts as lost (Last Will or time limit),
            # such a value is stale and the lost action decides instead.
            if (self.dewpoint_controller_connected() is False
                    and self.cfg.dewpoint_lost_action != "keep"):
                log.info("NeuraCell-X: stored dew-point block %r ignored - Taupunktsteuerung "
                         "disconnected (dewpoint_lost_action=%s).", raw, self.cfg.dewpoint_lost_action)
                await self._set_dewpoint_block(self.cfg.dewpoint_lost_action == "block")
                return
        else:
            # A live message is a sign of life for the time limit. On the generic
            # block topic the Last Will stays authoritative: while it says
            # offline, the message is still applied (it may come from a Home
            # Assistant test), but the status only changes when the controller
            # announces itself online. A live message on the controller's own
            # topics has already marked it alive (_tps_native_alive).
            self._tps_seen()
            await self.check_dewpoint_link()
        await self._set_dewpoint_block(block)
        await self._settle_fallback()

    async def on_dewpoint_sensor(self, which: str, raw: str) -> None:
        """One of the four sensor inputs (source='computed')."""
        if not self.cfg.dewpoint_enabled:
            return
        val = _payload_number(raw)
        if val is None:
            log.warning("NeuraCell-X: could not parse dew-point sensor %s=%r", which, raw)
            return
        self._dp_inputs[which] = val
        needed = ("indoor_t", "indoor_rh", "outdoor_t", "outdoor_rh")
        if not all(k in self._dp_inputs for k in needed):
            return
        self.indoor_dew_point = dew_point_c(self._dp_inputs["indoor_t"], self._dp_inputs["indoor_rh"])
        self.outdoor_dew_point = dew_point_c(self._dp_inputs["outdoor_t"], self._dp_inputs["outdoor_rh"])
        # Ventilating brings outdoor air in. If the outdoor dew point is at/above
        # (indoor dew point - margin), ventilating would add moisture -> block.
        margin = self.cfg.dewpoint_margin
        hyst = self.cfg.dewpoint_hysteresis
        if not self.dewpoint_block and self.outdoor_dew_point >= (self.indoor_dew_point - margin):
            await self._set_dewpoint_block(True)
        elif self.dewpoint_block and self.outdoor_dew_point <= (self.indoor_dew_point - margin - hyst):
            await self._set_dewpoint_block(False)
        else:
            self.bridge.publish_neuracell_state()

    async def _set_dewpoint_block(self, block: bool) -> None:
        if block == self.dewpoint_block:
            self.bridge.publish_neuracell_state()
            return
        self.dewpoint_block = block
        log.warning("NeuraCell-X: dew-point ventilation %s.",
                    "BLOCKED (fans off)" if block else "released")
        await self.reconcile(force=True)

    async def poll_dewpoint_device(self, device: Any) -> None:
        """Derive the dew-point block from a TPS device's cloud status (source='device').

        Reads the TPS's operating mode via the Ambientika API and blocks when it
        is one of cfg.dewpoint_device_block_mode_set (default: Off). No hardware.
        """
        if not self.cfg.dewpoint_enabled:
            return
        status = await self.bridge.read_status(device)
        self._tps_polled = True
        if status is None:
            self._tps_poll_failures += 1
            log.warning("NeuraCell-X: dew-point source device %s unreachable this poll; keeping last state.",
                        getattr(device, "serial_number", "?"))
            await self.check_dewpoint_link()
            return
        self._tps_poll_failures = 0
        self._tps_seen()
        await self.check_dewpoint_link()
        mode = status["operating_mode"]
        block = mode in self.cfg.dewpoint_device_block_mode_set
        log.debug("NeuraCell-X: TPS %s operating_mode=%s -> %s",
                  getattr(device, "serial_number", "?"), mode.name, "block" if block else "clear")
        await self._set_dewpoint_block(block)

    # ----- desired state -----
    def _desired(self, status: dict):
        """Return (operating_mode, fan_speed, humidity_level) or None to restore."""
        if self.radon_active:
            return (RADON_PROTECTION_MODE, self.cfg.radon_protection_fan_speed,
                    self._api_value(HumidityLevel, status["humidity_level"], HumidityLevel.Normal))
        if self.dewpoint_block:
            if self.dewpoint_fallback:
                return (DEWPOINT_FALLBACK_MODE, self.cfg.dewpoint_fallback_fan_speed,
                        self._api_value(HumidityLevel, status["humidity_level"], HumidityLevel.Normal))
            return (DEWPOINT_BLOCK_MODE,
                    self._api_value(FanSpeed, status["fan_speed"], FanSpeed.Low),
                    self._api_value(HumidityLevel, status["humidity_level"], HumidityLevel.Normal))
        return None

    # ----- reconciliation -----
    def unit_lock(self, serial: str) -> asyncio.Lock:
        """Per-unit lock: a protection change, a restore and a manual command for
        the SAME unit never interleave; different units never wait on each other."""
        lk = self._unit_locks.get(serial)
        if lk is None:
            lk = self._unit_locks[serial] = asyncio.Lock()
        return lk

    @staticmethod
    def _api_value(enum_cls, value, fallback):
        """Compatibility enum members (e.g. FanSpeed 'Night') are read-only: the
        API does not accept them back, so never put them into a command."""
        name = getattr(value, "name", None)
        if name is not None and name in COMPAT_ENUM_MEMBERS.get(enum_cls, ()):
            return fallback
        return value

    def _baseline_mode(self, serial: str, status: dict):
        """The operating mode a unit gets back once protection clears.

        Normally the mode it reports now. TIMED_MODE (TimedExpulsion) is the
        exception: the unit is in its 20-minute timed extraction and will return
        by itself to the mode it reports as last_operating_mode. Saving
        TimedExpulsion would restore an action that has long since ended -
        another 20 minutes of full extraction when protection clears, after
        which the unit falls back to the mode it was in just before, which by
        then is the protection mode itself (Off or Intake). So the baseline is
        the mode the extraction returns to. If the unit reports no usable one
        (missing, TimedExpulsion again, or a read-only value the API would not
        accept back), the old behaviour stays and the log says so.
        """
        mode = status["operating_mode"]
        if mode != TIMED_MODE:
            return mode
        last = status.get("last_operating_mode")
        if last is None or last == TIMED_MODE or is_compat_member(OperatingMode, last):
            log.warning("NeuraCell-X: %s is in its timed extraction and reports no usable "
                        "previous mode (%s) - saving TimedExpulsion as its baseline after all.",
                        serial, getattr(last, "name", last))
            self._timed_warned.discard(serial)     # the restore of this baseline warns once
            return mode
        log.info("NeuraCell-X: %s is in its timed extraction - saving the mode it returns "
                 "to (%s) as its baseline, not TimedExpulsion.", serial, last.name)
        return last

    def _still_protected(self, serial: str, status: dict, device: Any = None) -> bool:
        """Is the unit still in the protection state we put it in (restart check)?"""
        applied = self._applied.get(serial)
        if applied is not None and device is not None and self._is_coupled_slave(device):
            # A coupled Slave's own mode field does not follow the command (its
            # Master decides), so it cannot show a change made meanwhile.
            return True
        if applied is not None:
            return (status["operating_mode"] == applied[0]
                    and (applied[0] != RADON_PROTECTION_MODE or status["fan_speed"] == applied[1]))
        return status["operating_mode"] in (RADON_PROTECTION_MODE, DEWPOINT_BLOCK_MODE)

    async def _restore_one(self, serial: str, device: Any) -> bool:
        """Give one unit its saved baseline back. True when the baseline is done.

        Retry-safe: the baseline is only dropped after the restore succeeded AND
        nobody changed it meanwhile; otherwise it is kept for the next poll.
        Caller holds unit_lock(serial).
        """
        saved = self._saved_modes.get(serial)
        if saved is None:
            return True
        if serial in self._from_disk:
            # Baseline survived a restart. Give the protection inputs (radon meter,
            # TPS) a moment to report before undoing protection, and only restore
            # if the unit is still in the state protection put it in - if someone
            # changed it meanwhile (app, wall panel), their choice wins.
            if time.monotonic() - self._started < STARTUP_GRACE_S:
                return False
            status = await self.bridge.read_status(device)
            if status is None:
                return False
            if not self._still_protected(serial, status, device):
                log.info("NeuraCell-X: %s was changed while the bridge was down; "
                         "dropping its saved baseline.", serial)
                self._saved_modes.pop(serial, None)
                self._applied.pop(serial, None)
                self._from_disk.discard(serial)
                return True
        snapshot = dict(saved)
        applied = self._applied.get(serial)
        if snapshot["operating_mode"] == TIMED_MODE and serial not in self._timed_warned:
            # The unit was in its timed extraction when protection started and no
            # usable previous mode was known (see _baseline_mode), or the state
            # file was written before 1.4.37. The extraction ends by itself after
            # 20 minutes; the mode the unit falls back to then is its own.
            self._timed_warned.add(serial)
            log.warning("NeuraCell-X: the saved baseline of %s is the timed extraction "
                        "(TimedExpulsion - no usable previous mode was known) - restoring it; "
                        "the unit ends it after 20 minutes. Check the mode it settles on.", serial)
        ok = await self.bridge.set_device_mode(
            device, snapshot["operating_mode"], snapshot["fan_speed"], snapshot["humidity_level"])
        if not ok:
            return False
        self._recent_restore[serial] = (snapshot, time.monotonic(),
                                        applied[0] if applied else None)
        if self._saved_modes.get(serial) == snapshot:
            del self._saved_modes[serial]
            self._applied.pop(serial, None)
            self._from_disk.discard(serial)
            self._timed_warned.discard(serial)
            return True
        return False    # changed during the restore -> apply the newer baseline next poll

    async def _apply_pending_manual(self, serial: str, device: Any) -> None:
        """Apply a manual command that was deferred while the unit was protected
        but unreachable, once the unit is no longer under protection control.
        Caller holds unit_lock(serial)."""
        pm = self._pending_manual.get(serial)
        if not pm:
            self._pending_manual.pop(serial, None)
            return
        status = await self.bridge.read_status(device)
        if status is None:
            return
        snapshot = dict(pm)
        ok = await self.bridge.set_device_mode(
            device,
            snapshot.get("operating_mode", status["operating_mode"]),
            snapshot.get("fan_speed", self._api_value(FanSpeed, status["fan_speed"], FanSpeed.Low)),
            snapshot.get("humidity_level", self._api_value(HumidityLevel, status["humidity_level"],
                                                           HumidityLevel.Normal)))
        if ok and self._pending_manual.get(serial) == snapshot:
            del self._pending_manual[serial]
            log.info("NeuraCell-X: deferred manual change applied to %s.", serial)

    async def _protect_one(self, serial: str, device: Any, force: bool) -> None:
        """Put one controlled unit into the protection state. Caller holds its lock."""
        status = await self.bridge.read_status(device)
        if status is None:
            log.warning("NeuraCell-X: %s unreachable; will retry on next poll.", serial)
            return
        desired = self._desired(status)
        if desired is None:
            return
        # Capture the pre-protection baseline exactly once per device,
        # honouring any manual change made while it was overridden.
        # Only devices we actually control get a baseline, so a
        # targeted dew-point block never disturbs the other units.
        if serial not in self._saved_modes:
            base = {
                "operating_mode": self._baseline_mode(serial, status),
                "fan_speed": self._api_value(FanSpeed, status["fan_speed"], FanSpeed.Low),
                "humidity_level": self._api_value(HumidityLevel, status["humidity_level"],
                                                  HumidityLevel.Normal),
            }
            base.update(self._pending_manual.pop(serial, {}))
            self._saved_modes[serial] = base
        mode, fan, hum = desired
        if (self._applied.get(serial) == (mode, fan)
                and (self._is_coupled_slave(device)
                     or (status["operating_mode"] == mode and status["fan_speed"] == fan))):
            # The unit has already received exactly this protection state and
            # (for a Master) still reports it: nothing to send, not even on a
            # forced reconcile - e.g. when the dew-point block toggles while
            # radon protection holds every unit in Intake anyway. A coupled
            # Slave runs with its zone Master and keeps reporting its own mode
            # field, which a command does not change, so for it the record of
            # the last command counts; sending it again on every poll would
            # only repeat the command (and the unit's confirmation beep). Its
            # Master is checked against its status as before.
            return
        if (force or status["operating_mode"] != mode
                or status["fan_speed"] != fan
                or status["humidity_level"] != hum):
            if await self.bridge.set_device_mode(device, mode, fan, hum):
                self._applied[serial] = (mode, fan)
        else:
            self._applied[serial] = (mode, fan)

    def _is_coupled_slave(self, device: Any) -> bool:
        """True for a unit that currently runs as a Slave with a zone Master."""
        try:
            return self.bridge._zone_master(device) is not None
        except Exception:
            return False

    # ----- fallback ventilation: nobody watches the room -----
    def _refresh_fallback(self) -> None:
        """Keep `dewpoint_fallback` in line with the inputs. Changes flags only
        (no I/O); reconcile() acts on them, the link checks call it so the
        published status is current.

        Armed while the dew-point block holds its units Off, radon protection is
        not active, and BOTH the Taupunktsteuerung and the radon meter count as
        disconnected: no dew-point decision can come in and no radon value - the
        room is unwatched. After dewpoint_fallback_timeout the fallback starts:
        the blocked units run heat recovery at dewpoint_fallback_fan. It ends on
        a positive sign only - the Taupunktsteuerung is connected again or the
        radon meter delivers a current value again (a stored "online" after a
        restart is not enough), the block is released, radon protection takes
        over, or the option is switched off.
        """
        cfg = self.cfg
        limit = cfg.dewpoint_fallback_timeout * 60
        tps = self.dewpoint_controller_connected()
        meter = self.radon_meter_connected()
        if self.dewpoint_fallback:
            reason = None
            if limit <= 0:
                reason = "dewpoint_fallback_timeout is 0"
            elif not self.dewpoint_block:
                if self._fallback_from_disk and time.monotonic() - self._started < STARTUP_GRACE_S:
                    # Resumed after a restart: the stored block message of the
                    # Taupunktsteuerung may not have been delivered yet.
                    return
                reason = "dew-point block released"
            elif self.radon_active:
                reason = "radon protection has priority"
            elif tps is True:
                reason = "Taupunktsteuerung connected again"
            elif meter is True and self._effective_radon() is not None:
                # Connected alone is not enough: a meter that has just started
                # sends its first usable value about 10 minutes later. Until
                # then nobody measures radon - the ventilation stays.
                reason = "radon meter reporting again"
            if reason is None:
                return
            self.dewpoint_fallback = False
            self._blind_since = None
            self._fallback_changed = True
            self._fallback_from_disk = False
            log.warning("NeuraCell-X: fallback ventilation ended - %s%s.", reason,
                        " - the dew-point block (units Off) applies again"
                        if self.dewpoint_block and not self.radon_active else "")
            return
        blind = (limit > 0 and self.dewpoint_block and not self.radon_active
                 and tps is False and meter is False)
        if not blind:
            if self._blind_since is not None:
                self._blind_since = None
                why = ("dew-point block released" if not self.dewpoint_block
                       else "radon protection" if self.radon_active
                       else "Taupunktsteuerung reporting again" if tps is not False
                       else "radon meter reporting again")
                log.info("NeuraCell-X: fallback ventilation no longer pending (%s) - nothing was "
                         "changed.", why)
            return
        now = time.monotonic()
        if self._blind_since is None:
            self._blind_since = now
            log.warning("NeuraCell-X: Taupunktsteuerung and radon meter both disconnected while the "
                        "dew-point block holds the units Off - fallback ventilation (heat recovery %s) "
                        "in %d min unless one of them reports.",
                        cfg.dewpoint_fallback_fan, cfg.dewpoint_fallback_timeout)
            return
        if now - self._blind_since >= limit:
            self.dewpoint_fallback = True
            self._fallback_changed = True
            log.warning("NeuraCell-X: fallback ventilation - Taupunktsteuerung and radon meter "
                        "disconnected for %d min while the dew-point block holds the units Off: the "
                        "blocked units run heat recovery at %s until one of them reports again.",
                        cfg.dewpoint_fallback_timeout, cfg.dewpoint_fallback_fan)

    async def _settle_fallback(self) -> None:
        """Act on a fallback change noticed by a link check outside the poll
        path (message of the Taupunktsteuerung or the radon meter), so the units
        follow at once and not only with the next poll."""
        if self._fallback_changed:
            await self.reconcile()

    async def reconcile(self, force: bool = False) -> None:
        """Bring every device in line with the current protection state.

        Serialised with a lock so signal-triggered and poll-triggered
        reconciles can never interleave and corrupt the saved baseline; each
        unit is additionally handled under its own unit_lock, which manual
        commands for that unit take as well.
        """
        async with self._lock:
            self._refresh_fallback()
            if self._fallback_changed:
                # The protection state of the blocked units changed (Off <->
                # heat recovery): send it to every controlled unit once.
                self._fallback_changed = False
                force = True
            if self.override_active:
                for serial, device in list(self.bridge.devices.items()):
                    async with self.unit_lock(serial):
                        # Only touch devices the active protection actually controls.
                        # Radon protects every unit; a dew-point block can be limited
                        # to selected units (cfg.dewpoint_block_devices).
                        if self._device_under_control(serial, device):
                            await self._protect_one(serial, device, force)
                        elif serial in self._saved_modes:
                            # Not (or no longer) controlled by the active protection.
                            # Typical case: radon cleared while the dew-point block
                            # still holds the OFFICE units - the other units were in
                            # radon Intake and must get their own baseline back now,
                            # not only once the dew-point block is released too.
                            if await self._restore_one(serial, device):
                                log.info("NeuraCell-X: %s released from protection -> restored.", serial)
                        elif serial in self._pending_manual:
                            await self._apply_pending_manual(serial, device)
            elif self._saved_modes or self._pending_manual:
                # All protections cleared: restore each device, retry-safe.
                # Keep a device's baseline until its restore actually succeeds,
                # so an offline device is not left stuck in protection mode.
                had_saved = bool(self._saved_modes)
                for serial in list(self._saved_modes.keys()):
                    device = self.bridge.devices.get(serial)
                    if device is None:
                        self._saved_modes.pop(serial, None)
                        self._applied.pop(serial, None)
                        self._from_disk.discard(serial)
                        continue
                    async with self.unit_lock(serial):
                        await self._restore_one(serial, device)
                for serial in list(self._pending_manual.keys()):
                    if serial in self._saved_modes:
                        continue
                    device = self.bridge.devices.get(serial)
                    if device is None:
                        self._pending_manual.pop(serial, None)
                        continue
                    async with self.unit_lock(serial):
                        await self._apply_pending_manual(serial, device)
                if had_saved and not self._saved_modes:
                    log.warning("NeuraCell-X: all protections cleared -> devices restored.")
            self.bridge.publish_neuracell_state()

    async def enforce(self) -> None:
        """Called every poll: re-assert an active override, or finish a pending restore."""
        if self.override_active or self._saved_modes or self._pending_manual:
            await self.reconcile(force=False)

    # ----- persistence of baselines (survives add-on restart / update) -----
    # Only the baselines are persisted (plus which protection was applied). The
    # protection inputs themselves (radon value, TPS block) are NOT: they are
    # re-reported by the meter/TPS, and a stale persisted alarm could otherwise
    # hold units in protection forever. Deferred manual commands of unreachable
    # units are not persisted either (they could be outdated after a restart).
    @staticmethod
    def _enum_by_name(enum_cls, name):
        return dict.get(enum_cls._member_map_, name) if isinstance(name, str) else None

    def _serialise_state(self) -> str:
        def enc(d):
            return {k: getattr(v, "name", v) for k, v in d.items()}
        saved = {s: o["saved"] for s, o in self._orphans.items()}
        applied = {s: o["applied"] for s, o in self._orphans.items() if isinstance(o.get("applied"), dict)}
        saved.update({s: enc(v) for s, v in self._saved_modes.items()})
        applied.update({s: {"operating_mode": v[0].name, "fan_speed": v[1].name}
                        for s, v in self._applied.items() if s in self._saved_modes})
        state = {"saved_modes": saved, "applied": applied}
        if self.dewpoint_fallback and self._saved_modes:
            # Fallback ventilation in force: resume it after a restart instead of
            # switching the units Off again until both time limits have run out.
            # Cleared like any other state by a positive sign (see _refresh_fallback).
            state["dewpoint_fallback"] = True
        if self._orphans:
            state["orphans_since"] = {s: o.get("since", 0) for s, o in self._orphans.items()}
        return json.dumps(state, sort_keys=True)

    def persist(self) -> None:
        """Write baselines to disk when they changed (and refresh the timestamp
        twice a day while protection is active). Never raises."""
        if not (self.cfg.neuracell_enabled or self.cfg.dewpoint_enabled):
            return
        try:
            data = self._serialise_state()
        except Exception as e:     # pragma: no cover - defensive
            log.debug("NeuraCell-X: could not serialise state: %s", e)
            return
        now = time.time()
        if data == self._persisted and (not self._saved_modes
                                        or now - self._persisted_at < NEURACELL_STATE_REFRESH_S):
            return
        if self._persisted is None and not self._saved_modes and not self._orphans:
            self._persisted = data     # nothing to remember yet - no need to create the file
            return
        try:
            payload = json.loads(data)
            payload["ts"] = now
            self.bridge.nc_save(payload)
            self._persisted = data
            self._persisted_at = now
        except Exception as e:
            if not self._persist_warned:
                self._persist_warned = True
                log.warning("NeuraCell-X: could not persist baselines to %s (%s). "
                            "Protection works, but a restart during active protection "
                            "cannot restore the previous modes.", "the storage of Home Assistant", e)

    def load_persisted(self) -> None:
        """Load baselines saved before a restart (only for known devices). Never raises."""
        try:
            self._load_persisted()
        except Exception as e:
            log.warning("NeuraCell-X: ignoring unreadable %s: %s", "the storage of Home Assistant", e)
            self._saved_modes.clear()
            self._applied.clear()
            self._from_disk.clear()
            self._orphans.clear()
            self.dewpoint_fallback = False
            self._fallback_from_disk = False
            self._persisted = ""       # force a clean rewrite on the next change
        self._started = time.monotonic()

    def _load_persisted(self) -> None:
        raw = self.bridge.nc_load()
        if raw is None:
            return
        if not isinstance(raw, dict):
            raise ValueError("unexpected content")
        try:
            age = time.time() - float(raw.get("ts", 0))
        except (TypeError, ValueError):
            age = NEURACELL_STATE_MAX_AGE + 1
        if age > NEURACELL_STATE_MAX_AGE:
            log.info("NeuraCell-X: saved baselines are older than 7 days - ignored.")
            self._persisted = ""       # overwrite the outdated file on the next change
            self.persist()
            return
        saved = raw.get("saved_modes") or {}
        applied = raw.get("applied") or {}
        orphan_since = raw.get("orphans_since") or {}
        if not isinstance(saved, dict) or not isinstance(applied, dict) or not isinstance(orphan_since, dict):
            raise ValueError("unexpected content")
        skipped = 0
        for serial, d in saved.items():
            if not isinstance(d, dict):
                skipped += 1
                continue
            if serial not in self.bridge.devices:
                # Unit not discovered this time (e.g. a cloud hiccup): keep its entry
                # in the file so a later restart can still restore it - but not for
                # more than NEURACELL_STATE_MAX_AGE (unit removed from the account).
                try:
                    since = float(orphan_since.get(serial, raw.get("ts", 0)))
                except (TypeError, ValueError):
                    since = 0.0
                if time.time() - since <= NEURACELL_STATE_MAX_AGE:
                    self._orphans[serial] = {"saved": d, "applied": applied.get(serial), "since": since}
                else:
                    skipped += 1
                continue
            op = self._enum_by_name(OperatingMode, d.get("operating_mode"))
            if op is None:
                skipped += 1
                continue
            fan = self._enum_by_name(FanSpeed, d.get("fan_speed"))
            hum = self._enum_by_name(HumidityLevel, d.get("humidity_level"))
            self._saved_modes[serial] = {
                "operating_mode": op,
                "fan_speed": self._api_value(FanSpeed, fan if fan is not None else FanSpeed.Low,
                                             FanSpeed.Low),
                "humidity_level": self._api_value(HumidityLevel,
                                                  hum if hum is not None else HumidityLevel.Normal,
                                                  HumidityLevel.Normal),
            }
            self._from_disk.add(serial)
            a = applied.get(serial)
            if isinstance(a, dict):
                aop = self._enum_by_name(OperatingMode, a.get("operating_mode"))
                afan = self._enum_by_name(FanSpeed, a.get("fan_speed"))
                if aop is not None and afan is not None:
                    self._applied[serial] = (aop, afan)
        if raw.get("dewpoint_fallback") is True and self._saved_modes:
            self.dewpoint_fallback = True
            self._fallback_from_disk = True
        self._persisted = "" if skipped else self._serialise_state()
        if skipped:
            self.persist()             # drop entries that no longer apply from the file
        if self._saved_modes:
            log.warning("NeuraCell-X: resumed %d saved baseline(s) from before the restart: %s%s",
                        len(self._saved_modes), ", ".join(sorted(self._saved_modes)),
                        " - fallback ventilation was in force and is kept until the Taupunktsteuerung "
                        "or the radon meter reports again" if self.dewpoint_fallback else "")
