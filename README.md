# Ambientika for Home Assistant

Official Home Assistant integration for **Ambientika** decentralised ventilation units with heat recovery.
Developed and maintained by **Südwind GmbH** (SUEDWIND S.R.L.), Eppan/Appiano, South Tyrol – the manufacturer of Ambientika.

[![Open your Home Assistant instance and open this repository in the Marketplace or in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ambientika-eu&repository=HomeAssistant-integration-for-Ambientika&category=integration)

## Supported devices

All Ambientika units that are set up in the **Ambientika app** (for example SMART and OFFICE). The integration uses the same Ambientika cloud account as the app.

## Entities per unit

Since version 1.6 the integration shows the same values and follows the same rules as the [Ambientika MQTT Bridge](https://github.com/ambientika-eu/ambientika-mqtt-bridge).

| Platform | Entity | Description |
| --- | --- | --- |
| Climate | named like the unit | Operating mode, fan speed (Night and Turbo are shown, Low/Medium/High can be set), target humidity; shows the room temperature and the relative humidity |
| Select | Mode, Fan Speed, Humidity Level | The same values as single controls |
| Select | Dusk Sensor Sensitivity | Sensitivity of the dusk sensor that switches the unit to its night step: off, low, high. It is a setting, not a brightness reading |
| Sensor | Temperature, Humidity | Readings of the unit. A single impossible reading is held back; a saturated sensor (above 100 %) reads 100 % |
| Sensor | Temperature (averaged), Humidity (averaged) | Mean over a sliding window (30 minutes by default). The unit reverses its airflow about every half minute, so a single reading jumps; these values are steady enough for dashboards and thresholds |
| Sensor | Air Quality | Air quality level |
| Sensor | Filter Status | good / medium / bad |
| Sensor | Mode, Fan Speed, Humidity Level, Dusk Sensor Sensitivity | The values the unit reports. For a Slave, Mode is the mode of its zone Master, like in the app |
| Sensor | Last Mode | What the unit was doing before its current mode |
| Binary sensor | Humidity Alarm | Humidity alarm reported by the unit |
| Binary sensor | Night Mode | Night flag reported by the unit (named Night Alarm up to 1.5; same entity) |
| Button | Filter Reset | Resets the filter counter after a filter change. Its attributes say what the reset does on this unit |

Diagnostic entities: Mode raw and Filter Status raw (the unit's own values), Device Role, Zone, Last Status Packet and Failed Readings (both stay readable while the unit is unavailable). Disabled until you switch them on: Filter Reset Status, Humidity raw, and a number next to every text value ("(num)") for long-term statistics.

Every entity is tied to the serial number of its unit. A unit can be renamed in the Ambientika app, and two units can carry the same name, without entities being lost or created twice.

The status of every unit is read from the Ambientika cloud once every 30 seconds and shared by all its entities. A unit keeps its last values until three readings in a row have failed; then it is shown as unavailable until it answers again, and the log says so once.

### Master and Slaves

- A Slave runs with the Master of its zone. Set the mode on the Master. A mode set on a Slave is sent to it, but the Slave keeps following its Master.
- The filter reset goes to the unit and to the Master of its zone, and the status of the unit is read again to see whether the counter cleared. A Slave keeps its own filter counter, which the cloud cannot clear; it clears with a factory reset of the unit. With the option *Record the filter maintenance of Slaves* the reset records the maintenance instead: Filter Status reads good, Filter Status raw keeps the unit's own value.

### Commands

Commands for the same unit within 0.8 seconds are sent in one call, so an automation can set the mode and then the fan speed. The fan steps Night and Turbo are chosen by the unit itself; selecting one sets the nearest step.

## Options

**Settings → Devices & services → Ambientika → Configure** has three pages.

**General** offers the options of the Ambientika MQTT Bridge under the same names: reading interval, failed readings until a unit is unavailable, time to collect commands, window and hysteresis of the averaged values (also per unit, as `Name=value`), and the filter maintenance of Slaves.

## NeuraCell-X: radon protection and dew-point control

NeuraCell-X works like in the Ambientika MQTT Bridge. Where the bridge reads MQTT topics, the integration reads Home Assistant entities, which you choose on the pages **NeuraCell-X radon protection** and **NeuraCell-X dew-point control**. Without a source nothing happens and no entities are added.

- **Radon protection.** Choose one or more sensors with the radon value in Bq/m³, or an entity that is on while a radon alarm is active. While the value is at or above the threshold (300 Bq/m³ by default), every unit runs in Intake at the chosen fan speed. Once the value is back at the threshold minus the hysteresis, every unit gets back the mode it had before.
- **Dew-point control.** Choose an entity that is on while ventilating must be blocked, or the entity of the dew-point controller (on while it ventilates), or four sensors (temperature and humidity indoors and outdoors) from which the block is worked out. While the block is on, the selected units are switched off; afterwards they get their modes back.
- Radon protection has priority over the dew-point block.
- A mode you set while protection holds a unit is carried out when protection clears.
- The modes the units return to are kept over a restart of Home Assistant.
- A source that becomes unavailable counts as disconnected. If the block holds the units off while dew-point controller and radon sensor are both disconnected for 30 minutes, the blocked units run heat recovery at a low step until one of them reports again.
- Radon meter and dew-point controller can also be read from the Ambientika cloud by their serial number (source "device"). Such a device is then only read and not shown as a ventilation unit.

Entities of the device **NeuraCell-X**: Radon Protection Active, Radon Level, Radon Meter Connected, Ventilation Blocked (Dew Point), Dew-Point Controller Connected, Fallback Ventilation Active, Dew Point Indoor and Outdoor. Only those that belong to the chosen sources are created.

## Languages

The setup dialog, the options, the names of the entities and their states are available in Czech, Danish, Dutch, English, French, German, Italian, Polish, Portuguese (Portugal), Spanish and Swedish. Home Assistant picks the language from its own settings. Entity IDs are built once, when the integration is set up, and do not change with the language.

## Installation

Ambientika for Home Assistant is listed in the default repositories of HACS. Search for it, or use the button at the top of this page to open it in your Home Assistant.

### Home Assistant 2026.11 and newer: Marketplace

1. Go to **Settings → Marketplace** and search for **Ambientika for Home Assistant**.
2. Open it and choose **Install**. Restart Home Assistant if it asks for it.

### Home Assistant up to 2026.10: HACS

1. Open HACS and search for **Ambientika for Home Assistant**.
2. Download it and restart Home Assistant.

For a while the search may also show **HomeAssistant integration for Ambientika**. That is the previous repository with version 1.1.0, which is no longer maintained; choose **Ambientika for Home Assistant**.

If the search does not find the integration yet, add the repository by its link: in the Marketplace with **Add from link**, in HACS with **Custom repositories** (three dots, top right), each with `https://github.com/ambientika-eu/HomeAssistant-integration-for-Ambientika` and the type **Integration**.

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
2. Search for **Ambientika for Home Assistant** as described above, download it and restart Home Assistant.

Entities, entity IDs, names, areas, dashboards and automations stay as they are.

### With the Marketplace (Home Assistant 2026.11 and newer)

The Marketplace uninstalls an integration only together with its setup, and Home Assistant keeps the entries of the deleted entities for 30 days. From version 1.5.0 the integration takes these entries over when it is set up again.

1. In the Marketplace open **HomeAssistant integration for Ambientika** under **Installed**. In its three-dot menu choose **Uninstall** and then **Delete and uninstall**. Restart Home Assistant.
2. Search for **Ambientika for Home Assistant** as described above and install it.
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
