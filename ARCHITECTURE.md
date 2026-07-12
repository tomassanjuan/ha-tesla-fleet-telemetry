# Architecture — Tesla Fleet Telemetry (Home Assistant integration)

This documents the as-built design after the 2026-07-12 audit/fix pass (see
`DEFECT_LIST.md`). The architecture itself was already sound; this file records the
design decisions so they don't need to be re-derived from the code.

## Domain & manifest

- Domain: `tesla_fleet_telemetry`. `integration_type: hub`, `iot_class: local_push`
  (telemetry arrives via a self-hosted MQTT broker on the LAN, not Tesla's cloud — push,
  not polling).
- `dependencies: [application_credentials, auth]` — OAuth client id/secret are supplied
  via HA's `application_credentials` platform, not typed into the config flow.
- One config entry per vehicle (VIN), config-entry `unique_id` = VIN
  (`config_flow.py:997`), so multiple vehicles are multiple config entries. Entity
  `unique_id`s are `telemetry_{vin}_{field}` — VIN-scoped, since HA's entity registry
  keys entities by `(domain, platform, unique_id)` across the whole instance, not per
  config entry.

## Deployment model — what runs where

This integration does **not** run Tesla's telemetry/proxy infrastructure itself. The
user runs, outside of Home Assistant, three components (documented in `docs/SETUP.md`,
typically via Docker Compose):

1. **`vehicle-command` HTTP proxy** — Tesla's official signed-command relay. HA talks to
   it over HTTPS (self-signed cert, hence `_INSECURE_SSL` in `proxy_client.py` — a
   deliberate choice for a LAN-only hop, not an oversight). Cert/key generation, rotation,
   and process lifecycle are entirely the user's responsibility; this integration is a
   pure async HTTP client of it (`proxy_client.py`), never spawns or manages it as a
   subprocess.
2. **`tesla/fleet-telemetry` server** — Tesla's official telemetry receiver. It receives
   the vehicle's protobuf-over-mTLS stream and republishes each field as a JSON-encoded
   MQTT message under a configurable `topic_base` (default `tesla`, must match
   `CONF_MQTT_TOPIC_BASE`, added in this pass so a mismatched user deployment doesn't
   silently receive nothing).
3. **MQTT broker** — this project's `iot` compose stack runs `mosquitto` on the LAN (see
   parent `CLAUDE.md`); the fleet-telemetry server and Home Assistant both connect to it.

Home Assistant's job is purely: OAuth with Tesla, push the vehicle's
`fleet_telemetry_config` (hostname/port/CA/fields/alerts) so the vehicle knows to stream,
subscribe to the resulting MQTT topics, and issue signed commands through the proxy.

## OAuth2 / application_credentials

- `application_credentials.py` implements `async_get_auth_implementation` and
  `async_get_authorization_server` per HA convention — client id/secret come from a
  user-registered `ClientCredential`, never hardcoded.
- `oauth2.py`'s `TeslaOAuth2Implementation` subclasses HA's
  `LocalOAuth2ImplementationWithPkce` (Tesla's Fleet API OAuth requires PKCE S256; plain
  `LocalOAuth2Implementation` does not send a `code_verifier`/`code_challenge` — this was
  the one real security-relevant gap found in the audit, now fixed). Tesla-specific extra
  params (`scope`, `audience`, `prompt_missing_scopes`, etc.) are merged on top of the
  PKCE base's `extra_authorize_data`/`extra_token_resolve_data` via `super()`.
- Token storage/refresh goes through HA's own
  `config_entry_oauth2_flow.OAuth2Session.async_ensure_token_valid()` — no hand-rolled
  refresh logic, no blocking calls.
- `ConfigEntryAuthFailed` is reserved for actual credential failures (401/403 on refresh,
  revoked tokens) and triggers HA's reauth flow via `entry.async_start_reauth`. Plain
  configuration problems (bad MQTT port, missing proxy URL) raise `ConfigEntryError`
  instead — a reauth prompt wouldn't fix those, so they shouldn't imitate one.
- Scopes (`const.OAUTH_SCOPES`): `openid offline_access user_data vehicle_device_data
  vehicle_location vehicle_cmds vehicle_charging_cmds` — covers everything the integration
  actually uses; `offline_access` is present so refresh tokens are issued.

