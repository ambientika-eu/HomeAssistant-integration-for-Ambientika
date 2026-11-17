# Changelog

## 1.3.0 – 2026-11-17

Unique IDs

- The unique IDs of all entities are built from the serial number of the unit instead of its name in the Ambientika app. Existing entities are moved automatically at the first start with this version: entity IDs, your own names and icons, areas, hidden and disabled entities, dashboards, automations and the history stay as they are.
- Two units with the same name: both get all their entities. Before, the second unit got only its climate entity.
- Renaming a unit in the app no longer leaves the old entities behind and creates new ones. The device takes the new name, the entities stay.

Reliability

- Home Assistant started while the Ambientika cloud could not be reached (for example after a power cut, when the router is not up yet): the integration tries again by itself until the cloud answers. Before, it stayed failed until it was reloaded by hand.
- The sign-in of the Ambientika cloud expires after a while. The integration signs in again by itself. Before, all units stayed unavailable until the integration was reloaded or Home Assistant restarted.
- If the cloud does not accept user name and password, Home Assistant shows this as the reason.
- An account that is already set up cannot be added a second time; that created every entity twice.
- The setup dialog tells a wrong password apart from a cloud that cannot be reached.
- Current versions of Home Assistant no longer log deprecation warnings about the integration (connection class of the setup dialog, temperature properties of the climate entity).

Names

- The climate entity is named like the unit ("Flur") and no longer carries the name twice ("Flur Flur"). The filter reset button has a name of its own ("Flur Filter Reset"); before, it was shown under the name of the unit only.
- The entity IDs of an existing installation do not change. A new installation gets `climate.flur` and `button.flur_filter_reset`.
- While the status of a unit cannot be read, its binary sensors are shown as unavailable like its other entities (before: unknown).

Note

- Do not go back to 1.2.x after this update without a backup: the old version does not know the new IDs and creates all entities a second time.

## 1.2.1 – 2026-11-16

- Repository maintained by Südwind GmbH under `ambientika-eu`; README rewritten (supported devices, entities, installation, switching from the previous repository).
- Setup dialog: explains that the Ambientika app credentials are used.
- Unified integration name "Ambientika for Home Assistant".

## 1.2.0 – 2026-11-11

- Support for the fan speeds Night and Turbo reported by the Ambientika cloud. Before, a unit in Night or Turbo created only 3 of its 6 entities.
- Changing the fan speed in SMART mode keeps the SMART mode.
- Unknown values from the cloud no longer stop the status update; the entity is shown as unavailable until the next update.
- Entity IDs and unique IDs unchanged.
