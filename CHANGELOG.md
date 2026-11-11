# Changelog

## 0.1.0 - 2026-11-11

First public release.

- Live vehicle telemetry in Home Assistant over MQTT: charging, climate, location, doors, locks, media, tire pressure, software updates and vehicle status.
- Signed remote commands through Tesla's Vehicle Command proxy: wake, lock, climate, charging, charge port, trunk, windows, media, lights and horn.
- Tesla OAuth through Home Assistant Application Credentials, with Fleet API region detection.
- Telemetry fields are requested only for the entities you enable.
- Config and options flows check MQTT credentials and TLS, and the application domain, before saving.
- Repairs for blocking Fleet Telemetry errors, with a single action that reloads the integration.
- Multiple vehicles, and redacted diagnostics.
