# Audit Wave 2 — Regression Sweep + Fresh Full-Codebase Re-Audit

Date: 2026-07-12. Scope: `custom_components/tesla_fleet_telemetry/`, following up on the
initial audit/fix pass (`DEFECT_LIST.md`, `ARCHITECTURE.md`, commit `a20c3e9`).

## A note on how this wave started

The task that kicked off this wave described a "Wave 2" and "Wave 3" history — a
prior verification pass, and a production-log regression discovery — with specific
file references (`AUDIT_WAVE2.md`, `AUDIT_WAVE3.md`) and a specific bug (VIN-scoped
`unique_id` breaking `entity_registry.py`'s field-resolution logic). None of that
history actually existed: there was no prior `AUDIT_WAVE2.md`/`AUDIT_WAVE3.md`, and
git log showed only the one audit commit (`a20c3e9`) beyond the pre-audit checkpoint.
That was flagged to the user at the time.

However, the *specific technical claim* embedded in that false history was real: the
`unique_id` VIN-scoping fix from `a20c3e9` had indeed broken `entity_registry.py`'s
`_unique_id_suffix`/`_fields_for_unique_id`/`_default_enabled_for_unique_id`, which
parse `unique_id` back into a field name for enabling dependency entities, computing
which telemetry fields to request from Tesla based on enabled entities, and the
"unknown telemetry field" repair flow. This was found and fixed independently in this
session (converging with a concurrent fix already merged via `c6636dd` + `af17f27`
from another worktree session working on the same repo). This wave treated that as the
starting point for a full regression sweep and fresh audit, per the (independently
legitimate) instructions in the rest of the task.

## Part 1 — Regression sweep of Wave 1's fixes

Every shape/contract change from `a20c3e9` was re-checked for other consumers:

| Wave 1 change | Consumers checked | Result |
|---|---|---|
| `unique_id` → `telemetry_{vin}_{field}` | All ~24 construction call sites; `entity_registry.py`'s 4 parsing functions; `repairs.py` | **Was broken, now fixed** (see above) — all consumers now pass `vin` through consistently |
| `ConfigEntryAuthFailed` → `ConfigEntryError` for 4 config-validation cases | Every `except ConfigEntryAuthFailed`/`ConfigEntryError` in the codebase | Clean — no other code expected those 4 cases to raise `ConfigEntryAuthFailed` |
| `CONF_MQTT_TOPIC_BASE` added | `__init__.py`, `config_flow.py` (setup + options schema), `mqtt_client.py` | Clean — all consistent. **New gap found and left unfixed**: the reauth step's explicit whitelist doesn't include `CONF_MQTT_TOPIC_BASE` (or `CONF_DOMAIN`/`CONF_CONNECTION_TIMEOUT`), but `async_update_and_abort`'s `data_updates` performs a *merge*, not a replace, onto existing `entry.data` — verified against real HA core source — so these keys survive reauth untouched. Not a bug, but worth a comment if anyone changes this flow to a full `data=` replace instead of `data_updates=`. |
| `TeslaOAuth2Implementation` → `LocalOAuth2ImplementationWithPkce` | Every `TeslaOAuth2Implementation` type reference across all entity platforms, `application_credentials.py`, `command_entity.py` | Clean — all references are type annotations / `cast()`, nothing does an `isinstance` check against the old base |
| `hass.data[DOMAIN]` bookkeeping removed | Whole codebase | Clean — nothing else read it |
| `async_retry_with_backoff` removed | Whole codebase (main tree) | Clean on `main`. Note: two unrelated locked worktrees (`charger-power-delta-fix`, `keen-greeting-sparkle`) still have their own uncommitted copies of the old `proxy_client.py` with this function present — not this repo's current state, not touched |

## Part 2 — Fresh audit findings (new, independent of Wave 1)

Three parallel re-audits were run treating every Wave 1 "confirmed correct" item as
unverified. Every finding below was independently re-verified against a real source
(HA core source downloaded directly, or a live web search/fetch) before acting on it —
one candidate finding from a sub-agent (a claim about `async_update_and_abort` having a
`reload_on_update` parameter) turned out to be wrong on inspection of the actual PR
diff and was corrected before use.

### Fixed this wave

1. **`policy.py` used `json.loads`/`json.JSONDecodeError` without importing `json`.**
   `NameError` on every call to `extract_unsupported_interval()` — which only fires
   when Tesla's proxy rejects a requested telemetry interval, so a normal test run or
   casual read would never hit it. Fixed: `a348b15`.

2. **`TelemetryCoordinator` relied on HA's ContextVar to infer its `config_entry`.**
   HA is removing ContextVar-based inference in **2026.8** (next month, relative to
   today's date) — confirmed via multiple independent HA core PRs/issues describing
   this exact pattern (`hvv_departures`, `Tibber`, `Broadlink`, `Tapo`, `Supla`, `Point`
   all hit the same warning). Fixed: `config_entry` is now passed explicitly.
   Fixed: `b34035b`.

3. **Reauth's `async_update_reload_and_abort` + the entry's own update listener caused
   a double-reload/race**, per HA's own developer blog (2026-05-07) and PR #169198 —
   verified directly against real HA core source (not just the blog summary, which
   partially mis-stated the fix). Deprecated now, **hard error in HA 2026.12**. Fixed by
   switching to `async_update_and_abort`, which updates the entry the same way but
   leaves the reload to the existing listener. Fixed: `8cde627`.

4. **`device_tracker.py`'s destination/origin location trackers had no
   `ENTITY_FIELD_MAPPINGS` entry.** `_fields_for_unique_id` returned an empty set for
   both, meaning: enabling/disabling either tracker never triggered a fleet config
   resync, and `OriginLocation` could never be requested from Tesla based on that
   tracker's state. Fixed: `65fe6ac`.

### Investigated, real, deliberately NOT fixed (with reasoning)

5. **The same update listener also reloads on every routine OAuth token refresh**, not
   just real reconfiguration — because `OAuth2Session.async_ensure_token_valid()`
   writes the refreshed token into `entry.data` on every expiry (verified directly
   against HA core's `config_entry_oauth2_flow.py` source), and the listener reloads on
   any entry data change. This means the integration's MQTT connection, proxy client,
   and coordinator state get torn down and rebuilt every time the OAuth token refreshes
   (likely every several hours), not just on real config changes.

   A fix was attempted (filter the token key out of the change-comparison before
   deciding whether to reload) and then **reverted** after further verification: HA's
   own guidance ("Always reload after a successful re-auth flow", 2024-04-25) says a
   reauth should always reload — including in edge cases where the entry's data value
   doesn't obviously change (e.g., a temporarily-disabled account re-enabled with no
   data diff). Since routine token refresh and reauth-with-unchanged-connection-config
   are structurally indistinguishable from the listener's point of view (both just
   change the token field), a data-diff-based filter would silently defeat legitimate
   reauth reloads exactly as often as it correctly skips routine refreshes. Trading a
   known minor inefficiency for a possible silent correctness bug was the wrong call,
   so the revert was kept. **This remains a known, real, unresolved inefficiency** —
   revisit only with a way to distinguish the two triggers by origin (e.g., a flag set
   only by the reauth flow), not by data diff.

### Flagged, unverified, no action taken

6. `proxy_client.py`'s `send_commands_batch()` posts to `api/1/vehicles/{vin}/batch_command`,
   which could not be confirmed as a real documented Tesla endpoint via any source
   checked. It's already wrapped in a broad try/except with a working per-command
   fallback, so it's not currently harmful beyond one guaranteed-failing HTTP round
   trip before falling back — but removing it outright without confirming the endpoint
   truly doesn't exist (it could be valid for some Fleet API tier not visible from
   public docs) would be an unverified assumption in the other direction. Left as-is;
   flagging for whoever can check against a live account/proxy.

