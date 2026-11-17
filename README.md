# Ambientika for Home Assistant

Official Home Assistant integration for **Ambientika** decentralised ventilation units with heat recovery.
Developed and maintained by **Südwind GmbH** (SUEDWIND S.R.L.), Eppan/Appiano, South Tyrol – the manufacturer of Ambientika.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ambientika-eu&repository=HomeAssistant-integration-for-Ambientika&category=integration)

## Supported devices

All Ambientika units that are set up in the **Ambientika app** (for example SMART and OFFICE). The integration uses the same Ambientika cloud account as the app.

## Entities per unit

| Platform | Entity | Description |
| --- | --- | --- |
| Climate | named like the unit | Operating mode, fan speed (Night and Turbo are shown, Low/Medium/High can be set), target humidity; shows the room temperature and the relative humidity |
| Sensor | Air Quality | Air quality level |
| Sensor | Filter Status | good / medium / bad |
| Binary sensor | Humidity Alarm | Humidity alarm reported by the unit |
| Binary sensor | Night Alarm | Night signal reported by the unit |
| Button | Filter Reset | Resets the filter counter after a filter change |

Temperature and humidity are values of the climate entity (`current_temperature`, `current_humidity`), not sensors of their own.

Every entity is tied to the serial number of its unit. A unit can be renamed in the Ambientika app, and two units can carry the same name, without entities being lost or created twice.

The status of every unit is read from the Ambientika cloud once every 30 seconds and shared by all its entities. A unit that does not answer is shown as unavailable until it answers again, and the log says so once.

## Installation

### HACS (recommended)

1. In HACS open the menu (three dots, top right) and choose **Custom repositories**.
2. Add `https://github.com/ambientika-eu/HomeAssistant-integration-for-Ambientika` with the type **Integration**.
3. Search for **Ambientika for Home Assistant**, download it and restart Home Assistant.

### Manual

Copy the folder `custom_components/ambientika` into `config/custom_components/ambientika` of your Home Assistant installation and restart Home Assistant.

## Configuration

Go to **Settings → Devices & services → Add integration**, search for **Ambientika** and sign in with the same e-mail address and password as in the Ambientika app. All units of the account are added automatically.

## Updating

Update in HACS and restart Home Assistant. Up to version 1.2 the entities were tied to the name of the unit; version 1.3 moves them to the serial number at its first start. Entity IDs, your own names, areas, dashboards, automations and the history stay as they are. Do not go back to 1.2.x afterwards without a backup, because the old version creates all entities a second time.

If Home Assistant starts while the Ambientika cloud cannot be reached, the integration tries again by itself; nothing has to be reloaded.

## Switching from the previous repository

The integration was previously published at `ambientika/HomeAssistant-integration-for-Ambientika`. That repository is no longer maintained. To switch:

1. In HACS remove the old repository.
2. Add this repository as described above, download it and restart Home Assistant.

The domain (`ambientika`) and the entity IDs are unchanged, so existing entities, dashboards and automations keep working.

## Support

- Bugs and feature requests: [GitHub issues](https://github.com/ambientika-eu/HomeAssistant-integration-for-Ambientika/issues)
- Product questions: info@ambientika.eu
- Website: [www.ambientika.eu](https://www.ambientika.eu)

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License and credits

MIT License, see [LICENSE](LICENSE). This integration is based on the original community integration (`ambientika/HomeAssistant-integration-for-Ambientika`) and the integration blueprint by Joakim Sørensen. Thanks to everyone who contributed.

Contributions are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).
