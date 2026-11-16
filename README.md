# Ambientika for Home Assistant

Official Home Assistant integration for **Ambientika** decentralised ventilation units with heat recovery.
Developed and maintained by **Südwind GmbH** (SUEDWIND S.R.L.), Eppan/Appiano, South Tyrol – the manufacturer of Ambientika.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ambientika-eu&repository=HomeAssistant-integration-for-Ambientika&category=integration)

## Supported devices

All Ambientika units that are set up in the **Ambientika app** (for example SMART and OFFICE). The integration uses the same Ambientika cloud account as the app.

## Entities per unit

| Platform | Entity | Description |
| --- | --- | --- |
| Climate | Ventilation | Operating mode, fan speed (Night and Turbo are shown, Low/Medium/High can be set), target humidity |
| Sensor | Temperature | Room temperature |
| Sensor | Humidity | Relative humidity |
| Sensor | Air quality | Air quality level |
| Sensor | Filter status | good / medium / bad |
| Binary sensor | Humidity alarm | Humidity alarm reported by the unit |
| Binary sensor | Night alarm | Night signal reported by the unit |
| Button | Filter reset | Resets the filter counter after a filter change |

## Installation

### HACS (recommended)

1. In HACS open the menu (three dots, top right) and choose **Custom repositories**.
2. Add `https://github.com/ambientika-eu/HomeAssistant-integration-for-Ambientika` with the type **Integration**.
3. Search for **Ambientika for Home Assistant**, download it and restart Home Assistant.

### Manual

Copy the folder `custom_components/ambientika` into `config/custom_components/ambientika` of your Home Assistant installation and restart Home Assistant.

## Configuration

Go to **Settings → Devices & services → Add integration**, search for **Ambientika** and sign in with the same e-mail address and password as in the Ambientika app. All units of the account are added automatically.

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