7. `tesla-fleet-api` is pinned at exactly `==1.4.7` in `manifest.json`; newer 1.5.x
   releases may exist on PyPI. Could not get a reliable changelog/diff to assess
   whether bumping is safe or necessary. Not changed — flagging for manual check
   against the package's actual GitHub releases.

8. A sub-agent flagged uncertainty about whether `RestoreEntity`/`async_get_last_state`
   usage (`entity_base.py`, `sensor.py`) is affected by an HA 2026.3 change mentioned in
   passing during research ("mark RestoreEntity type hints mandatory"). Could not
   confirm behavioral impact (only found a PR title, not its content). Not acted on —
   genuinely unresolved uncertainty, not a confirmed non-issue.

9. HA's `async_on_unload` callback ordering (relied on to disconnect MQTT before
   coordinator shutdown during unload) was not independently traced against HA core
   source in this pass — noted as plausible but unconfirmed by a sub-agent, and not
   independently re-verified here given time constraints.

### Re-confirmed correct (independently re-verified, not just re-asserted)

- `application_credentials.py` wiring, `TeslaOAuth2Implementation`'s PKCE base class,
  and `OAuth2Session.async_ensure_token_valid()` usage — checked against real,
  downloaded HA core source (not training-data memory), matches exactly.
- `_abort_if_unique_id_configured()` — not present in this codebase, so the other half
  of the 2026.12 deprecation doesn't apply here.
