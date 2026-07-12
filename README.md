# Tesla Fleet Telemetry for Home Assistant

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)
![Home Assistant](https://img.shields.io/badge/Home%20Assistant-2024.1+-brightgreen)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

Tesla Fleet Telemetry connects Tesla vehicles to Home Assistant with live telemetry and signed remote commands.

It is meant for Home Assistant users who want a reliable local setup: Home Assistant receives vehicle state through MQTT, while Tesla's official companion services handle command signing and telemetry delivery.

This is a custom integration. It is not part of Home Assistant Core and is not affiliated with or endorsed by Tesla.

## What It Provides

- Live entities for charging, climate, location, doors, locks, media, tire pressure, software updates, and vehicle status.
- Remote commands for wake, lock, climate, charging, charge port, trunk, windows, media, lights, and horn.
- Efficient telemetry based on the entities you enable.
- Multiple vehicle support.
- Redacted diagnostics for safer support.

## What You Need

- Home Assistant 2024.1 or newer.
- A Tesla account with access to the vehicle.
- Tesla application credentials and vehicle permissions.
- A public hostname for the telemetry receiver.
- TLS certificates for the public services.
- MQTT reachable by Home Assistant and the telemetry receiver.
- Tesla Vehicle Command proxy and Fleet Telemetry receiver running on your own infrastructure.

## Documentation

The docs are compact but complete:

- [Setup](docs/SETUP.md): infrastructure, install flow, certificates, pairing, and validation.
- [Usage](docs/USAGE.md): entities, commands, telemetry modes, automations, and maintenance.
- [Troubleshooting](docs/TROUBLESHOOTING.md): fast checks, common errors, and recovery runbooks.
- [Security and Privacy](docs/SECURITY.md): secrets, MQTT, certificates, commands, and location data.

## Basic Flow

1. Create Tesla application credentials and key material.
2. Host the public key on your registered domain.
3. Run the Vehicle Command proxy.
4. Run Fleet Telemetry with MQTT output.
5. Pair the virtual key with the vehicle.
6. Install the Home Assistant integration.
7. Add the integration, sign in with Tesla, select the vehicle, and enter MQTT, proxy, and telemetry settings.

## Installation

### HACS

1. In HACS, add this repository as a custom repository with category `Integration`.
2. Install **Tesla Fleet Telemetry** from HACS.
3. Restart Home Assistant.
4. Add the integration from **Settings > Devices & services**.

### Manual

1. Copy `custom_components/tesla_fleet_telemetry` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings > Devices & services**.

## Supported Platforms

The integration currently provides `sensor`, `binary_sensor`, `device_tracker`, `climate`, `select`, `cover`, `lock`, `switch`, `number`, `button`, `media_player`, and `update` entities.

## Development

Use Python 3.12 or newer. CI configuration lives under `.github/cicd`.

```bash
python -m pip install -r .github/cicd/requirements-dev.txt
python .github/cicd/validate_files.py
pytest -c .github/cicd/pyproject.toml tests
```

The HACS brand icon is `brand/icon.png`. The SVG source artwork lives in `assets/`.

Release notes are tracked in [CHANGELOG.md](CHANGELOG.md).

## Important Notes

- Do not expose MQTT anonymously to the internet.
- Keep private keys, Tesla secrets, refresh tokens, MQTT passwords, and TLS keys private.
- Use a stable public hostname for telemetry.
- Enable only the entities you use.
- Test remote commands only when the vehicle is parked somewhere safe.

Before opening support issues, remove tokens, secrets, full VINs, exact coordinates, private hostnames, and account-specific URLs from logs and screenshots.
