# Changelog

## 1.1.0 - Unreleased

Added:

- Battery sensors: Nominal Full Pack Energy, Lifetime Energy Charged, Lifetime Regen Energy, Brick SoC Min.
- Software updates: the Firmware entity uses the vehicle's `SoftwareUpdateAvailable` / `SoftwareUpdateInProgress` flags when reported.
- Remote Start Active, Sunroof, Motor Torque Command, and Rear Inverter DC Voltage sensors.

Changed:

- Setup/options forms now do a real MQTT handshake (credentials and TLS), reporting rejected credentials or TLS failures at the form instead of looping "Not authorized" after setup.
- The application domain is validated locally (bare DNS name) before calling Tesla.
- Entities refresh only when a telemetry field they read changes, instead of every entity refreshing on every telemetry burst.
- Burst updates flush at a fixed interval, so a continuous stream can no longer delay entity updates indefinitely.
- Commands that wake the vehicle retry as soon as telemetry reports it connected, instead of always waiting 12 seconds.
- MQTT messages are handed to Home Assistant in batches, rather than one event-loop wakeup per message.
- Requires `tesla-fleet-api` 1.17.2 or newer (below 2.0), and no longer touches the library's private attributes or methods.
- Error handling catches only the expected network, Tesla API and certificate errors, so unexpected bugs are no longer silently logged and ignored.
- Documented minimum Home Assistant version corrected to 2026.1 (matching `hacs.json`).
- The telemetry server CA pushed to the vehicle is now only the certificate that issued the server's leaf (for example Let's Encrypt YE2), not the leaf plus the whole chain. Verified on a real vehicle. Existing entries switch to it on the next reload.
- Blocking-problem repairs have a single Submit action that reloads the integration and says whether the stored CA changed. The separate Retry action is removed because it re-pushed the stale stored CA.

Internal:

- Entity/field descriptions moved from `const.py` to `descriptions.py`; config-flow helpers moved to `flow_helpers.py`.
- Test files renamed by feature; CI enforces a minimum coverage of 50%.
- Audit notes moved to `docs/dev/`, architecture notes to `docs/ARCHITECTURE.md`.

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