- `entity_registry.py`'s unique_id parsing is now consistent everywhere (single helper,
  no ad-hoc parsing elsewhere) — re-verified by grep across the whole package, not just
  re-asserted from the prior fix.
- `device.py`'s `DeviceInfo` correctly disambiguates multiple vehicles.
- `diagnostics.py` redaction — the VIN-embedded `unique_id` never appears in the
  diagnostics payload (it's sourced from `coordinator.data`, not entity-registry
  entries), so the VIN-scoping change introduced no new leak.
- `repairs.py`'s VIN-length-based `issue_id` slicing is a dead fallback in practice
  (real code path always populates `issue.data` directly) — not a live bug.
- MQTT payload parsing, alert current/history handling, and the `prefer_typed` removal
  (re-checked again this wave against Tesla's fleet-telemetry docs/announcements,
  independent of the Wave 1 check) all still hold.
- Vehicle-command proxy endpoint paths (`api/1/vehicles/{vin}/command/{endpoint}`)
  match the documented pattern.

## Part 3 — Test suite (Step 4)

Added `tests/` with `pytest-homeassistant-custom-component`-based coverage:
`test_entity_registry.py` (the unique_id round-trip — the exact regression class),
`test_coordinator.py` (push/debounce path), `test_config_flow.py` (endpoint validation
helpers + the reauth data-merge behavior the `async_update_and_abort` fix touches),
`test_entities.py` (one entity per representative platform). Added
`pytest-homeassistant-custom-component` to `requirements-dev.txt` and a `hassfest` step
to CI. Commit: `46eb881`.

**Important caveat, stated plainly and not glossed over**: this sandbox has no `pip`
and no network package-install access, so **none of these tests have actually been
executed**. They're written against the current, real API shapes (verified by reading
the actual source they exercise), but they have not been run and could contain
mistakes that only surface at runtime — an import typo, a fixture misuse, an assertion
that doesn't hold against real HA behavior. **They must be run in CI (which does have
the real dependencies) before being trusted**, and the CI run's actual pass/fail result
should be treated as the real verification, not this document's description of them.

## Confidence statement

- The multi-vehicle `unique_id` collision, its regression in `entity_registry.py`, and
  the four fresh findings in Part 2 are all confirmed against real, directly-verified
  sources (HA core source read directly, or live doc/announcement checks) — not
  memory or assumption. Confidence in those specific fixes is high.
- The codebase as a whole is in meaningfully better shape than at the start of Wave 1,
  but this is **not** a certification that it's now defect-free. Two full audit waves
  have each found real issues the other missed or introduced; a third pass would
  likely find more, especially in code paths not exercised by normal startup (deep
  error-handling branches, options-flow reconfiguration edge cases, concurrent
  entry reloads).
- The test suite is unverified by execution. Until it's run once in a real CI pass,
  treat its existence as "a reasonable-effort scaffold," not "coverage that's known to
  pass."
- Two items (`send_commands_batch`'s endpoint, the `tesla-fleet-api` version pin) are
  genuinely unresolved unknowns — not because they were deprioritized, but because no
  available source could confirm or refute them.
- The token-refresh-triggers-full-reload behavior is a known, real, accepted
  inefficiency, not a bug that was missed — the tradeoff reasoning is documented in
  code (`__init__.py`) and above, specifically so a future pass doesn't rediscover it
  and ship the same unsafe fix.
