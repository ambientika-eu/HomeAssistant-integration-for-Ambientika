"""A demonstration 'hub' that connects several devices.

References:
 - https://developers.home-assistant.io/docs/integration_fetching_data/#coordinated-single-api-poll-for-data-for-all-entities

"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import timedelta
import time
from typing import Any

from ambientika_py import Device

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from homeassistant.helpers.update_coordinator import UpdateFailed, DataUpdateCoordinator


from .api import (
    AmbientikaApiClient,
    AmbientikaApiClientAuthenticationError,
    AmbientikaApiClientError,
    status_code,
)
from .const import DOMAIN, LOGGER

# HTTP status codes with which the cloud turns down an expired sign-in.
SIGN_IN_EXPIRED = (401, 403)
# Seconds until the integration may sign in again: after it has just done so,
# after the cloud could not be reached, and after the cloud did not accept user
# name and password.
SIGN_IN_INTERVAL = 60
SIGN_IN_RETRY = 60
SIGN_IN_RETRY_REJECTED = 1800


class AmbientikaHub(DataUpdateCoordinator):
    """Connection Hub to all devices."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, config: Mapping[str, Any]) -> None:
        """Initialize the hub to manage all devices and the API facade."""
        self._hass_config = hass
        self._hass = hass
        self._credentials = {
            "username": config.get(CONF_USERNAME, ""),
            "password": config.get(CONF_PASSWORD, ""),
        }
        self.devices = []
        self._sign_in_lock = asyncio.Lock()
        self._sign_in_not_before = 0.0
        self._sign_in_failing = False

        super().__init__(
            hass=hass,
            logger=LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )

    async def login(self) -> None:
        """Async loading of the devices."""

        self.client = AmbientikaApiClient(
            username=self._credentials["username"],
            password=self._credentials["password"],
        )
        self.devices = await self.client.async_get_data()

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
        result = await request()
        if status_code(result) not in SIGN_IN_EXPIRED:
            return result
        if not await self._async_sign_in_again(device, token):
            return result
        return await request()

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

    async def _async_update_data(self):
        """Update data via library."""
        try:
            LOGGER.debug("HUB: Fetching data from Ambientika API.")
            return await self.client.async_get_data()
        except AmbientikaApiClientAuthenticationError as exception:
            raise ConfigEntryAuthFailed(exception) from exception
        except AmbientikaApiClientError as exception:
            raise UpdateFailed(exception) from exception
