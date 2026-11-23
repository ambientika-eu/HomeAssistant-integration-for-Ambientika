"""Configuration flow.

This will prompt the user for input when adding the intergation.
https://developers.home-assistant.io/docs/config_entries_config_flow_handler
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector

from .api import (
    AmbientikaApiClient,
    AmbientikaApiClientAuthenticationError,
    AmbientikaApiClientCommunicationError,
    AmbientikaApiClientError,
)
from . import const as c
from .const import (
    CONF_AVERAGE_WINDOW_DEVICES,
    CONF_AVERAGE_WINDOW_MIN,
    CONF_COMMAND_COALESCE_MS,
    CONF_FAILURE_THRESHOLD,
    CONF_FILTER_ACK_TTL_DAYS,
    CONF_HUMIDITY_AVG_HYSTERESIS,
    CONF_HUMIDITY_AVG_HYSTERESIS_DEVICES,
    CONF_POLL_INTERVAL,
    CONF_SLAVE_FILTER_SOFT_RESET,
    CONF_TEMPERATURE_AVG_HYSTERESIS,
    CONF_TEMPERATURE_AVG_HYSTERESIS_DEVICES,
    CONF_TEMPERATURE_AVG_WINDOW_MIN,
    DEFAULT_OPTIONS,
    DEWPOINT_OPTIONS,
    GENERAL_OPTIONS,
    RADON_OPTIONS,
    DOMAIN,
    LOGGER,
)


class AmbientikaFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Config Flow Class."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> AmbientikaOptionsFlow:
        """Return the dialog for the options."""
        return AmbientikaOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict | None = None,
    ) -> FlowResult:
        """Handle a flow initialized by the user."""
        errors = {}

        if user_input is not None:
            # An account that is set up twice would create every entity twice.
            username = user_input[CONF_USERNAME].strip().casefold()
            for entry in self._async_current_entries():
                if entry.data.get(CONF_USERNAME, "").strip().casefold() == username:
                    return self.async_abort(reason="already_configured")

            try:
                devices = await _test_pairing(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
            except AmbientikaApiClientAuthenticationError as exception:
                LOGGER.warning(exception)
                errors["base"] = "auth"
            except AmbientikaApiClientCommunicationError as exception:
                LOGGER.warning(exception)
                errors["base"] = "connection"
            except AmbientikaApiClientError as exception:
                LOGGER.exception(exception)
                errors["base"] = "unknown"
            except Exception as exception:  # pylint: disable=broad-except
                LOGGER.exception(exception)
                errors["base"] = "unknown"

            if not errors:
                if len(devices) == 0:
                    return self.async_abort(reason="no_supported_devices_found")

                return self.async_create_entry(
                    title=user_input[CONF_USERNAME],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME,
                        default=(user_input or {}).get(CONF_USERNAME, vol.UNDEFINED),
                    ): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.TEXT
                        ),
                    ),
                    vol.Required(CONF_PASSWORD): selector.TextSelector(
                        selector.TextSelectorConfig(
                            type=selector.TextSelectorType.PASSWORD
                        ),
                    ),
                }
            ),
            errors=errors,
        )


async def _test_pairing(username, password) -> list:
    client = AmbientikaApiClient(username, password)
    return await client.async_get_data()


def _number(low: float, high: float, step: float, unit: str | None = None):
    config = {
        "min": low,
        "max": high,
        "step": step,
        "mode": selector.NumberSelectorMode.BOX,
    }
    if unit:
        config["unit_of_measurement"] = unit
    return selector.NumberSelector(selector.NumberSelectorConfig(**config))


_TEXT = selector.TextSelector(selector.TextSelectorConfig(multiline=False))

# The options of the Ambientika MQTT Bridge, with the same names and limits.
GENERAL_FIELDS = {
    CONF_POLL_INTERVAL: _number(10, 300, 1, "s"),
    CONF_FAILURE_THRESHOLD: _number(1, 10, 1),
    CONF_COMMAND_COALESCE_MS: _number(0, 10000, 1, "ms"),
    CONF_AVERAGE_WINDOW_MIN: _number(1, 240, 1, "min"),
    CONF_AVERAGE_WINDOW_DEVICES: _TEXT,
    CONF_HUMIDITY_AVG_HYSTERESIS: _number(0, 10, 0.1, "%"),
    CONF_HUMIDITY_AVG_HYSTERESIS_DEVICES: _TEXT,
    CONF_TEMPERATURE_AVG_HYSTERESIS: _number(0, 5, 0.1, "°C"),
    CONF_TEMPERATURE_AVG_HYSTERESIS_DEVICES: _TEXT,
    CONF_TEMPERATURE_AVG_WINDOW_MIN: _number(0, 240, 1, "min"),
    CONF_SLAVE_FILTER_SOFT_RESET: selector.BooleanSelector(),
    CONF_FILTER_ACK_TTL_DAYS: _number(1, 3650, 1, "d"),
}


def _choice(key: str, options: list[str]):
    return selector.SelectSelector(
        selector.SelectSelectorConfig(
            options=options,
            translation_key=key,
            mode=selector.SelectSelectorMode.DROPDOWN,
        )
    )


def _entity(domains: list[str], multiple: bool = False):
    return selector.EntitySelector(
        selector.EntitySelectorConfig(domain=domains, multiple=multiple)
    )


_VALUE = ["sensor", "number", "input_number"]
_SWITCH = ["binary_sensor", "input_boolean", "switch"]
_FAN = ["low", "medium", "high"]

# NeuraCell-X. Where the bridge names an MQTT topic, an entity is chosen here.
RADON_FIELDS = {
    c.CONF_NEURACELL_ENABLED: selector.BooleanSelector(),
    c.CONF_RADON_SOURCE: _choice("radon_source", ["signal", "device"]),
    c.CONF_RADON_ENTITIES: _entity(_VALUE, multiple=True),
    c.CONF_RADON_ALARM_ENTITY: _entity(_SWITCH),
    c.CONF_RADON_THRESHOLD: _number(50, 1000, 1, "Bq/m³"),
    c.CONF_RADON_HYSTERESIS: _number(0, 500, 1, "Bq/m³"),
    c.CONF_RADON_PROTECTION_FAN: _choice("fan", _FAN),
    c.CONF_RADON_METER_TIMEOUT: _number(0, 1440, 1, "min"),
    c.CONF_RADON_DEVICE_SERIAL: _TEXT,
    c.CONF_RADON_DEVICE_ALARM_FIELD: _TEXT,
    c.CONF_RADON_DEVICE_ALARM_VALUES: _TEXT,
}
DEWPOINT_FIELDS = {
    c.CONF_DEWPOINT_ENABLED: selector.BooleanSelector(),
    c.CONF_DEWPOINT_SOURCE: _choice(
        "dewpoint_source", ["signal", "computed", "device"]
    ),
    c.CONF_DEWPOINT_BLOCK_ENTITY: _entity(_SWITCH),
    c.CONF_DEWPOINT_CONTROLLER_ENTITY: _entity(_SWITCH),
    c.CONF_DEWPOINT_BLOCK_DEVICES: _TEXT,
    c.CONF_DEWPOINT_INDOOR_TEMP: _entity(_VALUE),
    c.CONF_DEWPOINT_INDOOR_HUMIDITY: _entity(_VALUE),
    c.CONF_DEWPOINT_OUTDOOR_TEMP: _entity(_VALUE),
    c.CONF_DEWPOINT_OUTDOOR_HUMIDITY: _entity(_VALUE),
    c.CONF_DEWPOINT_MARGIN: _number(0, 20, 0.1, "°C"),
    c.CONF_DEWPOINT_HYSTERESIS: _number(0, 20, 0.1, "°C"),
    c.CONF_DEWPOINT_DEVICE_SERIAL: _TEXT,
    c.CONF_DEWPOINT_DEVICE_BLOCK_MODES: _TEXT,
    c.CONF_DEWPOINT_LOST_ACTION: _choice(
        "dewpoint_lost_action", ["keep", "release", "block"]
    ),
    c.CONF_DEWPOINT_SIGNAL_TIMEOUT: _number(0, 1440, 1, "min"),
    c.CONF_DEWPOINT_CONTROLLER_TIMEOUT: _number(0, 1440, 1, "min"),
    c.CONF_DEWPOINT_FALLBACK_TIMEOUT: _number(0, 1440, 1, "min"),
    c.CONF_DEWPOINT_FALLBACK_FAN: _choice("fan", _FAN),
}
_WHOLE = (
    CONF_POLL_INTERVAL,
    CONF_FAILURE_THRESHOLD,
    CONF_COMMAND_COALESCE_MS,
    CONF_AVERAGE_WINDOW_MIN,
    CONF_TEMPERATURE_AVG_WINDOW_MIN,
    CONF_FILTER_ACK_TTL_DAYS,
    c.CONF_RADON_THRESHOLD,
    c.CONF_RADON_HYSTERESIS,
    c.CONF_RADON_METER_TIMEOUT,
    c.CONF_DEWPOINT_SIGNAL_TIMEOUT,
    c.CONF_DEWPOINT_CONTROLLER_TIMEOUT,
    c.CONF_DEWPOINT_FALLBACK_TIMEOUT,
)
PAGES = {
    "general": (GENERAL_FIELDS, GENERAL_OPTIONS),
    "radon": (RADON_FIELDS, RADON_OPTIONS),
    "dewpoint": (DEWPOINT_FIELDS, DEWPOINT_OPTIONS),
}


class AmbientikaOptionsFlow(config_entries.OptionsFlow):
    """The options, on three pages: general, radon protection, dew-point control."""

    async def async_step_init(self, user_input: dict | None = None) -> FlowResult:
        """Offer the three pages."""
        return self.async_show_menu(step_id="init", menu_options=list(PAGES))

    async def async_step_general(self, user_input: dict | None = None) -> FlowResult:
        """Show the page with reading interval, averaged values, filter acknowledgement."""
        return self._page("general", user_input)

    async def async_step_radon(self, user_input: dict | None = None) -> FlowResult:
        """Show the page for NeuraCell-X radon protection."""
        return self._page("radon", user_input)

    async def async_step_dewpoint(self, user_input: dict | None = None) -> FlowResult:
        """Show the page for NeuraCell-X dew-point control."""
        return self._page("dewpoint", user_input)

    def _page(self, page: str, user_input: dict | None) -> FlowResult:
        """Show one page and store it; the integration reloads by itself."""
        fields, defaults = PAGES[page]
        current = {**DEFAULT_OPTIONS, **self.config_entry.options}
        if user_input is not None:
            options = dict(current)
            for key, default in defaults.items():
                # A field that was emptied is not sent.
                value = user_input.get(key, type(default)())
                if key in _WHOLE:
                    value = int(value)
                elif isinstance(value, str):
                    value = value.strip()
                options[key] = value
            return self.async_create_entry(title="", data=options)

        schema = {}
        for key, field in fields.items():
            if isinstance(defaults[key], str | list) and not isinstance(
                field, selector.SelectSelector
            ):
                # Text and entity fields may stay empty, so they are not required.
                schema[
                    vol.Optional(key, description={"suggested_value": current[key]})
                ] = field
            else:
                schema[vol.Required(key, default=current[key])] = field
        return self.async_show_form(step_id=page, data_schema=vol.Schema(schema))
