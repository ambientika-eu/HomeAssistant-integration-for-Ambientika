"""The hub: the sign-in and one shared reading of the status of every unit.

All entities of a unit show parts of the same status packet. The hub reads that
packet once per unit and interval and hands it to the entities, instead of
every entity asking the cloud for itself.

References:
 - https://developers.home-assistant.io/docs/integration_fetching_data/#coordinated-single-api-poll-for-data-for-all-entities

"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta
import math
import time
from typing import Any

from ambientika_py import (
    Device,
    DeviceStatus,
    FanSpeed,
    HumidityLevel,
    LightSensorLevel,
    OperatingMode,
)
from returns.result import Failure, Success

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from . import const
from .compat import is_compat_member
from .logic import Measurements, filter_status_to_num, raw_number
from .neuracell import (
    DEWPOINT_BLOCK_MODE,
    RADON_PROTECTION_MODE,
    RESTORE_STALE_WINDOW_S,
    TIMED_MODE,
    NeuraCellXController,
)
from .neuracell_ha import NeuraCellConfig, NeuraCellHost, NeuraCellInputs

from .api import (
    AmbientikaApiClient,
    AmbientikaApiClientAuthenticationError,
    AmbientikaApiClientError,
    status_code,
)
from .const import (
    CONF_COMMAND_COALESCE_MS,
    CONF_FAILURE_THRESHOLD,
    CONF_FILTER_ACK_TTL_DAYS,
    CONF_POLL_INTERVAL,
    CONF_SLAVE_FILTER_SOFT_RESET,
    DEFAULT_OPTIONS,
    DOMAIN,
    LOGGER,
    REQUEST_TIMEOUT,
    STATUS_REQUESTS_AT_ONCE,
)

# What NeuraCell-X changes on a unit and gives back afterwards.
BASELINE_VALUES = ("operating_mode", "fan_speed", "humidity_level")
# The values a command can set, with the enumeration each one belongs to.
COMMAND_ENUMS = {
    "operating_mode": OperatingMode,
    "fan_speed": FanSpeed,
    "humidity_level": HumidityLevel,
    "light_sensor_level": LightSensorLevel,
}

# HTTP status codes with which the cloud turns down an expired sign-in.
SIGN_IN_EXPIRED = (401, 403)
# Seconds until the integration may sign in again: after it has just done so,
# after the cloud could not be reached, and after the cloud did not accept user
# name and password.
SIGN_IN_INTERVAL = 60
SIGN_IN_RETRY = 60
SIGN_IN_RETRY_REJECTED = 1800
# Seconds after which the units that have answered are shown, without waiting
# for a unit whose answer is still out.
SLOW_ANSWER = 3

# One reading of a unit: serial number, status (None without one), why there is
# no status, and when the request was sent.
Reading = tuple[str, DeviceStatus | None, str | None, float]


def _bounded(value: Any, low: float, high: float, default: float) -> float:
    """Return an option as a number within its limits."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return min(high, max(low, number))


def _utc(timestamp: float | None) -> datetime | None:
    """Return a unix time as a time Home Assistant shows in local time."""
    if not timestamp:
        return None
    try:
        return datetime.fromtimestamp(float(timestamp), UTC).replace(microsecond=0)
    except (OverflowError, OSError, ValueError):
        return None


def describe_failure(error: Any) -> str:
    """Say in a few words why the cloud gave no status."""
    if isinstance(error, dict):
        code, data = error.get("status_code"), error.get("data")
        if isinstance(data, dict):
            data = data.get("message")
        text = data.strip()[:120] if isinstance(data, str) and data.strip() else ""
        if isinstance(code, int):
            return f"HTTP status {code}: {text}" if text else f"HTTP status {code}"
        if text:
            return text
    return repr(error)[:200]


