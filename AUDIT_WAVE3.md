# Audit Wave 3 — Full-Repo Bug / Improvement Sweep

Date: 2026-09-24. Scope: all of `custom_components/tesla_fleet_telemetry/`, the tests, and
CI config. Follows `DEFECT_LIST.md` and `AUDIT_WAVE2.md`; issues already covered there
are not repeated.

Validation: the full suite (75 tests: the 66 existing plus 9 new in
`tests/test_audit_regressions.py`) passes, and pre-commit (ruff, ruff-format, mypy,
JSON/YAML checks), `validate_files.py` and yamllint are clean. All 9 new tests fail
against the pre-fix code.

## Fixed in this wave

| # | Area | Defect | Fix |
|---|------|--------|-----|
| 1 | `media_player.py` | `media_position_updated_at` returned `utcnow()` on every state write. That changed the attributes on every coordinator update (any field, ~1 Hz in realtime mode), so the recorder wrote a new row every time. | The timestamp is taken only when the elapsed position changes. |
| 2 | `media_player.py` | Volume was capped at 10.0, but `adjust_volume` accepts 0–11. The optimistic volume was also cleared by *any* field update. | The cap is now the vehicle max (≤ 11). The optimistic value is dropped only when `MediaAudioVolume` changes. |
| 3 | `cover.py` | `window_control` sent lat/lon only for `vent`. Tesla requires them for **close** (per the `tesla-fleet-api` docstring) and ignores them for vent. | Both commands now send the vehicle location. |
| 4 | `entity_registry.py` | Enabling or disabling an entity within 5 min of the last push was skipped as `throttled_auto` and never retried, so the new entity stayed unavailable until a reload or a manual push. | A skipped resync is now retried after the throttle or rate-limit window ends. The retry is guarded against firing after unload. |
| 5 | `fleet_config.py` | Only one `unsupported_interval` rejection was retried per push. A second rejected field failed the push, and the first field's authorized interval was lost, so every later push hit the same failures. | Retries once per distinct field (up to 5). Learned intervals are returned in `authorized_interval_overrides` on success *and* failure, and `__init__.py` stores all of them. |
| 6 | `fleet_config.py` | An invalid stored CA raised a bare `ValueError` outside the error handling. The "fatal CA" branch in `_is_fatal_fleet_config_push_error` could never run. | It now raises an actionable `HomeAssistantError`, and the dead branch is removed. |
| 7 | `__init__.py` | The detached post-push sync task (manual pushes) only caught `TimeoutError`/`OSError`. A revoked token (`ConfigEntryAuthFailed`) or an `aiohttp` error became an unhandled task exception and started no reauth. | Auth failures now start reauth and publish a status. Other errors publish a failed-sync status. |
| 8 | `update.py` | `in_progress` was true whenever the download percent was 0–99, including the idle `0` with no pending update. The entity could show "installing" indefinitely. | Progress is reported only while `SoftwareUpdateVersion` differs from the installed version. |
| 9 | `number.py` | The charge-amps max was hard-capped at 32 A, which blocked 48 A (NA Wall Connector) vehicles. | The vehicle-reported `ChargeCurrentRequestMax` is used, with a sanity cap of 80 A. |
| 10 | `proxy_client.py` | Wake dedup cleared `_wake_task` in the first caller's `finally`. If that caller was cancelled, the next caller started a second wake while the first was still running. | The task is cleared from a done-callback, which also retrieves its exception. |
| 11 | `config_flow.py` | Already-configured VINs were rejected only at the very end, after OAuth, the connection form, CA fetch and partner registration. | Configured VINs are filtered right after the VIN fetch. The flow aborts early when none remain. |
| 12 | `config_flow.py` | Reauth accepted a token from any Tesla account, including one that can't see this vehicle. | Aborts with `wrong_account` when the entry's VIN is not in the new account's vehicle list. |
| 13 | `repairs.py` | The pairing-link fallback used `https://tesla.com/_ak/{VIN}`, but the path takes the app **domain**. | Rebuilt from the entry's domain or telemetry hostname. |
| 14 | `repairs.py` | The unknown-field fix disabled entities as soon as "Fix" was clicked, although its strings describe a confirm step. | Shows the confirm form first. |
| 15 | `strings.json` / `en.json` | Missing text for the `reauth_successful` abort, the standard OAuth aborts, the options `reauth_not_possible` abort, and the new `wrong_account` abort. The `still_not_paired` text had drifted between the two files. | Added, and the two files are identical again. |
| 16 | misc | `button.py` re-wrapped its own `ServiceValidationError` ("Action failed: Push skipped: …"). `climate.py`/`select.py` raised `ValueError` for bad user input. `diagnostics.py` used `get_event_loop()`. | `ServiceValidationError` is re-raised as is, bad input raises `ServiceValidationError`, and diagnostics uses `get_running_loop()`. |

