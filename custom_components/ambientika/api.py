"""API Client.

This uses https://pypi.org/project/ambientika/ as a facade.
Ambientika API: https://app.ambientika.eu:4521/swagger/index.html.
"""

import asyncio

from returns.result import Failure

from ambientika_py import authenticate, Device

from .const import DEFAULT_HOST, LOGGER, REQUEST_TIMEOUT

# HTTP status codes with which the cloud turns down user name and password.
SIGN_IN_REJECTED = (400, 401, 403)


class AmbientikaApiClientError(Exception):
    """Exception to indicate a general API error."""


class AmbientikaApiClientCommunicationError(AmbientikaApiClientError):
    """Exception to indicate that the Ambientika cloud gave no usable answer."""


class AmbientikaApiClientAuthenticationError(AmbientikaApiClientError):
    """Exception to indicate an authentication error."""


def status_code(result) -> int | None:
    """Return the HTTP status code of a request the cloud turned down."""
    if not isinstance(result, Failure):
        return None
    error = result.failure()
    if not isinstance(error, dict):
        return None
    code = error.get("status_code")
    return code if isinstance(code, int) else None


class AmbientikaApiClient:
    """API Client Class."""

    def __init__(self, username: str, password: str) -> None:
        """Create an instance of the API."""
        self._username = username
        self._password = password
        self._host = DEFAULT_HOST

    async def async_get_data(self) -> list[Device]:
        """Get all devices from the API.

        The devices are flattend. Meaning, the information about rooms and houses is not made available to hass.
        """

        try:
            LOGGER.debug("Authenticating with Ambientika API.")
            async with asyncio.timeout(REQUEST_TIMEOUT):
                authenticator = await authenticate(
                    self._username, self._password, self._host
                )
        except TimeoutError as exception:
            raise AmbientikaApiClientCommunicationError(
                f"The Ambientika cloud did not answer the sign-in within {REQUEST_TIMEOUT} seconds"
            ) from exception
        except Exception as exception:
            raise AmbientikaApiClientCommunicationError(
                f"No connection to the Ambientika cloud ({type(exception).__name__}: {exception})"
            ) from exception

        if isinstance(authenticator, Failure):
            code = status_code(authenticator)
            if code in SIGN_IN_REJECTED:
                raise AmbientikaApiClientAuthenticationError(
                    "The Ambientika cloud did not accept user name and password"
                )
            raise AmbientikaApiClientCommunicationError(
                f"The Ambientika cloud answered the sign-in with HTTP status {code}"
            )
        api_client = authenticator.unwrap()

        try:
            LOGGER.debug("fetching houses.")
            async with asyncio.timeout(REQUEST_TIMEOUT):
                houses = await api_client.houses()
        except TimeoutError as exception:
            raise AmbientikaApiClientCommunicationError(
                "The Ambientika cloud did not answer the request for the list of "
                f"units within {REQUEST_TIMEOUT} seconds"
            ) from exception
        except Exception as exception:
            raise AmbientikaApiClientCommunicationError(
                f"The list of units could not be read from the Ambientika cloud ({type(exception).__name__}: {exception})"
            ) from exception
        if isinstance(houses, Failure):
            code = status_code(houses)
            message = (
                "The Ambientika cloud answered the request for the list of units "
                f"with HTTP status {code}"
            )
            if code is None or code >= 500 or code in (408, 429):
                raise AmbientikaApiClientCommunicationError(message)
            raise AmbientikaApiClientError(message)

        try:
            # TODO: write tests
            LOGGER.debug("fetching devices.")
            devices = []
            for house in houses.unwrap():
                for room in house.rooms:
                    for device in room.devices:
                        # The zone index is unique only within one house.
                        device.house_id = getattr(house, "id", None)
                        devices.append(device)
            return devices
        except Exception as exception:
            raise AmbientikaApiClientError("Unknown error") from exception
