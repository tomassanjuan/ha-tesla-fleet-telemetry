# Audit Wave 3 — Full-Repo Bug / Improvement Sweep

Date: 2026-09-24. Scope: all of `custom_components/tesla_fleet_telemetry/`, the tests, and
CI config. Follows `DEFECT_LIST.md` and `AUDIT_WAVE2.md`; issues already covered there
are not repeated.

Validation: the full suite (75 tests: the 66 existing plus 9 new in
`tests/test_audit_regressions.py`, now `tests/test_push_and_entity_regressions.py`) passes, and pre-commit (ruff, ruff-format, mypy,
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

Tests: `tests/test_audit_wave4.py` (now `tests/test_connectivity.py`) covers region detection, full-chain vs leaf-only CA
fetch (against a local TLS server with a generated CA → leaf chain), and MQTT
credential, TLS and client-id wiring.

## Final pass (remaining low-priority findings)

| # | Finding | Resolution |
|---|---------|-----------|
| 23 | **Two MQTT reconnect loops.** paho's network thread and our `_schedule_reconnect` both reconnected. | Our loop is removed. paho owns reconnection, with `reconnect_delay_set(1, 60)`. `on_connect_fail` now fails the *initial* connect straight away, so setup no longer waits out the whole timeout. |
| 24 | **Proxy TLS verification always off.** | New "Verify proxy TLS certificate" option (default off, for the usual self-signed proxy). When on, it uses HA's system-trusted client context, and the form reports a distinct `proxy_cert_invalid` error. |
| 25 | **`set_temps` overwrote the passenger temperature with the driver's.** | The passenger's current setpoint is kept; it falls back to the driver value only when unknown. |
| 26 | **Reauth token check matched message substrings.** | `async_fetch_available_vins` raises `TeslaAuthError` for HTTP 401/403, and reauth catches that type. |
| 27 | **Ad-hoc `aiohttp.ClientSession`s** in `oauth2.py` and the config flow's proxy check. | They now use HA's shared session (`async_get_clientsession`). |
| 28 | **`2026.0.0` version pins.** | Corrected to `2026.1.0`, the same effective floor as a valid calver. |
| 29 | **CI red on `main` since before this audit.** Tests failed with "Lingering timer" (newer HA test harness), and mypy couldn't parse HA's Python 3.14 syntax. | Test fixtures cancel the coordinator's debounce timer (new `async_cancel_pending_update()`). mypy `python_version` is now 3.14, matching CI. |

No findings from this audit remain open.

## Wire-format pass (checked against fleet-telemetry's MQTT dispatcher)

Every decoder was re-checked against `fleet-telemetry/datastore/mqtt/mqtt_payload.go`
(`getDatumValue`/`getProtoValue`) and `protos/vehicle_data.proto`, the code that
actually produces the MQTT payloads this integration reads.

| # | Finding | Resolution |
|---|---------|-----------|
| 30 | **Window binary sensors never had a state.** `FdWindow`…`RpWindow` arrive as `WindowState` enum names (`"WindowStatePartiallyOpen"`), and the binary sensor returned `None` for any string. | Enum-name decoding: Closed → off, PartiallyOpen/Opened → on, Unknown → unknown. |
| 31 | **Driver seat-belt binary sensor never had a state.** The value is a `BuckleStatus` enum name. | Latched → on, Unlatched → off, Faulted/Unknown → unknown. |
| 32 | **TPMS warning sensors never had a state.** `TpmsHardWarnings`/`TpmsSoftWarnings` are a TireLocation object listing only the flagged tyres (`{}` or `{"front_left": true}`), and dicts decoded to `None`. | Aggregate = any flagged tyre. |
| 33 | **The per-tyre TPMS fields aren't Tesla fields.** `TpmsHardWarningFl`… are not in the proto, so enabling one put an unknown field in the push and Tesla rejected the whole config. The mapping that should have tied them to the real field was keyed on a suffix no entity uses. | Per-tyre values are derived in the coordinator from the aggregate object. They're excluded from the push and mapped to the aggregate field for enablement. |
| 34 | **Partially open window read as closed** by the windows cover. | `PartiallyOpen` counts as open. |
| 35 | **`HvilStatusUnknown` reported as a fault.** | Unknown is now unknown; only `Fault` is on. |
| 36 | **Charge-port latch decoding used substring checks.** "engaged" matches "disengaged". It's currently unused (the field is a sensor), but was wrong. | Exact matches. |
| 37 | **Payload parser coerced strings.** Every value is `json.Marshal`ed, but the parser stripped quotes and tried `float()`: a numeric-looking string became a number, and `\u` escapes were left raw. | JSON-first decoding, with the old heuristics kept only for non-JSON payloads. |
| 38 | **Invalid values showed stale restored state.** `null` (Tesla's "invalid") wasn't stored, so an entity kept showing its pre-restart value indefinitely. | The coordinator stores explicit `None`. Restored state is used only until the field has been received at all. |
| 39 | **The unknown-field repair undid itself.** It disabled entities with `disabled_by=INTEGRATION`, which the next setup's default-enabled sync re-enables, and which the fleet-config resync listener ignores. | It now disables as `USER`, which is what it is: a user-confirmed fix. |

Tests: `tests/test_value_decoding.py` (25 cases); 15 of them fail on the previous code.
