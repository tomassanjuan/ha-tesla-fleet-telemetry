<p align="center">
  <img src="custom_components/tesla_fleet_telemetry/brand/icon.png" alt="Tesla Fleet Telemetry (Self-Hosted) icon" width="128" height="128">
</p>

<h1 align="center">Tesla Fleet Telemetry (Self-Hosted)</h1>

<p align="center">
  Live Tesla telemetry and signed remote commands in Home Assistant,<br>
  over your own MQTT broker and your own infrastructure.
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS custom repository"></a>
  <img src="https://img.shields.io/badge/Home%20Assistant-2026.1+-brightgreen" alt="Home Assistant 2026.1+">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-blue.svg" alt="MIT licence"></a>
</p>

> [!IMPORTANT]
> **With thanks to Teslemetry and Bre77.** This project exists because of [Teslemetry](https://teslemetry.com) and the work of [Bre77](https://github.com/Bre77): it was inspired by their product, and it is built on their open-source [`tesla-fleet-api`](https://github.com/Teslemetry/python-tesla-fleet-api) library. This is not a competitor. If you want a simpler, easier setup with more control and less infrastructure, use Teslemetry. See [Alternatives and Credits](#alternatives-and-credits).

> [!WARNING]
> **Experimental software (v0.1).** This integration is early, mostly AI-assisted ("vibe-coded"), and may be broken. Expect bugs and breaking changes between releases. It can send commands to your vehicle (lock, climate, charging, trunk, windows, horn), so test only with the car parked somewhere safe. Use at your own risk, as stated in the [MIT licence](LICENSE).

**Tesla Fleet Telemetry (Self-Hosted)** connects Tesla vehicles to Home Assistant with live telemetry and signed remote commands. It is MQTT-based and runs on your own infrastructure.

It is meant for Home Assistant users who want a reliable local setup: Home Assistant receives vehicle state through MQTT, while Tesla's official companion services handle command signing and telemetry delivery.

This is a custom integration. It is not part of Home Assistant Core and is not affiliated with or endorsed by Tesla.

## What It Provides

- Live entities for charging, climate, location, doors, locks, media, tire pressure, software updates, and vehicle status.
- Remote commands for wake, lock, climate, charging, charge port, trunk, windows, media, lights, and horn.
- Efficient telemetry based on the entities you enable.
- Multiple vehicle support.
- Redacted diagnostics for safer support.

## What You Need

- Home Assistant 2026.1 or newer.
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
- [Architecture](docs/ARCHITECTURE.md): component layout and data flow. Historical audit notes live in [docs/dev](docs/dev/).

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
2. Install **Tesla Fleet Telemetry (Self-Hosted)** from HACS (HACS menu > Custom repositories > paste this repository URL).
3. Restart Home Assistant.
4. Add the integration from **Settings > Devices & services**.

### Manual

1. Copy `custom_components/tesla_fleet_telemetry` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Add the integration from **Settings > Devices & services**.

## Supported Platforms

The integration currently provides `sensor`, `binary_sensor`, `device_tracker`, `climate`, `select`, `cover`, `lock`, `switch`, `number`, `button`, `media_player`, and `update` entities.

## Development

Use Python 3.12 or newer (CI runs 3.14). CI configuration lives under `.github/cicd`. There is no build step; lint, type checks and tests run through CI tooling:

```bash
python -m pip install -r .github/cicd/requirements-dev.txt
python .github/cicd/validate_files.py
pytest -c .github/cicd/pyproject.toml tests
ruff check --config .github/cicd/pyproject.toml custom_components/tesla_fleet_telemetry/
ruff format --config .github/cicd/pyproject.toml --check custom_components/tesla_fleet_telemetry/
mypy --config-file .github/cicd/pyproject.toml custom_components/tesla_fleet_telemetry/
```

The integration icon is `custom_components/tesla_fleet_telemetry/brand/icon.png`.

Release notes are tracked in [CHANGELOG.md](CHANGELOG.md).

## Important Notes

- Do not expose MQTT anonymously to the internet.
- Keep private keys, Tesla secrets, refresh tokens, MQTT passwords, and TLS keys private.
- Use a stable public hostname for telemetry.
- Enable only the entities you use.
- Test remote commands only when the vehicle is parked somewhere safe.

Before opening support issues, remove tokens, secrets, full VINs, exact coordinates, private hostnames, and account-specific URLs from logs and screenshots.

## Alternatives and Credits

This project is not a competitor to the hosted options below. It is a self-hosted setup for people who want to run the Tesla proxy, telemetry receiver and MQTT themselves. If you want a simpler, easier setup with more control and less infrastructure, use one of these instead:

- [Teslemetry](https://teslemetry.com) and [Bre77](https://github.com/Bre77): hosted service with its own Home Assistant integration. It was the main inspiration for this project, and its open-source `tesla-fleet-api` library is used here.
- Home Assistant Core's built-in [Tesla Fleet](https://www.home-assistant.io/integrations/tesla_fleet/) and [Tessie](https://www.home-assistant.io/integrations/tessie/) integrations.

Thank you to the authors of all of these, and to Tesla for the public [Fleet API](https://developer.tesla.com/docs) and [Fleet Telemetry](https://github.com/teslamotors/fleet-telemetry) projects.

## License

MIT, see [LICENSE](LICENSE). Dependency licences and trademark notes are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
