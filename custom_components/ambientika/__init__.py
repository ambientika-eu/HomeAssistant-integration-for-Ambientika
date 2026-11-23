"""Custom integration for Ambientika devices with Home Assistant.

For more details about this integration, please refer to
https://github.com/ambientika-eu/HomeAssistant-integration-for-Ambientika
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from . import compat  # noqa: F401  (makes the ambientika_py lookups tolerant)
from .api import AmbientikaApiClientAuthenticationError, AmbientikaApiClientError
from .const import DOMAIN
from .hub import AmbientikaHub
from .migration import async_migrate_unique_ids, async_restore_deleted_entities

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.BINARY_SENSOR,
    Platform.CLIMATE,
    Platform.SELECT,
    Platform.SENSOR,
]


# https://developers.home-assistant.io/docs/config_entries_index/#setting-up-an-entry
async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up this integration using UI."""
    hass.data.setdefault(DOMAIN, {})

    hub = AmbientikaHub(hass, entry)
    try:
        await hub.login()
    except AmbientikaApiClientAuthenticationError as error:
        raise ConfigEntryError(
            "The Ambientika cloud did not accept user name and password. Remove "
            "the integration and add it again with the sign-in of the Ambientika app."
        ) from error
    except AmbientikaApiClientError as error:
        # No internet connection yet (for example after a power cut) or the
        # cloud does not answer: Home Assistant tries again by itself.
        raise ConfigEntryNotReady(str(error)) from error

    # Before the entities are created: entries Home Assistant kept from a setup
    # that was deleted are brought back, and entries that still carry a unique
    # ID built from the unit's name are moved to the serial number.
    async_restore_deleted_entities(hass, entry, hub.devices)
    async_migrate_unique_ids(hass, entry, hub.devices)

    # The first reading of the status of every unit. From here on the hub
    # reads it once per unit and interval for all entities together.
    # https://developers.home-assistant.io/docs/integration_fetching_data#coordinated-single-api-poll-for-data-for-all-entities
    await hub.async_load_store()
    # The modes the units had before NeuraCell-X protection took over. Also read
    # when NeuraCell-X was switched off meanwhile, so the units get them back.
    hub.neuracell.load_persisted()
    await hub.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = hub

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    hub.async_resume()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Handle removal of an entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete what the integration stored for an entry that is removed."""
    from homeassistant.helpers.storage import Store  # noqa: PLC0415

    hass.data.get(f"{DOMAIN}_stored", {}).pop(entry.entry_id, None)
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry."""
    await hass.config_entries.async_reload(entry.entry_id)
