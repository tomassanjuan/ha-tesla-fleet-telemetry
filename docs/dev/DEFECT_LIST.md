# Audit Defect List — tesla_fleet_telemetry

Audited 2026-07-12 against current Home Assistant developer conventions and Tesla Fleet
API / `vehicle-command` proxy / `fleet-telemetry` documentation. Scope: full
`custom_components/tesla_fleet_telemetry/` directory (~10k lines across 28 modules).

Overall assessment: this is **not** a naive AI-slop codebase. The architecture
(application_credentials-based OAuth, push `DataUpdateCoordinator`, async proxy client,
redacted diagnostics, repair issues, reauth flow) is fundamentally sound and follows
current HA idiom in most places. The defects below were real but targeted — all have now
been fixed in place rather than warranting a rewrite. Status noted per item.

## High severity — FIXED

1. **Entity `unique_id` collided across vehicles (multi-VIN breakage). FIXED.**
   `entity_base.py:16-18` built `_attr_unique_id` from the field/key name alone — never
   the VIN. HA's entity registry keys entities by `(domain, platform, unique_id)`, not by
   config entry, and the config flow's own `async_set_unique_id(vin)`
   (`config_flow.py:997`) proves multiple vehicles/config entries are intended. With two
   vehicles configured, every entity of the second vehicle collided with the first's.
   **Fix applied:** `telemetry_unique_id(vin, key) -> f"telemetry_{vin}_{key}"`, threaded
   through all ~24 call sites across every platform file.

2. **No PKCE on the OAuth2 authorization-code flow. FIXED.**
   `oauth2.py` subclassed plain `LocalOAuth2Implementation`. Tesla's Fleet API OAuth
   requires PKCE (S256); HA core ships `LocalOAuth2ImplementationWithPkce` for exactly
   this case (verified against HA core source, not assumed).
   **Fix applied:** `TeslaOAuth2Implementation` now subclasses
   `LocalOAuth2ImplementationWithPkce`; `extra_authorize_data`/`extra_token_resolve_data`
   merge with `super()` to preserve the existing Tesla-specific extra params.

## Medium severity — FIXED

