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

## Open findings (not changed — need a product decision or real-vehicle testing)

### High impact

- **Fleet API region is hardcoded to EU** (`const.FLEET_API_BASE =
  fleet-api.prd.eu.vn.cloud.tesla.com`). It is used for the OAuth `audience`, partner
  registration and public-key checks, and the setup VIN fetch. North America and
  Asia-Pacific accounts get an out-of-region error at the VIN fetch (setup aborts with
  `vin_fetch_failed`), and partner registration is done only for the EU region. The
  docs don't mention the limitation. Options: a region selector in the config flow, or
  auto-detection via `TeslaFleetApi.find_server()` (`/api/1/users/region`) with
  registration in the detected region.
- **The telemetry CA pins the leaf certificate.** `_async_fetch_server_cert` sends only
  `chain[0]` (the server's own cert) as `ca`. When the cert renews (every 60–90 days
  with Let's Encrypt), vehicles stop trusting the server until the user re-saves the
  options. Tesla's docs describe `ca` as the chain that issued the server cert. Sending
  the issuing intermediate/root, or re-checking the CA on startup and pushing again
  when it changes, would avoid silent outages.

### Medium

- **No MQTT authentication or TLS.** `TeslaMQTTClient` has no username, password or TLS
  options, so the broker must allow anonymous plaintext access.
- **Two MQTT reconnect loops.** paho's `loop_start()` thread already reconnects after
  `connect_async`, and `_schedule_reconnect` calls `connect_async` on top of that.
  Functionally it works; `reconnect_delay_set()` plus letting paho own reconnection
  would be simpler.
- **Fixed MQTT client id** (`tesla_fleet_telemetry_<VIN>`). A second HA instance, such
  as a dev box, would kick the first off the broker in a loop.
- **Proxy TLS verification is always off** (`_INSECURE_SSL`), even for a remote proxy.
  An optional CA or "verify" toggle would help setups where the proxy isn't on
  localhost.
- **Manual pushes bypass the 30 s universal throttle.** The code comment says
  "regardless of reason", but `reason == "manual"` skips it. Repeated button presses
  can each push.

### Low

- `set_temps` always sets the passenger temperature equal to the driver's.
- The reauth "invalid token" check matches error-message substrings (`"401"`,
  `"unauthorized"`) instead of exception types.
- The config flow and `oauth2.py` create ad-hoc `aiohttp.ClientSession`s instead of
  HA's shared session.
- `_generate_qr_data_uri`'s docstring says SVG, but it produces PNG.
- `hacs.json` / `requirements-dev.txt` use `2026.0.0` (HA calver has no `.0` month).
  It resolves to 2026.1+, but reads like a typo.
- CI pins Python 3.14. Local runs against the preinstalled 3.14 RC fail inside
  `mashumaro` (`typing.ByteString` was removed); Python 3.13 works.
