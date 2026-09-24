# Changelog

## Unreleased

Fixed:

- TPMS warnings: per-wheel hard/soft warning sensors now read Tesla's `TpmsHardWarnings` / `TpmsSoftWarnings` fields (which report all four tyres together). The old per-wheel field names don't exist on Tesla's side and are no longer requested from the vehicle. The two combined TPMS sensors now show the correct on/off state.

Added:

- Battery sensors: Nominal Full Pack Energy, Lifetime Energy Charged, Lifetime Regen Energy, Brick SoC Min.
- Software updates: the Firmware entity uses the vehicle's `SoftwareUpdateAvailable` / `SoftwareUpdateInProgress` flags when reported.
- Remote Start Active, Sunroof, Motor Torque Command, and Rear Inverter DC Voltage sensors.

Changed:

- Entities refresh only when a telemetry field they read changes, instead of every entity refreshing on every telemetry burst.
- Burst updates flush at a fixed interval, so a continuous stream can no longer delay entity updates indefinitely.
- Commands that wake the vehicle retry as soon as telemetry reports it connected, instead of always waiting 12 seconds.
- MQTT messages are handed to Home Assistant in batches, rather than one event-loop wakeup per message.

## 1.0.0 - 2026-05-02

Initial release.

Added:

- Home Assistant integration for Tesla Fleet Telemetry.
- Live vehicle data through MQTT.
- Vehicle entities for charging, climate, location, locks, doors, media, tires, software updates, and general status.
- Remote commands for common Home Assistant controls.
- Tesla sign-in during setup.
- Multiple vehicle support.
- Diagnostics with sensitive values redacted.
- Unified user documentation:
  - [Setup](docs/SETUP.md)
  - [Usage](docs/USAGE.md)
  - [Troubleshooting](docs/TROUBLESHOOTING.md)
  - [Security and Privacy](docs/SECURITY.md)
