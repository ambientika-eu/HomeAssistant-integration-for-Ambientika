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
from datetime import timedelta
import time
from typing import Any

from ambientika_py import Device, DeviceStatus
from returns.result import Failure, Success

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import (
    AmbientikaApiClient,
    AmbientikaApiClientAuthenticationError,
    AmbientikaApiClientError,
    status_code,
)
from .const import (
    DOMAIN,
    LOGGER,
    REQUEST_TIMEOUT,
    STATUS_REQUESTS_AT_ONCE,
    UPDATE_INTERVAL,
)

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
        self.devices: list[Device] = []
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
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )

    async def login(self) -> None:
        """Async loading of the devices."""

        self.client = AmbientikaApiClient(
            username=self._credentials["username"],
            password=self._credentials["password"],
        )
        self.devices = await self.client.async_get_data()

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
        """Keep the readings and say in the log which units stopped or resumed answering."""
        names = {device.serial_number: device.name for device in self.devices}
        stopped: list[tuple[str, str]] = []
        resumed: list[str] = []
        for serial, status, problem, sent in readings:
            if sent < self._read_at.get(serial, 0.0):
                # The status shown comes from a request sent later than this one.
                continue
            self._read_at[serial] = sent
            self._statuses[serial] = status
            if status is None and serial not in self._silent:
                self._silent.add(serial)
                stopped.append((serial, problem or "no status"))
            elif status is not None and serial in self._silent:
                self._silent.discard(serial)
                resumed.append(serial)

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