class AmbientikaHub(DataUpdateCoordinator[dict[str, DeviceStatus | None]]):
    """The connection to the Ambientika cloud and the status of all units."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the hub to manage all devices and the API facade."""
        self._credentials = {
            "username": entry.data.get(CONF_USERNAME, ""),
            "password": entry.data.get(CONF_PASSWORD, ""),
        }
        self.options: dict[str, Any] = {**DEFAULT_OPTIONS, **entry.options}
        self.poll_interval = _bounded(self.options[CONF_POLL_INTERVAL], 10, 300, 30)
        self.failure_threshold = int(
            _bounded(self.options[CONF_FAILURE_THRESHOLD], 1, 10, 3)
        )
        self.coalesce_s = (
            _bounded(self.options[CONF_COMMAND_COALESCE_MS], 0, 10000, 800) / 1000.0
        )
        self.soft_reset = bool(self.options[CONF_SLAVE_FILTER_SOFT_RESET])
        self.filter_ack_ttl_s = (
            _bounded(self.options[CONF_FILTER_ACK_TTL_DAYS], 1, 3650, 90) * 86400.0
        )
        self.measurements = Measurements(LOGGER, self.poll_interval)
        self.started = time.time()
        self.devices: list[Device] = []
        # serial number -> what the entities of the unit show (None without status)
        self._views: dict[str, dict[str, Any] | None] = {}
        # serial number -> failed readings in a row
        self._failures: dict[str, int] = {}
        # serial number -> diagnostics (last status packet, failed readings)
        self._diag: dict[str, dict[str, Any]] = {}
        # serial number -> light sensor level seen while the unit was running. A
        # unit that is switched off reports a default level.
        self._last_light: dict[str, Any] = {}
        # serial number -> {value name: the last value the cloud accepts back}
        self._last_sendable: dict[str, dict[str, Any]] = {}
        # serial number -> {value name: (value sent, when)} until the unit reports it
        self._sent: dict[str, dict[str, tuple[Any, float]]] = {}
        # serial number -> (operating mode sent, when), checked on the next readings
        self._mode_expect: dict[str, tuple[Any, float]] = {}
        # serial number -> (own operating mode, when read)
        self._last_mode: dict[str, tuple[Any, float]] = {}
        self._mode_note: dict[str, tuple[str, str]] = {}
        # serial number -> role the unit reported in its last status
        self._live_role: dict[str, str] = {}
        self._topology_logged: tuple | None = None
        # commands waiting to be sent in one call
        self._pending_cmds: dict[str, dict[str, Any]] = {}
        self._cmd_waiters: dict[str, asyncio.Future[None]] = {}
        self._unit_locks: dict[str, asyncio.Lock] = {}
        # filter reset
        self._reset_inflight: set[str] = set()
        self._reset_shown: dict[str, str] = {}
        self._reset_log: dict[str, dict[str, Any]] = {}
        self._ack_good_streak: dict[str, tuple[int, float]] = {}
        self._ack_odd_raw: dict[str, str] = {}
        self._store: Store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}")
        self._stored: dict[str, Any] = {"pending_resets": [], "filter_ack": {}}
        # NeuraCell-X: radon protection and dew-point control
        self.nc_cfg = NeuraCellConfig(self.options, self.failure_threshold)
        self.neuracell = NeuraCellXController(
            NeuraCellHost(self, self.nc_cfg), self.nc_cfg
        )
        self.nc_inputs = NeuraCellInputs(hass, self.neuracell, self.nc_cfg)
        # Radon meter and Taupunktsteuerung read from the cloud (source "device"):
        # only read, never shown or controlled as ventilation units.
        self.radon_device: Device | None = None
        self.dewpoint_device: Device | None = None
        self._sign_in_lock = asyncio.Lock()
        self._sign_in_not_before = 0.0
        self._sign_in_failing = False
        # serial number -> the status shown (None while there is none)
        self._statuses: dict[str, DeviceStatus | None] = {}
        # serial number -> when the request for the status shown was sent
        self._read_at: dict[str, float] = {}
        # serial numbers of the units the log has reported as without status
        self._silent: set[str] = set()
        # serial number -> the reading under way that was asked for by name
        self._unit_readings: dict[str, asyncio.Task[None]] = {}
        self._status_requests = asyncio.Semaphore(STATUS_REQUESTS_AT_ONCE)

        super().__init__(
            hass,
            LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(seconds=self.poll_interval),
        )

    async def login(self) -> None:
        """Async loading of the devices."""

        self.client = AmbientikaApiClient(
            username=self._credentials["username"],
            password=self._credentials["password"],
        )
        self.devices = await self.client.async_get_data()
        self._take_out_source_devices()
        self.measurements.configure(
            self.options, {d.serial_number: d.name for d in self.devices}
        )

    def _take_out_source_devices(self) -> None:
        """Take radon meter and Taupunktsteuerung out of the ventilation units."""
        cfg = self.nc_cfg
        for enabled, source, serial, name in (
            (
                cfg.dewpoint_enabled,
                cfg.dewpoint_source,
                cfg.dewpoint_device_serial,
                "dewpoint",
            ),
            (cfg.neuracell_enabled, cfg.radon_source, cfg.radon_device_serial, "radon"),
        ):
            found = None
            if enabled and source == "device" and serial:
                found = next(
                    (
                        d
                        for d in self.devices
                        if d.serial_number.lower() == serial.lower()
                    ),
                    None,
                )
                if found is None:
                    LOGGER.error(
                        "NeuraCell-X: %s_device_serial %r not found. Serial numbers "
                        "of this account: %s",
                        name,
                        serial,
                        ", ".join(d.serial_number for d in self.devices) or "(none)",
                    )
                else:
                    self.devices = [d for d in self.devices if d is not found]
                    LOGGER.info(
                        "NeuraCell-X: %s source %s (%s) is only read, not shown as a "
                        "ventilation unit",
                        name,
                        found.name,
                        found.serial_number,
                    )
            setattr(self, f"{name}_device", found)

    @property
    def stored(self) -> dict[str, Any]:
        """What the integration keeps over a restart."""
        return self._stored

    def save_store(self) -> None:
        """Write what the integration keeps over a restart."""
        self._save_store()

    def device(self, serial_number: str) -> Device | None:
        """Return the unit with this serial number."""
        return next((d for d in self.devices if d.serial_number == serial_number), None)

    def view(self, serial_number: str) -> dict[str, Any] | None:
        """Return what the entities of a unit show, None while it has no status."""
        return self._views.get(serial_number)

    def status(self, serial_number: str) -> DeviceStatus | None:
        """Return the status of a unit as it was last read, None without one."""
        return self._statuses.get(serial_number)

    async def async_request(
        self, device: Device, request: Callable[[], Awaitable[Any]]
    ) -> Any:
        """Send a request for a unit to the cloud.

        The sign-in of the Ambientika cloud expires after a while. A request the
        cloud turns down for that reason is repeated once after signing in
        again; without that every unit would stay unavailable until the
        integration is reloaded.
        """
        token = device.api.token
        result = await self._async_send(request)
        if status_code(result) not in SIGN_IN_EXPIRED:
            return result
        if not await self._async_sign_in_again(device, token):
            return result
        return await self._async_send(request)

    @staticmethod
    async def _async_send(request: Callable[[], Awaitable[Any]]) -> Any:
        """Send one request and wait a limited time for the answer.

        A request the cloud accepts and never answers would otherwise hold up
        the reading of all units for minutes.
        """
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                return await request()
        except TimeoutError:
            return Failure(
                {
                    "status_code": None,
                    "data": f"no answer from the Ambientika cloud within {REQUEST_TIMEOUT} seconds",
                }
            )

    async def _async_sign_in_again(self, device: Device, used_token: str) -> bool:
        """Sign in again and hand the new sign-in to all units."""
        async with self._sign_in_lock:
            if device.api.token != used_token:
                # Another request has signed in again in the meantime.
                return True
            if time.monotonic() < self._sign_in_not_before:
                return False
            try:
                fresh = await self.client.async_get_data()
                token = fresh[0].api.token if fresh else None
            except AmbientikaApiClientError as error:
                rejected = isinstance(error, AmbientikaApiClientAuthenticationError)
                self._sign_in_not_before = time.monotonic() + (
                    SIGN_IN_RETRY_REJECTED if rejected else SIGN_IN_RETRY
                )
                if not self._sign_in_failing or rejected:
                    LOGGER.warning(
                        "The sign-in to the Ambientika cloud has expired and "
                        "signing in again failed: %s",
                        error,
                    )
                self._sign_in_failing = True
                return False
            if not token:
                self._sign_in_not_before = time.monotonic() + SIGN_IN_RETRY
                return False
            for known in self.devices:
                known.api.token = token
            device.api.token = token
            self._sign_in_not_before = time.monotonic() + SIGN_IN_INTERVAL
            self._sign_in_failing = False
            LOGGER.info(
                "The sign-in to the Ambientika cloud had expired, signed in again"
            )
            return True

    async def _async_read(self, device: Device) -> Reading:
        """Ask the cloud for the status of one unit."""
        sent = time.monotonic()
        try:
            result = await self.async_request(device, device.status)
        except Exception as error:  # noqa: BLE001
            # No connection, or a status packet this version cannot read. It
            # must never stop the reading of the other units.
            return device.serial_number, None, f"{type(error).__name__}: {error}", sent
        match result:
            case Success(data):
                return device.serial_number, data, None, sent
            case Failure(error):
                return device.serial_number, None, describe_failure(error), sent
        return device.serial_number, None, "unexpected answer", sent

    @callback
    def _async_take(self, readings: Iterable[Reading]) -> None:
        """Keep the readings and say in the log which units stopped or resumed answering.

        A unit keeps its last values until availability_failure_threshold
        readings in a row have failed: the cloud now and then answers a single
        request with "Status packet not found" for a unit that is reachable.
        """
        names = {device.serial_number: device.name for device in self.devices}
        by_serial = {device.serial_number: device for device in self.devices}
        stopped: list[tuple[str, str]] = []
        resumed: list[str] = []
        # Masters first, so a Slave is shown with its Master's mode of this reading.
        ordered = sorted(
            readings,
            key=lambda r: (
                r[0] in by_serial and self._role_of(by_serial[r[0]]) != "master"
            ),
        )
        for serial, status, problem, sent in ordered:
            if sent < self._read_at.get(serial, 0.0):
                # The status shown comes from a request sent later than this one.
                continue
            self._read_at[serial] = sent
            device = by_serial.get(serial)
            if status is not None:
                failed = self._failures.pop(serial, 0)
                self._diag_success(serial, failed)
                self._statuses[serial] = status
                if device is not None:
                    try:
                        self._views[serial] = self._view(device, status)
                    except Exception as error:  # noqa: BLE001
                        LOGGER.warning(
                            "The status of the unit %s (%s) could not be evaluated: %s",
                            names.get(serial, "?"),
                            serial,
                            error,
                        )
                        self._views[serial] = None
                        self._statuses[serial] = None
                if serial in self._silent and self._statuses[serial] is not None:
                    self._silent.discard(serial)
                    resumed.append(serial)
                continue
            count = self._failures.get(serial, 0) + 1
            self._failures[serial] = count
            self._diag_failure(serial, count, problem or "no status")
            if count < self.failure_threshold and self._statuses.get(serial):
                LOGGER.info(
                    "Reading %d of %d failed for the unit %s (%s): %s. It keeps its "
                    "last values",
                    count,
                    self.failure_threshold,
                    names.get(serial, "?"),
                    serial,
                    problem or "no status",
                )
                continue
            self._statuses[serial] = None
            self._views[serial] = None
            if serial not in self._silent:
                self._silent.add(serial)
                stopped.append((serial, problem or "no status"))

        # One line when the cloud as a whole does not answer, instead of one per unit.
        everything = len(self.devices) > 1
        if everything and len(stopped) == len(self.devices):
            reasons = sorted({problem for _, problem in stopped})
            LOGGER.warning(
                "No status from any of the %d units (%s). Their entities are "
                "unavailable until the Ambientika cloud answers again",
                len(stopped),
                "; ".join(reasons)[:300],
            )
        else:
            for serial, problem in stopped:
                LOGGER.warning(
                    "No status from the unit %s (%s): %s. Its entities are "
                    "unavailable until it answers again",
                    names.get(serial, "?"),
                    serial,
                    problem,
                )
        if everything and len(resumed) == len(self.devices):
            LOGGER.info("All %d units answer again", len(resumed))
        else:
            for serial in resumed:
                LOGGER.info(
                    "The unit %s (%s) answers again", names.get(serial, "?"), serial
                )

    async def _async_update_data(self) -> dict[str, DeviceStatus | None]:
        """Read the status of every unit: one request per unit."""

        async def read(device: Device) -> Reading:
            async with self._status_requests:
                return await self._async_read(device)

        if not self.devices:
            return {}
        readings = [
            self.hass.async_create_task(
                read(device), f"{DOMAIN} status of {device.serial_number}"
            )
            for device in self.devices
        ]
        try:
            done, late = await asyncio.wait(readings, timeout=SLOW_ANSWER)
            if late:
                # A unit whose answer is still out must not hold back the
                # values of the units that have answered.
                if done:
                    self._async_take([r.result() for r in readings if r in done])
                    self.data = dict(self._statuses)
                    self.async_update_listeners()
                await asyncio.wait(late)
                self._async_take([r.result() for r in readings if r in late])
            else:
                self._async_take([r.result() for r in readings])
        except asyncio.CancelledError:
            for reading in readings:
                reading.cancel()
            raise
        try:
            self._log_topology()
        except Exception as error:  # noqa: BLE001
            LOGGER.debug("zone topology not logged: %s", error)
        await self._async_neuracell_cycle()
        return dict(self._statuses)

    async def async_refresh_unit(self, device: Device, *, join: bool = True) -> None:
        """Read the status of one unit now and show it.

        With join, a reading of that unit that was asked for in the same way and
        is still under way is shared: an update of all entities of a unit
        (service homeassistant.update_entity) asks the cloud once, not once per
        entity. After a command the status is always read anew.
        """
        if not join:
            await self._async_refresh_unit(device)
            return
        serial = device.serial_number
        reading = self._unit_readings.get(serial)
        if reading is None:
            reading = self.hass.async_create_task(
                self._async_refresh_unit(device), f"{DOMAIN} status of {serial}"
            )
            self._unit_readings[serial] = reading

            def finished(done: asyncio.Task[None]) -> None:
                if self._unit_readings.get(serial) is done:
                    del self._unit_readings[serial]

            reading.add_done_callback(finished)
        await asyncio.shield(reading)

    async def _async_refresh_unit(self, device: Device) -> None:
        self._async_take([await self._async_read(device)])
        self.data = dict(self._statuses)
        self.async_update_listeners()

    async def _async_read_now(self, device: Device) -> DeviceStatus | None:
        """Read the status of one unit now, show it and return it."""
        reading = await self._async_read(device)
        self._async_take([reading])
        self.data = dict(self._statuses)
        self.async_update_listeners()
        return reading[1]

    # ----- what the entities of a unit show -----
    def _view(self, device: Device, status: DeviceStatus) -> dict[str, Any]:
        """Work out what is shown for a unit from its status.

        Filter status and operating mode come as pairs: the main value is the
        effective one, the unit's own value sits next to it as "raw".
        """
        serial = device.serial_number
        now = time.monotonic()
        own = status["operating_mode"]
        role = status.get("device_role")
        if role is not None:
            self._live_role[serial] = str(getattr(role, "name", role))
        light = status["light_sensor_level"]
        if own != OperatingMode.Off and not is_compat_member(LightSensorLevel, light):
            self._last_light[serial] = light
        self._remember_sendable(serial, status)
        self._note_reported(serial, status, now)
        try:
            self._check_mode_applied(device, own, now)
        except Exception as error:  # noqa: BLE001
            LOGGER.debug("mode check for %s skipped: %s", serial, error)
        self._last_mode[serial] = (own, now)
        shown = self._shown_mode(device, own, now)
        filter_raw = status["filters_status"]
        filter_shown = self._filter_ack_effective(serial, filter_raw)
        ack = self._filter_ack_record(serial)
        fresh = self.measurements.is_fresh(serial, status, now)
        temperature, temperature_avg = self.measurements.measured(
            serial, "temperature", status["temperature"], fresh, now
        )
        humidity, humidity_avg = self.measurements.measured(
            serial, "humidity", status["humidity"], fresh, now
        )
        self._sync_reset_state_with_ack(serial, ack is not None)
        return {
            "operating_mode": shown,
            "operating_mode_raw": own,
            "fan_speed": status["fan_speed"],
            "humidity_level": status["humidity_level"],
            "light_sensor_level": light,
            "temperature": temperature,
            "humidity": humidity,
            "temperature_avg": temperature_avg,
            "humidity_avg": humidity_avg,
            "humidity_raw": raw_number(status["humidity"]),
            "air_quality": status["air_quality"],
            "humidity_alarm": status["humidity_alarm"],
            "filters_status": filter_shown,
            "filters_status_raw": filter_raw,
            "filter_ack_since": _utc(ack["acked_at"]) if ack else None,
            "filter_ack_until": _utc(ack["valid_until"]) if ack else None,
            "night_alarm": status["night_alarm"],
            "device_role": status.get("device_role"),
            "last_operating_mode": status["last_operating_mode"],
            "zone_index": getattr(device, "zone_index", None),
        }

    # ----- roles and zones -----
    def _role_of(self, device: Device) -> str:
        """Return the current role in lower case: the unit's own report, else the list's."""
        live = self._live_role.get(device.serial_number)
        role = live if live is not None else (getattr(device, "role", "") or "")
        return str(role).lower()

    def zone_master(self, device: Device) -> Device | None:
        """Return the Master of the zone of a Slave, None for every other unit.

        A coupled group has one Master and one or more Slaves that share a zone
        index within one house.
        """
        if not self._role_of(device).startswith("slave"):
            return None
        zone = getattr(device, "zone_index", None)
        if zone is None:
            return None
        house = getattr(device, "house_id", None)
        for other in self.devices:
            if (
                other.serial_number != device.serial_number
                and getattr(other, "zone_index", None) == zone
                and getattr(other, "house_id", None) == house
                and self._role_of(other) == "master"
            ):
                return other
        return None

    def _shown_mode(self, device: Device, own: Any, now: float) -> Any:
        """Return the operating mode to show: the zone Master's for a Slave.

        A Slave runs with its Master, like in the Ambientika app. Its own value
        is shown when the Master has not been read for a while.
        """
        master = self.zone_master(device)
        if master is None:
            return own
        try:
            if self.neuracell._device_under_control(device.serial_number, device):  # noqa: SLF001
                return own
        except Exception:  # noqa: BLE001
            return own
        record = self._last_mode.get(master.serial_number)
        max_age = max(const.MASTER_MODE_MAX_AGE_S, 2 * self.poll_interval + 60)
        if record is None or now - record[1] > max_age:
            return own
        shown = record[0]
        serial = device.serial_number
        note = (getattr(own, "name", str(own)), getattr(shown, "name", str(shown)))
        if note[0] != note[1] and self._mode_note.get(serial) != note:
            self._mode_note[serial] = note
            LOGGER.info(
                "Operating mode of %s: the unit reports %s, shown as %s - it is a "
                "Slave and runs with its zone Master %s",
                serial,
                note[0],
                note[1],
                master.serial_number,
            )
        return shown

    @staticmethod
    def _unit_label(device: Device) -> str:
        return f"{getattr(device, 'name', None) or '?'} ({device.serial_number})"

    def _log_topology(self) -> None:
        """Log Master and Slaves of every zone once, and again when they change."""
        if not self.devices:
            return
        groups: dict = {}
        for device in self.devices:
            key = (
                getattr(device, "house_id", None),
                getattr(device, "zone_index", None),
            )
            masters, slaves, others = groups.setdefault(key, ([], [], []))
            role = self._role_of(device)
            shown = (
                self._live_role.get(device.serial_number)
                or getattr(device, "role", None)
                or "no role"
            )
            if role == "master":
                masters.append(device)
            elif role.startswith("slave"):
                slaves.append((device, str(shown)))
            else:
                others.append((device, str(shown)))
        signature = tuple(
            sorted(
                (
                    str(house),
                    str(zone),
                    tuple(sorted(d.serial_number for d in masters)),
                    tuple(sorted((d.serial_number, r.lower()) for d, r in slaves)),
                    tuple(sorted((d.serial_number, r.lower()) for d, r in others)),
                )
                for (house, zone), (masters, slaves, others) in groups.items()
            )
        )
        if signature == self._topology_logged:
            return
        head = (
            "Zone topology"
            if self._topology_logged is None
            else "Zone topology changed"
        )
        self._topology_logged = signature
        many_houses = len({house for house, _ in groups}) > 1
        for (house, zone), (masters, slaves, others) in sorted(
            groups.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        ):
            where = f"zone {zone}" if zone is not None else "no zone"
            if many_houses:
                where += f" (house {house})"
            parts = []
            if masters:
                parts.append(
                    "Master " + ", ".join(self._unit_label(d) for d in masters)
                )
            elif slaves:
                parts.append("no Master found")
            if slaves:
                parts.append(
                    "Slaves: "
                    + ", ".join(f"{self._unit_label(d)} {r}" for d, r in slaves)
                )
            elif masters and not others:
                parts.append("no Slaves (standalone)")
            if others:
                parts.append(
                    "not coupled: "
                    + ", ".join(f"{self._unit_label(d)} {r}" for d, r in others)
                )
            LOGGER.info("%s - %s: %s", head, where, "; ".join(parts))

    # ----- diagnostics: last status packet, failed readings -----
    def _diag_of(self, serial: str) -> dict[str, Any]:
        return self._diag.setdefault(
            serial,
            {
                "last_status": None,
                "total": 0,
                "consecutive": 0,
                "last_failure": None,
                "last_failure_reason": None,
                "outage_start": None,
                "outage_end": None,
                "outage_polls": None,
            },
        )

    def _diag_failure(self, serial: str, count: int, reason: str) -> None:
        diag = self._diag_of(serial)
        now = time.time()
        diag["total"] += 1
        diag["consecutive"] = count
        diag["last_failure"] = now
        diag["last_failure_reason"] = str(reason)[:200]
        if count == 1 or diag["outage_start"] is None or diag["outage_end"] is not None:
            diag["outage_start"] = now
            diag["outage_end"] = None
        diag["outage_polls"] = count

    def _diag_success(self, serial: str, failed_before: int) -> None:
        diag = self._diag_of(serial)
        now = time.time()
        diag["last_status"] = now
        if failed_before or diag["consecutive"]:
            diag["outage_end"] = now
            diag["outage_polls"] = max(
                int(failed_before or 0), int(diag["consecutive"])
            )
        diag["consecutive"] = 0

    def diagnostics(self, serial: str) -> dict[str, Any]:
        """Return the diagnostics of a unit; readable while the unit is unavailable."""
        diag = self._diag_of(serial)
        return {
            "last_status": _utc(diag["last_status"]),
            "poll_failures_total": int(diag["total"]),
            "consecutive_failures": int(diag["consecutive"]),
            "last_failure": _utc(diag["last_failure"]),
            "last_failure_reason": diag["last_failure_reason"],
            "last_outage_start": _utc(diag["outage_start"]),
            "last_outage_end": _utc(diag["outage_end"]),
            "last_outage_polls": diag["outage_polls"],
            "counting_since": _utc(self.started),
        }

    # ----- commands -----
    def _remember_sendable(self, serial: str, status: DeviceStatus) -> None:
        """Keep the last value of every command value that the cloud accepts back."""
        memory = self._last_sendable.setdefault(serial, {})
        for name, enum_cls in COMMAND_ENUMS.items():
            value = status.get(name)
            if value is None or is_compat_member(enum_cls, value):
                continue
            if (
                name == "light_sensor_level"
                and status.get("operating_mode") == OperatingMode.Off
            ):
                continue
            memory[name] = value

    def _note_reported(self, serial: str, status: DeviceStatus, now: float) -> None:
        """Forget values that were sent once the unit reports them."""
        sent = self._sent.get(serial)
        if not sent:
            return
        for name in list(sent):
            value, when = sent[name]
            if status.get(name) == value or now - when > const.MODE_VERIFY_WINDOW_S:
                del sent[name]

    @staticmethod
    def _nearest_sendable(name: str, value: Any) -> Any:
        """Return the replacement when no accepted value was ever seen."""
        if name == "fan_speed":
            reported = getattr(value, "name", "")
            if reported == "Night":
                return FanSpeed.Low
            return FanSpeed.High if reported == "Turbo" else FanSpeed.Medium
        if name == "humidity_level":
            return HumidityLevel.Normal
        if name == "light_sensor_level":
            return LightSensorLevel.Off
        return None  # an operating mode is never guessed

    def _sendable(self, serial: str, name: str, value: Any, explicit: bool) -> Any:
        """Return the value if the cloud accepts it, else the best replacement.

        The steps Night and Turbo and values this version does not know are
        shown but never sent. A value that was only filled in from the status
        is replaced by the last accepted one of the unit; a value the command
        named by the nearest one that can be sent.
        """
        enum_cls = COMMAND_ENUMS[name]
        if value is not None and not is_compat_member(enum_cls, value):
            return value
        replacement = (
            None if explicit else self._last_sendable.get(serial, {}).get(name)
        )
        if replacement is None:
            replacement = self._nearest_sendable(name, value)
        if replacement is None:
            LOGGER.error(
                "%s %r of %s cannot be sent and no earlier value is known - "
                "command skipped",
                name,
                getattr(value, "name", value),
                serial,
            )
            return None
        LOGGER.debug(
            "%s %r for %s is a value the cloud does not accept - sending %r instead",
            name,
            getattr(value, "name", value),
            serial,
            replacement.name,
        )
        return replacement

    def _check_mode_applied(self, device: Device, reported: Any, now: float) -> None:
        """Compare the operating mode read with the one sent last."""
        serial = device.serial_number
        expected = self._mode_expect.get(serial)
        if expected is None:
            return
        sent, when = expected
        name = getattr(sent, "name", str(sent))
        if reported == sent:
            self._mode_expect.pop(serial, None)
            LOGGER.debug("Operating mode %s confirmed on %s", name, serial)
            return
        if self.neuracell._device_under_control(serial, device):  # noqa: SLF001
            # The protection mode is expected to differ from the user's command.
            self._mode_expect.pop(serial, None)
            return
        waited = now - when
        if waited < const.MODE_VERIFY_WINDOW_S:
            return
        self._mode_expect.pop(serial, None)
        master = self.zone_master(device)
        if master is not None:
            LOGGER.warning(
                "Operating mode for %s: %s was sent and accepted by the cloud, but "
                "the unit still reports %s after %d s. This unit is a Slave - it "
                "runs with its zone Master %s. Set the mode on the Master",
                serial,
                name,
                getattr(reported, "name", reported),
                int(waited),
                master.serial_number,
            )
        else:
            LOGGER.warning(
                "Operating mode for %s: %s was sent and accepted by the cloud, but "
                "the unit still reports %s after %d s. The cloud confirms the call, "
                "not that the unit has carried it out",
                serial,
                name,
                getattr(reported, "name", reported),
                int(waited),
            )

    async def async_command(self, device: Device, values: dict[str, Any]) -> None:
        """Set values of a unit: operating_mode, fan_speed, humidity_level, light_sensor_level.

        Commands for the same unit that arrive within command_coalesce_ms are
        sent in one call. An automation that sets the mode and then the fan
        speed would otherwise write the old mode back with the second command.
        Returns when the command was sent and the status was read again.
        """
        serial = device.serial_number
        self._pending_cmds.setdefault(serial, {}).update(values)
        waiter = self._cmd_waiters.get(serial)
        if waiter is None:
            waiter = self.hass.loop.create_future()
            self._cmd_waiters[serial] = waiter
            self.config_entry.async_create_background_task(
                self.hass,
                self._async_flush_later(device, waiter),
                f"{DOMAIN} command for {serial}",
            )
        await asyncio.shield(waiter)

    async def _async_flush_later(
        self, device: Device, waiter: asyncio.Future[None]
    ) -> None:
        serial = device.serial_number
        try:
            if self.coalesce_s > 0:
                await asyncio.sleep(self.coalesce_s)
            # The lock NeuraCell-X takes too: a command never interleaves with
            # a protection change or a restore of the same unit.
            lock = self.neuracell.unit_lock(serial)
            async with lock:
                if self._cmd_waiters.get(serial) is waiter:
                    del self._cmd_waiters[serial]
                values = self._pending_cmds.pop(serial, None)
                if values:
                    await self._async_flush(device, values)
        except Exception as error:  # noqa: BLE001
            LOGGER.error("The command for %s could not be sent: %s", serial, error)
        finally:
            if self._cmd_waiters.get(serial) is waiter:
                del self._cmd_waiters[serial]
            if not waiter.done():
                waiter.set_result(None)

    async def _async_flush(self, device: Device, values: dict[str, Any]) -> None:  # noqa: C901
        """Send all values that are waiting for one unit in a single call."""
        serial = device.serial_number
        if len(values) > 1:
            LOGGER.debug(
                "Sending %d values for %s in one call: %s",
                len(values),
                serial,
                ", ".join(sorted(values)),
            )
        # While NeuraCell-X protection controls this unit, a change of mode, fan
        # speed or humidity level must not fight it. It becomes what the unit
        # returns to once protection clears. Units outside a dew-point block
        # stay fully controllable; the light sensor level always passes.
        nc = self.neuracell
        if nc._device_under_control(serial, device):  # noqa: SLF001
            deferred = [name for name in values if name in BASELINE_VALUES]
            if values.get("operating_mode") == TIMED_MODE:
                deferred.remove("operating_mode")
                LOGGER.warning(
                    "NeuraCell-X active: timed extraction requested for %s while "
                    "protection controls the unit - not possible now and not "
                    "carried over",
                    serial,
                )
            for name in deferred:
                if serial in nc._saved_modes:  # noqa: SLF001
                    nc._saved_modes[serial][name] = values[name]  # noqa: SLF001
                else:
                    nc._pending_manual.setdefault(serial, {})[name] = values[name]  # noqa: SLF001
            if deferred:
                LOGGER.warning(
                    "NeuraCell-X active: the change of %s on %s is carried out when "
                    "protection clears",
                    ", ".join(sorted(deferred)),
                    serial,
                )
                nc.persist()
            values = {n: v for n, v in values.items() if n not in BASELINE_VALUES}
            if not values:
                return
        else:
            if (
                values.get("operating_mode") == TIMED_MODE
                and serial in nc._saved_modes  # noqa: SLF001
                and not await nc._restore_one(serial, device)  # noqa: SLF001
            ):
                # The unit has not got its previous mode back yet. Started now,
                # the timed extraction would end in the protection mode.
                LOGGER.warning(
                    "Timed extraction for %s: the unit has not got its previous "
                    "mode back yet - not started. Please try again in a moment",
                    serial,
                )
                return
            for store in (nc._saved_modes, nc._pending_manual):  # noqa: SLF001
                if serial in store:
                    for name, value in values.items():
                        if name in BASELINE_VALUES and not (
                            name == "operating_mode" and value == TIMED_MODE
                        ):
                            store[serial][name] = value
        nc.persist()

        status = self._statuses.get(serial)
        if status is None:
            status = (await self._async_read(device))[1]
        if status is None:
            LOGGER.error(
                "The command for %s was not sent: its status cannot be read", serial
            )
            return
        self._remember_sendable(serial, status)
        current_mode = status["operating_mode"]
        mode = {name: status[name] for name in COMMAND_ENUMS}
        # Values sent a moment ago that the unit does not report yet count as
        # what the user set, so a second command cannot turn them back.
        now = time.monotonic()
        for name, (value, when) in self._sent.get(serial, {}).items():
            if now - when <= const.MODE_VERIFY_WINDOW_S:
                mode[name] = value
        # A unit that is switched off reports a default light sensor level.
        if (
            "light_sensor_level" not in values
            and "light_sensor_level" not in self._sent.get(serial, {})
            and current_mode == OperatingMode.Off
            and serial in self._last_light
        ):
            mode["light_sensor_level"] = self._last_light[serial]
        # A restore or a deferred change that is still waiting (the unit was
        # unreachable) is part of what the user asked for. And right after a
        # restore the cloud may still report the protection mode: the values
        # not named here then come from what was restored.
        pending_base = None
        pending_manual = {}
        if not nc._device_under_control(serial, device):  # noqa: SLF001
            pending_base = nc._saved_modes.get(serial) or None  # noqa: SLF001
            if pending_base is not None:
                mode.update({n: pending_base[n] for n in BASELINE_VALUES})
            else:
                pending_manual = nc._pending_manual.get(serial) or {}  # noqa: SLF001
                mode.update(pending_manual)
            restored = nc._recent_restore.get(serial)  # noqa: SLF001
            if (
                pending_base is None
                and not pending_manual
                and restored is not None
                and time.monotonic() - restored[1] < RESTORE_STALE_WINDOW_S
                and current_mode
                == (restored[2] if restored[2] is not None else current_mode)
                and current_mode in (RADON_PROTECTION_MODE, DEWPOINT_BLOCK_MODE)
            ):
                mode.update({n: restored[0][n] for n in BASELINE_VALUES})
        mode.update(values)
        light = values.get("light_sensor_level")
        if light is not None and not is_compat_member(LightSensorLevel, light):
            self._last_light[serial] = light
        for name in COMMAND_ENUMS:
            mode[name] = self._sendable(serial, name, mode[name], name in values)
        if mode["operating_mode"] is None:
            return
        LOGGER.debug(
            "Writing to device %s: %s",
            serial,
            ", ".join(f"{name} {value.name}" for name, value in mode.items()),
        )
        result = await self.async_request(device, lambda: device.change_mode(mode))
        match result:
            case Success(_):
                self._last_sendable.setdefault(serial, {}).update(mode)
                sent = self._sent.setdefault(serial, {})
                for name in values:
                    sent[name] = (mode[name], now)
                if "operating_mode" in values:
                    self._mode_expect[serial] = (mode["operating_mode"], now)
                # The user's own command is the reference now, not the last restore.
                nc._recent_restore.pop(serial, None)  # noqa: SLF001
                under_control = nc._device_under_control(serial, device)  # noqa: SLF001
                if pending_base is not None and not under_control:
                    # This command carried the whole baseline: the restore is done.
                    nc._saved_modes.pop(serial, None)  # noqa: SLF001
                    nc._applied.pop(serial, None)  # noqa: SLF001
                    nc._from_disk.discard(serial)  # noqa: SLF001
                    nc.persist()
                if not under_control:
                    nc._pending_manual.pop(serial, None)  # noqa: SLF001
            case Failure(error):
                LOGGER.error(
                    "Writing to device %s: failed. %s", serial, describe_failure(error)
                )
        # All entities of the unit show what it reports after the command.
        await self._async_read_now(device)

    # ----- stored: filter resets under way, maintenance acknowledgements -----
    async def async_load_store(self) -> None:
        """Read what the integration keeps over a restart."""
        # Over a reload the data in memory is the newest; the file follows it.
        kept = self.hass.data.setdefault(f"{DOMAIN}_stored", {})
        if self.config_entry.entry_id in kept:
            self._stored = kept[self.config_entry.entry_id]
            return
        try:
            data = await self._store.async_load()
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("The stored filter data could not be read: %s", error)
            data = None
        if isinstance(data, dict):
            pending = data.get("pending_resets")
            acks = data.get("filter_ack")
            self._stored = {
                "pending_resets": [str(s) for s in pending]
                if isinstance(pending, list)
                else [],
                "filter_ack": acks if isinstance(acks, dict) else {},
            }
            if isinstance(data.get("neuracell"), dict):
                self._stored["neuracell"] = data["neuracell"]
        kept[self.config_entry.entry_id] = self._stored

    def _save_store(self) -> None:
        self._store.async_delay_save(lambda: self._stored, 1.0)

    async def async_remove_store(self) -> None:
        """Delete what was stored for this entry."""
        await self._store.async_remove()

    @callback
    def async_resume(self) -> None:
        """After the start: resume filter resets that were under way."""
        self.nc_inputs.async_start(
            lambda coro: self.config_entry.async_create_background_task(
                self.hass, coro, f"{DOMAIN} NeuraCell-X inputs"
            )
        )
        self.config_entry.async_on_unload(self.nc_inputs.async_stop)
        known = {device.serial_number: device for device in self.devices}
        for serial in list(self._stored["pending_resets"]):
            device = known.get(serial)
            if device is None:
                self._stored["pending_resets"].remove(serial)
                continue
            LOGGER.info("Resuming the unconfirmed filter reset for %s", serial)
            self.async_start_filter_reset(device)
        for serial, device in known.items():
            record = self._filter_ack_record(serial)
            if record and serial not in self._reset_inflight:
                self._reset_shown[serial] = "acknowledged"
                LOGGER.info(
                    "Filter acknowledgement for %s (%s) still in force: recorded %s, "
                    "runs out %s",
                    serial,
                    device.name,
                    _utc(record["acked_at"]),
                    _utc(record["valid_until"]),
                )

    # ----- maintenance acknowledgement of a Slave (slave_filter_soft_reset) -----
    def _filter_ack_times(self, record: Any) -> tuple[float, float] | None:
        if not isinstance(record, dict):
            return None
        try:
            acked_at = float(record.get("acked_at", 0))
        except (TypeError, ValueError):
            return None
        if (
            not math.isfinite(acked_at)
            or acked_at <= 0
            or acked_at > time.time() + 86400.0
        ):
            return None
        return acked_at, acked_at + self.filter_ack_ttl_s

    def _filter_ack_record(self, serial: str) -> dict[str, Any] | None:
        """Return the acknowledgement of a unit if one is in force."""
        if not self.soft_reset:
            return None
        record = self._stored["filter_ack"].get(serial)
        times = self._filter_ack_times(record)
        if times is None or time.time() > times[1]:
            return None
        return {"acked_at": times[0], "valid_until": times[1]}

    def _filter_ack_drop(self, serial: str, why: str) -> None:
        if self._stored["filter_ack"].pop(serial, None) is not None:
            self._save_store()
            LOGGER.info("Filter acknowledgement for %s removed: %s", serial, why)
        self._ack_good_streak.pop(serial, None)
        self._ack_odd_raw.pop(serial, None)

    def _filter_ack_write(self, serial: str, raw_status: Any) -> bool:
        if not self.soft_reset:
            return False
        self._stored["filter_ack"][serial] = {
            "acked_at": time.time(),
            "raw_when_acked": str(raw_status),
        }
        self._save_store()
        self._ack_good_streak.pop(serial, None)
        self._ack_odd_raw.pop(serial, None)
        return True

    def _filter_ack_effective(self, serial: str, raw_status: Any) -> Any:
        """Return the filter status to show: "Good" while an acknowledgement holds.

        An acknowledgement ends when filter_ack_ttl_days are over, or when the
        unit itself reports "Good" for long enough. Both are written to the log.
        """
        if not self.soft_reset:
            return raw_status
        record = self._stored["filter_ack"].get(serial)
        if not record:
            return raw_status
        times = self._filter_ack_times(record)
        if times is None:
            self._filter_ack_drop(serial, "the stored record is unusable")
            return raw_status
        age = time.time() - times[0]
        if age > self.filter_ack_ttl_s:
            self._filter_ack_drop(
                serial,
                f"its validity of {self.filter_ack_ttl_s / 86400.0:.0f} days ran out",
            )
            return raw_status
        number = filter_status_to_num(raw_status)
        if number is None:
            self._ack_good_streak.pop(serial, None)
            if self._ack_odd_raw.get(serial) != str(raw_status):
                self._ack_odd_raw[serial] = str(raw_status)
                LOGGER.info(
                    "Filter acknowledgement for %s kept: the unit reported the "
                    "unknown filter value %r",
                    serial,
                    raw_status,
                )
            return "Good"
        self._ack_odd_raw.pop(serial, None)
        if number <= 0:
            now = time.monotonic()
            count, since = self._ack_good_streak.get(serial, (0, now))
            count += 1
            self._ack_good_streak[serial] = (count, since)
            if (
                count >= const.FILTER_ACK_CLEAR_POLLS
                and now - since >= const.FILTER_ACK_CLEAR_MIN_S
            ):
                self._filter_ack_drop(
                    serial,
                    f"the unit itself has reported {raw_status!r} for {count} "
                    "readings in a row - the acknowledgement is no longer needed",
                )
            return raw_status
        self._ack_good_streak.pop(serial, None)
        return "Good"

    # ----- filter reset -----
    def reset_state(self, serial: str) -> str:
        """Return idle, running, confirmed, acknowledged or unconfirmed."""
        return self._reset_shown.get(serial, "idle")

    def _sync_reset_state_with_ack(self, serial: str, acked: bool) -> None:
        if serial in self._reset_inflight:
            return
        shown = self._reset_shown.get(serial)
        if acked:
            if shown in (None, "idle"):
                self._reset_shown[serial] = "acknowledged"
        elif shown == "acknowledged":
            self._reset_shown[serial] = "idle"

    def reset_info(self, device: Device) -> dict[str, Any]:
        """Say what the filter reset does on this unit (attributes of the button)."""
        serial = device.serial_number
        master = self.zone_master(device)
        role = self._live_role.get(serial) or getattr(device, "role", None) or "unknown"
        last = self._reset_log.get(serial, {})
        if master is not None:
            note = (
                "Coupled Slave: the filter reset of the cloud is accepted but not "
                "carried out by this unit, so its own filter counter stays. It "
                "clears with a factory reset of the unit (then set the unit up "
                "again in the app and assign it to its zone again). The button "
                "still sends the reset to this unit and to its zone Master "
                f"{self._unit_label(master)}, which clears the Master's counter"
                + (
                    ", and records the maintenance acknowledgement "
                    "(slave_filter_soft_reset)."
                    if self.soft_reset
                    else "; switch on slave_filter_soft_reset to record the "
                    "maintenance as an acknowledgement instead."
                )
            )
            clears = False
        elif str(role).lower() == "master":
            note = (
                "Master: the filter reset of the cloud clears this unit's counter. "
                "Filter Reset Status confirms it against the real status of the "
                "unit after the reset."
            )
            clears = True
        else:
            note = (
                "Standalone or not coupled: the filter reset of the cloud is sent "
                "to this unit and confirmed against its real status."
            )
            clears = True
        return {
            "device_role": str(role),
            "zone_master": self._unit_label(master) if master is not None else None,
            "cloud_reset_clears_counter": clears,
            "note": note,
            "last_reset_started": _utc(last.get("started")),
            "last_reset_result": last.get("result"),
            "last_reset_result_at": _utc(last.get("result_at")),
        }

    @callback
    def async_start_filter_reset(self, device: Device) -> None:
        """Run one filter reset per unit in the background."""
        serial = device.serial_number
        if serial in self._reset_inflight:
            LOGGER.info(
                "Filter reset for %s already running - extra press ignored", serial
            )
            return
        self._reset_inflight.add(serial)
        if serial not in self._stored["pending_resets"]:
            self._stored["pending_resets"].append(serial)
            self._save_store()
        self._reset_shown[serial] = "running"
        self._reset_log[serial] = {
            "started": time.time(),
            "result": None,
            "result_at": None,
        }
        self.async_update_listeners()
        task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_reset_filter(device),
            f"{DOMAIN} filter reset {serial}",
        )

        def done(finished: asyncio.Task[str]) -> None:
            self._reset_inflight.discard(serial)
            state = "unconfirmed"
            if finished.cancelled():
                # Home Assistant stops or the entry is reloaded: resumed at the start.
                return
            try:
                state = finished.result()
            except Exception as error:  # noqa: BLE001
                LOGGER.error("Filter reset for %s failed: %s", serial, error)
            self._reset_shown[serial] = state
            if serial in self._stored["pending_resets"]:
                self._stored["pending_resets"].remove(serial)
                self._save_store()
            record = self._reset_log.setdefault(serial, {})
            record["result"], record["result_at"] = state, time.time()
            self.async_update_listeners()

        task.add_done_callback(done)

    async def _async_reset_filter(self, device: Device) -> str:
        """Send the filter reset and check it against the real status.

        The reset goes to the unit and to the Master of its zone; only the
        documented call device/reset-filter is ever sent. A Master or a
        standalone unit is read again a few times until its counter has
        improved. A Slave cannot be cleared through the cloud at all: the
        manufacturer resets filters per zone through the Master. With
        slave_filter_soft_reset the maintenance is recorded instead.
        """
        serial = device.serial_number
        master = self.zone_master(device)
        before = await self._async_read_now(device)
        before_raw = (before or {}).get("filters_status")
        before_num = filter_status_to_num(before_raw)
        if before_num == 0:
            LOGGER.info(
                "Filter reset for %s: the filter status is already %r - nothing to do",
                serial,
                before_raw,
            )
            return "confirmed"
        accepted = False
        targets = [(serial, "unit")]
        if master is not None:
            targets.append((master.serial_number, "zone Master"))
        for target, label in targets:
            try:
                result = await self.async_request(
                    device,
                    lambda target=target: device.api.get(
                        "device/reset-filter", {"deviceSerialNumber": target}
                    ),
                )
            except Exception as error:  # noqa: BLE001
                LOGGER.warning(
                    "Filter reset [%s] %s: no answer (%s)", label, target, error
                )
                continue
            match result:
                case Success(_):
                    accepted = True
                    LOGGER.info("Filter reset [%s] %s: sent", label, target)
                case Failure(error):
                    LOGGER.info(
                        "Filter reset [%s] %s: %s",
                        label,
                        target,
                        describe_failure(error),
                    )
        if not accepted:
            LOGGER.info("Filter reset for %s: the cloud did not take it", serial)
            return "unconfirmed"
        after = before
        for _ in range(1 if master is not None else 3):
            await asyncio.sleep(const.FILTER_RESET_VERIFY_DELAY)
            after = await self._async_read_now(device)
            after_num = filter_status_to_num((after or {}).get("filters_status"))
            if after_num is not None and (
                after_num == 0 or (before_num is not None and after_num < before_num)
            ):
                LOGGER.info(
                    "Filter reset for %s: confirmed - filter status %r -> %r",
                    serial,
                    before_raw,
                    (after or {}).get("filters_status"),
                )
                self._filter_ack_drop(serial, "the counter really cleared")
                return "confirmed"
        if master is None:
            LOGGER.info(
                "Filter reset for %s: sent, but the unit still reports the filter "
                "status %r. If the unit carries it out, the change shows on a later "
                "reading",
                serial,
                (after or {}).get("filters_status") if after else None,
            )
            return "unconfirmed"
        acknowledged = self._filter_ack_write(
            serial, (after or {}).get("filters_status") or "Bad"
        )
        LOGGER.warning(
            "Filter reset for %s: this unit is a Slave - its filter counter cannot "
            "be cleared through the cloud. The manufacturer resets filters per "
            "zone through the Master only. To clear this unit's own counter, "
            "factory-reset the unit, set it up again in the app and assign it to "
            "its zone again%s",
            serial,
            ". The maintenance was recorded: its filter status reads Good, the "
            "unit's own value stays visible as the raw filter status"
            if acknowledged
            else "; or switch on the option slave_filter_soft_reset to record the "
            "maintenance",
        )
        if acknowledged:
            await self._async_read_now(device)
            return "acknowledged"
        return "unconfirmed"

    # ----- NeuraCell-X -----
    async def async_read_for_neuracell(self, device: Device) -> DeviceStatus | None:
        """Read the status of a unit now, for NeuraCell-X."""
        if device in self.devices:
            return await self._async_read_now(device)
        # radon meter or Taupunktsteuerung of the source "device"
        return (await self._async_read(device))[1]

    async def async_set_device_mode(
        self, device: Device, operating_mode: Any, fan_speed: Any, humidity_level: Any
    ) -> bool:
        """Set mode, fan speed and humidity level; the light sensor level stays."""
        serial = device.serial_number
        light = self._last_light.get(serial)
        if light is None:
            status = self._statuses.get(serial) or (await self._async_read(device))[1]
            if status is not None:
                self._remember_sendable(serial, status)
                light = status["light_sensor_level"]
                if status[
                    "operating_mode"
                ] != OperatingMode.Off and not is_compat_member(
                    LightSensorLevel, light
                ):
                    self._last_light[serial] = light
        if light is None:
            light = LightSensorLevel.Off
        mode = {
            "operating_mode": operating_mode,
            "fan_speed": fan_speed,
            "humidity_level": humidity_level,
            "light_sensor_level": light,
        }
        for name in COMMAND_ENUMS:
            mode[name] = self._sendable(serial, name, mode[name], False)
        if mode["operating_mode"] is None:
            return False
        try:
            result = await self.async_request(device, lambda: device.change_mode(mode))
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("NeuraCell-X: %s could not be set (%s)", serial, error)
            return False
        if isinstance(result, Failure):
            LOGGER.error(
                "NeuraCell-X: %s could not be set: %s",
                serial,
                describe_failure(result.failure()),
            )
            return False
        self._last_sendable.setdefault(serial, {}).update(mode)
        # What the user sent before no longer stands for what the unit is set to.
        for name in BASELINE_VALUES:
            self._sent.get(serial, {}).pop(name, None)
        LOGGER.info(
            "NeuraCell-X: %s set to %s / %s",
            serial,
            mode["operating_mode"].name,
            mode["fan_speed"].name,
        )
        return True

    async def _async_neuracell_cycle(self) -> None:
        """After every reading: read the sources, watch the connections, enforce."""
        cfg, nc = self.nc_cfg, self.neuracell
        steps = []
        # Radon first: it has priority over the dew point.
        if cfg.neuracell_enabled and cfg.radon_source == "device" and self.radon_device:
            steps.append(
                ("radon device", lambda: nc.poll_radon_device(self.radon_device))
            )
        if (
            cfg.dewpoint_enabled
            and cfg.dewpoint_source == "device"
            and self.dewpoint_device
        ):
            steps.append(
                (
                    "dew-point device",
                    lambda: nc.poll_dewpoint_device(self.dewpoint_device),
                )
            )
        if cfg.dewpoint_enabled:
            steps.append(("dew-point watch", nc.check_dewpoint_link))
        if (
            cfg.neuracell_enabled
            and cfg.radon_source != "device"
            and cfg.radon_entities
        ):
            steps.append(("radon meter watch", nc.check_radon_meter_link))
        steps.append(("enforce", nc.enforce))
        for name, step in steps:
            try:
                await step()
            except Exception:  # noqa: BLE001
                LOGGER.exception("NeuraCell-X: %s failed", name)
        if (
            not (cfg.neuracell_enabled or cfg.dewpoint_enabled)
            and "neuracell" in self._stored
            and not nc._saved_modes  # noqa: SLF001
        ):
            # Both switched off in the options and every unit has its mode back.
            del self._stored["neuracell"]
            self._save_store()

    def neuracell_state(self) -> dict[str, Any]:
        """Return what the NeuraCell-X entities show."""
        nc = self.neuracell
        return {
            "radon_protection": nc.radon_active,
            "radon": nc.last_radon,
            "radon_meter_connected": nc.radon_meter_connected(),
            "dewpoint_controller_connected": nc.dewpoint_controller_connected(),
            "dewpoint_block": nc.dewpoint_block,
            "dewpoint_fallback": nc.dewpoint_fallback,
            "indoor_dew_point": round(nc.indoor_dew_point, 1)
            if nc.indoor_dew_point is not None
            else None,
            "outdoor_dew_point": round(nc.outdoor_dew_point, 1)
            if nc.outdoor_dew_point is not None
            else None,
            "radon_value_alarm": nc._radon_value_alarm,  # noqa: SLF001
            "radon_signal_alarm": nc._radon_signal_alarm,  # noqa: SLF001
            "radon_threshold": self.nc_cfg.radon_threshold,
            "radon_sources": {
                source: value
                for source, (value, _t) in sorted(nc._radon_values.items())  # noqa: SLF001
            },
            "dewpoint_block_devices": sorted(self.nc_cfg.dewpoint_block_device_tokens)
            or "all",
        }