## Follow-up pass (resolved open findings)

| # | Finding | Resolution |
|---|---------|-----------|
| 17 | **Region hardcoded to EU.** NA/APAC accounts failed the setup VIN fetch; partner registration happened only in EU. | `async_fetch_available_vins` detects the account's regional host with `TeslaFleetApi.find_server()` (`/api/1/users/region`). The host is stored per entry as `fleet_api_base` (refreshed on reauth) and used for the partner token, domain registration and the public-key check. The OAuth `audience` stays on the default host, because the region isn't known until after sign-in. |
| 18 | **Telemetry CA pinned only the leaf certificate**, so vehicles stopped trusting the server when the cert renewed. | `utils.async_fetch_server_ca` returns the full verified chain (leaf → root) for publicly trusted servers. On every startup the bootstrap re-fetches the chain and, if it differs, updates the stored CA before the startup push. Existing leaf-pinned entries migrate on their next restart. |
| 19 | **No MQTT auth/TLS.** `docs/SETUP.md` recommends `allow_anonymous false` with "matching credentials in Home Assistant", but there were no credential fields. | Optional username, password (password selector) and "Use TLS" (HA's system-trusted client context) on the setup and options forms. Both new fields are redacted in diagnostics. |
| 20 | **Fixed MQTT client id** made two HA instances evict each other in a loop. | Client id is `tesla_fleet_telemetry_<VIN>_<first 8 chars of the HA instance id>`. |
| 21 | **Manual pushes bypassed the 30 s throttle.** | The throttle now covers every reason and reports "throttled, retry in Ns". *Reload Telemetry Data* deletes and then pushes, so it passes `force=True` to avoid leaving the vehicle with no config. |
| 22 | Docs claimed a PEM certificate could be pasted manually, but no such field exists. | `SETUP.md`/`TROUBLESHOOTING.md` corrected. The renewal section now describes the automatic re-check. |

Tests: `tests/test_audit_wave4.py` covers region detection, full-chain vs leaf-only CA
fetch (against a local TLS server with a generated CA → leaf chain), and MQTT
credential, TLS and client-id wiring.

## Still open

- **Two MQTT reconnect loops.** paho's `loop_start()` thread already reconnects after
  `connect_async`, and `_schedule_reconnect` also calls `connect_async`. It works, but
  paho's own `reconnect_delay_set()` would be simpler.
- **Proxy TLS verification is always off** (`_INSECURE_SSL`). This is fine for the
  documented localhost/self-signed proxy, but an optional CA would help a remote proxy.
- `set_temps` always sets the passenger temperature equal to the driver's.
- The reauth "invalid token" check matches message substrings instead of exception types.
- The config flow and `oauth2.py` create ad-hoc `aiohttp.ClientSession`s instead of
  HA's shared session.
- `hacs.json` / `requirements-dev.txt` use `2026.0.0`. It resolves to 2026.1+ but reads
  like a typo.
- CI pins Python 3.14. The preinstalled 3.14 RC breaks inside `mashumaro`; tests were
  run on 3.13.
