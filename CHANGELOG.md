# Changelog

## Unreleased

- New telemetry entities (all disabled by default): front, rear-left and rear-right drive-unit sensors, slave motor torque command, scheduled departure time, power transfer status, self-driving software version, Cybertruck tonneau sensors, Powershare sensors (Cybertruck and 2026+ Model 3/Y), and Semi tire pressure, park brake, cruise, mass and port sensors.
- Model-specific entities are created only for the matching vehicle, detected from the car type: Cybertruck-only (tonneau), Semi-only, and rear-left/right motor sensors (Model S, Model X, Cybertruck). Until the model is known they are skipped, and the integration reloads once it is reported. Front-motor sensors can't be detected (no field reports AWD), so RWD cars still get them; leave them disabled.
- Options: "Hide front drive-unit sensors" and "Hide Powershare sensors" checkboxes, both on by default, so those entities are not created. Untick them to get the entities (e.g. on an AWD car, or if you use Powershare/PCS2).
- Fixed the `Accessory Rail` binary sensor showing no icon: it used `mdi:rail`, which doesn't exist in Material Design Icons. It now uses `mdi:power-plug`.
- Speed Limit number entity to set Speed Limit Mode's maximum speed (`speed_limit_set_limit`). It replaces the read-only "Current Speed Limit" sensor.
- Removed the duplicate `Remote Start Active` binary sensor; the `Remote Start` lock already reports the same state. Delete the orphaned entity from the registry if it remains.

## 0.1.0 - 2026-11-11

First public release.

- Live vehicle telemetry in Home Assistant over MQTT: charging, climate, location, doors, locks, media, tire pressure, software updates and vehicle status.
- Signed remote commands through Tesla's Vehicle Command proxy: wake, lock, climate, charging, charge port, trunk, windows, media, lights and horn.
- Tesla OAuth through Home Assistant Application Credentials, with Fleet API region detection.
- Telemetry fields are requested only for the entities you enable.
- Config and options flows check MQTT credentials and TLS, and the application domain, before saving.
- Repairs for blocking Fleet Telemetry errors, with a single action that reloads the integration.
- Multiple vehicles, and redacted diagnostics.