## Data flow: push coordinator

`coordinator.py`'s `TelemetryCoordinator` extends `DataUpdateCoordinator` but sets no
`update_interval` and its `_async_update_data` is a documented no-op — it exists only to
satisfy the base class. Real updates come from `mqtt_client.py`'s message callback calling
`coordinator.async_update_field(field, value)` synchronously, which:

1. Validates the field against `FIELD_DEFS`.
2. Merges it into `self.data`.
3. Fires any per-field listeners (`async_add_field_listener`) immediately.
4. Schedules a single debounced `async_update_listeners()` call (50ms burst window) so a
   flood of fields from one MQTT payload triggers one HA state-write cycle, not dozens.

This is the correct shape for push-based telemetry — polling-style coordinators would be
wrong here since there's nothing to poll.

## MQTT ingestion

`mqtt_client.py`'s `TeslaMQTTClient` wraps `paho-mqtt` (sync library) for asyncio
correctly: `loop_start()` runs paho's own network thread, and every callback
(`_on_connect`, `_on_disconnect`, `_on_mqtt_message`) marshals back onto the HA event loop
via `hass.loop.call_soon_threadsafe(...)` rather than touching HA state from the paho
thread directly. Connect/disconnect are awaited via a future (`_connect_waiter`) instead
of a blocking `loop_forever()`. Reconnection uses exponential backoff
(`MQTT_BACKOFF_BASE_SECONDS` → `MQTT_BACKOFF_MAX_SECONDS`).

Topic layout (topic_base configurable, see above):
`{topic_base}/{vin}/v/{field}`, `{topic_base}/{vin}/connectivity`,
`{topic_base}/{vin}/alerts/{name}/current` (the `/history` variant is intentionally
ignored — it's a replay of a past alert, not live state, and previously corrupted the
`LastAlertTriggered` sensor), `{topic_base}/{vin}/errors/{name}`.

## Vehicle commands

`command_entity.py`'s `CommandEntity` base handles the shared logic every writable entity
needs: fetch a valid access token (triggering reauth on `ConfigEntryAuthFailed`), send the
command through `proxy_client.py`, and translate proxy/API errors into
`ServiceValidationError` (user-actionable, e.g. rate-limited, vehicle asleep) vs.
`HomeAssistantError` (unexpected). It also implements one behavior worth calling out
explicitly: if a command fails because the vehicle is asleep, it automatically issues
`wake_up`, waits, and retries once — this is intentional UX, not silent error-swallowing.

## Entities

Every entity ultimately derives from either `TelemetryEntity` (read-only,
`entity_base.py`) or `CommandEntity` (`command_entity.py`); both set
`_attr_has_entity_name = True` and a `device_info` property built from
`device.build_device_info(coordinator, vin)`. `device.py` normalizes vehicle metadata
(name, model, firmware version) from telemetry fields (`VehicleName`, `CarType`,
`Version`) into a proper `DeviceInfo` with `identifiers={(DOMAIN, vin)}`,
`manufacturer="Tesla"`, `serial_number=vin`.

## Diagnostics & repairs

- `diagnostics.py` redacts tokens, client credentials, VIN, and location/connectivity
  telemetry fields before export via `async_redact_data`.
- `repairs.py` creates/dismisses two repair issues via `issue_registry`:
  `key_not_paired` (virtual key not paired with the vehicle — includes a QR pairing flow)
  and `unknown_telemetry_field` (vehicle reports a field this integration doesn't
  recognize — offers to disable the entity). Both flows are looked up via
  `hass.config_entries.async_entries(DOMAIN)` + `entry.runtime_data`, the single source of
  truth for per-entry runtime state (the legacy `hass.data[DOMAIN]` duplicate bookkeeping
  was removed in this pass).

## Known gap: no automated tests

There is no `tests/` directory; CI's pytest step silently no-ops rather than failing when
it's absent. This is the one item from the original audit request not addressed in this
pass — a `pytest-homeassistant-custom-component` suite covering config flow (success +
reauth + error paths), the coordinator's push/debounce behavior, and at least one entity
per platform is recommended as dedicated follow-up work, given its size relative to
everything else fixed here.