3. **`ConfigEntryAuthFailed` misused for non-auth config errors. FIXED.**
   `__init__.py` raised `ConfigEntryAuthFailed` (triggers HA's reauth/OAuth UI) for
   missing config keys, invalid/missing MQTT host or port, and a missing proxy URL — none
   of which are credential problems.
   **Fix applied:** these four raise `ConfigEntryError` now (a permanent config problem,
   not something a reauth or retry fixes). Genuine auth failures are untouched.

4. **MQTT topic prefix `"tesla/"` hardcoded, not configurable. FIXED.**
   `mqtt_client.py` hardcoded the topic root as `f"tesla/{vin}/..."` in four places. The
   upstream `fleet-telemetry` server's `topic_base` is a broker-side setting the user
   configures independently — a mismatch would silently receive nothing.
   **Fix applied:** added `CONF_MQTT_TOPIC_BASE` (default `"tesla"`) to `const.py`, wired
   into `TeslaMQTTClient.__init__`, `__init__.py`'s setup, and both the initial and
   options config-flow schemas (with `strings.json`/`translations/en.json` labels).

5. ~~`minimum_delta` pushed without `prefer_typed`~~ — **not a defect, re-verified against
   current Tesla docs; no code change made.** An initial pass flagged this against older
   Tesla docs (which required `prefer_typed: true` for `minimum_delta` to take effect).
   Checked against developer.tesla.com/docs/fleet-api/announcements: Tesla **removed**
   `prefer_typed` as of August 2025 — vehicles now always send typed data regardless of
   the setting, so `minimum_delta` (numeric fields only) already works without it. Noted
   here so it isn't relitigated.

6. **Alert `/history` topic not filtered, corrupted `LastAlertTriggered`. FIXED.**
   `mqtt_client.py` stripped a trailing `"/current"` suffix from alert topics but did
   nothing with `.../alerts/<name>/history`, so history-replay messages flowed through and
   overwrote the alert sensor's state with a raw `"<AlertName>/history"` string.
   **Fix applied:** only `/current` topics now update the alert sensor; `/history` topics
   are explicitly ignored (see comment in `_on_mqtt_message`).

## Low severity / cleanup — FIXED

7. **`coordinator.py` set `self.last_updated`, an attribute that doesn't exist on
   `DataUpdateCoordinator`. FIXED.** (It exists only on `TimestampDataUpdateCoordinator`.)
   Dead code; nothing read it. **Fix applied:** line removed along with the now-unused
   `utcnow` import.

8. **`strings.json` / `translations/en.json` drift. FIXED.** `fleet_config.py:440`
   creates a repair issue with `translation_key="unknown_telemetry_field"`, which existed
   in `translations/en.json` but was missing from `strings.json` (the source-of-truth
   template). **Fix applied:** added the matching `issues.unknown_telemetry_field` entry
   to `strings.json`; both files now match exactly (one pre-existing unrelated cosmetic
   diff in `key_not_paired` wording left as-is, out of scope).

9. **`entry.runtime_data` vs legacy `hass.data[DOMAIN][entry.entry_id]` inconsistency.
   FIXED.** Both were populated; most code read `entry.runtime_data`, but `repairs.py` and
   `entity_registry.py` read `hass.data[DOMAIN]` instead, while `async_unload_entry` only
   cleared the `hass.data` copy — a latent trap. **Fix applied:** removed the legacy
   `hass.data[DOMAIN]` bookkeeping entirely (`__init__.py`); `repairs.py` now looks up the
   entry via `hass.config_entries.async_entries(DOMAIN)` + `entry.runtime_data`,
   `entity_registry.py`'s resync listener uses the `entry` already in its closure. Single
   source of truth now.

10. **No test suite. NOT FIXED — flagged for follow-up, out of scope for this pass.**
    `tests/` doesn't exist; CI's pytest step silently no-ops rather than failing. No
    `hassfest` validation step exists either (would have caught #8 automatically). Writing
    a `pytest-homeassistant-custom-component` suite covering config flow, coordinator, and
    one entity per platform is a substantial separate effort — recommend a follow-up pass.

11. **`manifest.json` declared `"homeassistant": "2024.1.0"`** as the minimum, but the
    code uses `entry.runtime_data` (added in HA 2024.9) throughout — installing on the
    declared minimum would crash at setup. **Fix applied:** bumped to `"2025.12.5"` to
    match the floor already pinned in `requirements-dev.txt`.

12. **`proxy_client.py`'s `async_retry_with_backoff`** was fully implemented but never
    applied anywhere. **Fix applied:** removed (dead code), along with its now-unused
    `functools.wraps`/`TypeVar`/`Coroutine`/`Callable`/`asyncio` imports. Also fixed an
    adjacent redundant `except (TeslaFleetError, Exception)` to just `except Exception`.

## Confirmed correct (no action needed)

- Push-based `DataUpdateCoordinator` pattern (no polling `update_interval`; MQTT callback
  drives updates via debounced field writes) — the right pattern for MQTT telemetry.
- Proxy client: pure async `aiohttp`/`tesla_fleet_api` calls, no blocking I/O, correct
  setup/teardown via `entry.async_on_unload`, idempotent close.
- Proxy TLS cert/key handling is intentionally left to the user's externally-run
  `vehicle-command` proxy (Docker Compose prerequisite, documented in `docs/SETUP.md`) —
  not a gap, a deliberate and correct architectural choice for this deployment model.
- `application_credentials.py` / `config_flow.py` OAuth2 flow handler wiring, reauth flow,
  and scopes (`openid offline_access user_data vehicle_device_data vehicle_location
  vehicle_cmds vehicle_charging_cmds`) are all correct per current HA conventions.
- Token refresh uses HA's `OAuth2Session.async_ensure_token_valid()` — no hand-rolled or
  blocking refresh logic.
- `device.py`'s `DeviceInfo` (`identifiers`, `manufacturer`, `model`, `serial_number`,
  `sw_version`) is correct — the multi-vehicle bug was entity-level only, not device-level.
- `diagnostics.py` thoroughly redacts tokens/secrets/VIN before export.
- MQTT ingestion correctly subscribes to the self-hosted `fleet-telemetry` server's
  already-decoded JSON-per-field MQTT dispatcher output (not raw protobuf) — paho-mqtt is
  wrapped correctly for asyncio (`loop_start`, `call_soon_threadsafe`, future-based connect
  signaling), with reconnect/backoff.
- No blocking calls (`time.sleep`, sync `requests`, direct file I/O) found in any async
  code path across the whole integration.

## Not fixed in this pass

- **#10 (no test suite)** — recommend as a dedicated follow-up; writing a real
  `pytest-homeassistant-custom-component` suite is significant, separate work.
- Consider adding a `hassfest`-equivalent validation step to CI (there is currently
  `validate_files.py` for manifest/JSON but no full hassfest run) — would have caught
  the strings.json/translations drift (#8) automatically.
