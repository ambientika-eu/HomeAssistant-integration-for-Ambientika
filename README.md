# Ambientika for Home Assistant

Official Home Assistant integration for **Ambientika** decentralised ventilation units with heat recovery.
Developed and maintained by **Südwind GmbH** (SUEDWIND S.R.L.), Eppan/Appiano, South Tyrol – the manufacturer of Ambientika.

[![Open your Home Assistant instance and open this repository in the Marketplace or in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ambientika-eu&repository=HomeAssistant-integration-for-Ambientika&category=integration)

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

## Languages

The setup dialog, the names of the entities and the states of air quality and filter status are available in Czech, Danish, Dutch, English, French, German, Italian, Polish, Portuguese (Portugal), Spanish and Swedish. Home Assistant picks the language from its own settings. Entity IDs are built once, when the integration is set up, and do not change with the language.

## Installation

The button at the top of this page opens this repository in your Home Assistant, in the Marketplace or in HACS.

### Home Assistant 2026.11 and newer: Marketplace

1. Go to **Settings → Marketplace** and choose **Add from link**.
2. Enter `https://github.com/ambientika-eu/HomeAssistant-integration-for-Ambientika` as **GitHub link** and select **Add**. The first time, Home Assistant asks you to connect a GitHub account. If it asks for the type, choose **Integration**.
3. Search the Marketplace for **Ambientika for Home Assistant**, open it and choose **Install**. Restart Home Assistant if it asks for it.

### Home Assistant up to 2026.10: HACS

1. In HACS open the menu (three dots, top right) and choose **Custom repositories**.
2. Add `https://github.com/ambientika-eu/HomeAssistant-integration-for-Ambientika` with the type **Integration**.
3. Search for **Ambientika for Home Assistant**, download it and restart Home Assistant.

### Manual

Copy the folder `custom_components/ambientika` into `config/custom_components/ambientika` of your Home Assistant installation and restart Home Assistant.

## Configuration

Go to **Settings → Devices & services → Add integration**, search for **Ambientika** and sign in with the same e-mail address and password as in the Ambientika app. All units of the account are added automatically. In the Marketplace, **Set up** leads to the same dialog once the integration is installed.

## Updating

Update in the Marketplace or in HACS and restart Home Assistant. Up to version 1.2 the entities were tied to the name of the unit; version 1.3 moves them to the serial number at its first start. Entity IDs, your own names, areas, dashboards, automations and the history stay as they are. Do not go back to 1.2.x afterwards without a backup, because the old version creates all entities a second time.

If Home Assistant starts while the Ambientika cloud cannot be reached, the integration tries again by itself; nothing has to be reloaded.

## Switching from the previous repository

The integration was previously published at `ambientika/HomeAssistant-integration-for-Ambientika`, where it is listed as **HomeAssistant integration for Ambientika**. That repository is no longer maintained. Both use the domain `ambientika`, so only one of them can be installed at a time.

### With HACS (Home Assistant up to 2026.10)

1. In HACS remove **HomeAssistant integration for Ambientika**. Leave the integration under **Settings → Devices & services** as it is.
2. Add this repository as described above, download **Ambientika for Home Assistant** and restart Home Assistant.

Entities, entity IDs, names, areas, dashboards and automations stay as they are.

### With the Marketplace (Home Assistant 2026.11 and newer)

The Marketplace uninstalls an integration only together with its setup, and Home Assistant keeps the entries of the deleted entities for 30 days. From version 1.5.0 the integration takes these entries over when it is set up again.

1. In the Marketplace open **HomeAssistant integration for Ambientika** under **Installed**. In its three-dot menu choose **Uninstall** and then **Delete and uninstall**. Restart Home Assistant.
2. Add this repository as described above and install **Ambientika for Home Assistant**.
3. Choose **Set up** and sign in with the same e-mail address and password as in the Ambientika app.

The entities come back with their entity IDs and with the names, icons, areas and labels you gave them, so dashboards and automations keep working. Two units that carry the same name in the Ambientika app get new entities, apart from the climate entity.

## Support

- Bugs and feature requests: [GitHub issues](https://github.com/ambientika-eu/HomeAssistant-integration-for-Ambientika/issues)
- Product questions: info@ambientika.eu
- Website: [www.ambientika.eu](https://www.ambientika.eu)

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## License and credits

MIT License, see [LICENSE](LICENSE). This integration is based on the original community integration (`ambientika/HomeAssistant-integration-for-Ambientika`) and the integration blueprint by Joakim Sørensen. Thanks to everyone who contributed.

Contributions are welcome, see [CONTRIBUTING.md](CONTRIBUTING.md).
