# Changelog

## 1.2.1 – 2026-11-16

- Repository maintained by Südwind GmbH under `ambientika-eu`; README rewritten (supported devices, entities, installation, switching from the previous repository).
- Setup dialog: explains that the Ambientika app credentials are used.
- Unified integration name "Ambientika for Home Assistant".

## 1.2.0 – 2026-11-11

- Support for the fan speeds Night and Turbo reported by the Ambientika cloud. Before, a unit in Night or Turbo created only 3 of its 6 entities.
- Changing the fan speed in SMART mode keeps the SMART mode.
- Unknown values from the cloud no longer stop the status update; the entity is shown as unavailable until the next update.
- Entity IDs and unique IDs unchanged.
