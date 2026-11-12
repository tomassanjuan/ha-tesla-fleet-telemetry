# AGENTS.md

Home Assistant custom integration (`tesla_fleet_telemetry`, HACS): Tesla Fleet Telemetry over a self-hosted MQTT broker (push) plus signed commands via Tesla's `vehicle-command` proxy. See `docs/ARCHITECTURE.md` for design decisions before changing structure.

## Layout
- `custom_components/tesla_fleet_telemetry/`: the integration (one module per HA platform, plus `coordinator.py`, `mqtt_client.py`, `proxy_client.py`, `fleet_config.py`, `oauth2.py`).
- `tests/`: pytest, using `pytest-homeassistant-custom-component`.
- `docs/`: user docs (Setup, Usage, Troubleshooting, Security, Architecture). `CHANGELOG.md` at the root.
- `.github/cicd/`: tool config and requirements (ruff/mypy/pytest live in `.github/cicd/pyproject.toml`, not the repo root).

## Commands
```bash
pip install -r .github/cicd/requirements-dev.txt
pytest -c .github/cicd/pyproject.toml tests/
ruff check --config .github/cicd/pyproject.toml . && ruff format --config .github/cicd/pyproject.toml .
python .github/cicd/validate_files.py
```
CI also runs pre-commit (`.github/.pre-commit-config.yaml`), hassfest, HACS validation and pip-audit.

## Conventions
- Python 3.14, line length 100, ruff-formatted, async everywhere (no blocking I/O in the event loop).
- Entity `unique_id` must be VIN-scoped: use `telemetry_unique_id(vin, key)`. Multiple vehicles = multiple config entries.
- Telemetry fields are requested from Tesla only for enabled entities (`ENTITY_FIELD_MAPPINGS`); a new entity needs a mapping or it will never receive data.
- User-facing strings go in `strings.json` and `translations/`; keep them in sync.
- Dependency pins live in three places that must match: `manifest.json`, `.github/cicd/requirements-security.txt`, `.github/cicd/requirements-dev.txt` (plus `pyproject.toml`).
- Never log or commit secrets (tokens, MQTT credentials, VINs in diagnostics are redacted); see `docs/SECURITY.md`.
- Add a `CHANGELOG.md` entry for user-visible changes.
