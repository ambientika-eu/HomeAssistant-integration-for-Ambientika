# Changelog

## 1.5.1 – 2026-11-20

Icon and logo

- The integration ships its own icon and logo in the folder `brand`, for the light and the dark theme. Home Assistant shows them wherever the integration appears, for example in the list of integrations and in the setup dialog.

Functions, entities, entity IDs and unique IDs of existing installations are unchanged. Going back to 1.5.0 is possible at any time.

## 1.5.0 – 2026-11-18

Setting up again

- When the integration was deleted and is set up again, its entities take over the entries Home Assistant kept of the earlier setup, also of a setup made with version 1.2 or older. They come back with their entity IDs; current Home Assistant versions also bring back the names, icons, areas and labels you gave them. Home Assistant keeps these entries for 30 days.
- Two units that carry the same name in the Ambientika app get new entities in that case, apart from the climate entity.

Installation

- The README describes the installation with the Marketplace of Home Assistant 2026.11 and newer and with HACS for older versions, and the switch from the previous repository for both.

Functions, entities, entity IDs and unique IDs of existing installations are unchanged. Going back to 1.4.1 is possible at any time.

## 1.4.1 – 2026-11-18

Languages

- The setup dialog, the names of the entities and the states of air quality and filter status are shown in nine more languages: Czech, Danish, Dutch, French, Italian, Polish, Portuguese (Portugal), Spanish and Swedish. English and German stay as they are.
- Home Assistant picks the language from its own settings. Existing installations keep their entity IDs; a new installation builds them from the names in its language.

Functions, entities, entity IDs and unique IDs of existing installations are unchanged. Going back to 1.4.0 is possible at any time.

## 1.4.0 – 2026-11-17

One reading per unit

- The status of a unit is read once every 30 seconds and shared by all its entities: two requests per unit and minute. The values of the climate entity now arrive every 30 seconds, like those of the sensors.
- At the start the integration signs in once and reads the status of every unit once.
- Updating several entities of a unit with `homeassistant.update_entity` asks the cloud once for that unit.

Reliability

- A request the cloud accepts but does not answer ends after 20 seconds. That unit is shown as unavailable until it answers again; the other units are read on time.
- A unit that stops answering, for example because it is switched off, is reported in the log once.

Entities, entity IDs, unique IDs and names are unchanged. Going back to 1.3.0 is possible at any time.

## 1.3.0 – 2026-11-17

Unique IDs

- The unique IDs of all entities are built from the serial number of the unit. Existing entities are moved automatically at the first start with this version: entity IDs, your own names and icons, areas, hidden and disabled entities, dashboards, automations and the history stay as they are.
- Two units with the same name: both get all their entities.
- Renaming a unit in the app: the device takes the new name, the entities stay.

Reliability

- Home Assistant started while the Ambientika cloud could not be reached (for example after a power cut, when the router is not up yet): the integration tries again by itself until the cloud answers.
- The sign-in of the Ambientika cloud expires after a while. The integration signs in again by itself.
- If the cloud does not accept user name and password, Home Assistant shows this as the reason.
- An account that is already set up cannot be added a second time.
- The setup dialog tells a wrong password apart from a cloud that cannot be reached.
- Adapted to the interfaces of current Home Assistant versions (connection class of the setup dialog, temperature properties of the climate entity).

Names

- The climate entity is named like the unit ("Flur"). The filter reset button has a name of its own ("Flur Filter Reset").
- The entity IDs of an existing installation do not change. A new installation gets `climate.flur` and `button.flur_filter_reset`.
- While the status of a unit cannot be read, its binary sensors are shown as unavailable like its other entities.

Note

- Do not go back to 1.2.x after this update without a backup: the old version does not know the new IDs and creates all entities a second time.

## 1.2.1 – 2026-11-16

- Repository maintained by Südwind GmbH under `ambientika-eu`; README rewritten (supported devices, entities, installation, switching from the previous repository).
- Setup dialog: explains that the Ambientika app credentials are used.
- Unified integration name "Ambientika for Home Assistant".

## 1.2.0 – 2026-11-11

- Support for the fan speeds Night and Turbo reported by the Ambientika cloud; every unit is set up with all of its entities.
- Changing the fan speed in SMART mode keeps the SMART mode.
- With an unknown value from the cloud, the entity is shown as unavailable until the next update.
- Entity IDs and unique IDs unchanged.
