# Audit: MQTT Only Delivering Static Metadata Fields

## Symptom

Only `CarType`, `EfficiencyPackage`, `VehicleName`, `Version` arrive over MQTT
for vehicle `5YJ3E1EA1JF000001`, instead of the full field set implied by its
enabled entities.

## Is this the wave-3 bug (unique_id/VIN stripping)?

**No.** Confirmed by direct evidence, not inference:

- `custom_components/tesla_fleet_telemetry/entity_registry.py`'s
  `_unique_id_suffix`/`_fields_for_unique_id` correctly strip the
  `telemetry_{vin}_` prefix and resolve fields from `FIELD_DEFS` /
  `ENTITY_FIELD_MAPPINGS`. Running this exact code inside the live
  `homeassistant` container against the current `core.entity_registry`
  snapshot for this VIN's config entry resolves **94** required fields, not 0.
- The registry data used for that run was untouched since `18:23:58` /
  `18:25:54` UTC (per each entry's `modified_at`) — i.e. it was already in its
  current (enabled) state well before the push in question fired.

So entity → field resolution is correct once the registry has settled. The
wave-3 fix holds.

## Actual root cause: a startup race, not a resolution bug

`hassio/home-assistant.log` shows the one and only push so far this session:

```
20:31:33.597 DEBUG entity_registry: Resolved 0 entity-driven telemetry fields for 5YJ3E1EA1JF000001
20:31:33.597 DEBUG entity_registry: Resolved 4 required telemetry fields from enabled entities for 5YJ3E1EA1JF000001
20:31:33.597 INFO  fleet_config: fleet_telemetry_config pushed for 5YJ3E1EA1JF000001: mode=realtime reason=setup total=4 fast=0 slow=4
20:31:33.782 DEBUG fleet_config: fleet_telemetry_config response received: {'response': {'updated_vehicles': 1}}
20:31:48.851 DEBUG fleet_config: fleet_telemetry_config for 5YJ3E1EA1JF000001 synced on attempt 1
```

This push happens inside `_async_run_setup_bootstrap`, scheduled as a
background task from `async_setup_entry` (`__init__.py`) right after platform
setup and `_sync_entity_registry_defaults` run. In this boot, it fired before
the live entity registry actually reflected the config entry's entities —
`_entity_required_fields` queried an empty/incomplete set at that instant,
so `_resolved_required_fields` fell back to just `DEVICE_METADATA_FIELDS`
(the always-on baseline in `const.py`), and that 4-field config is exactly
what got pushed and acknowledged by Tesla (`updated_vehicles: 1`).

Nothing re-triggered a corrective push afterward:
`_register_entity_registry_sync_listener` only reacts to entity registry
`"update"` events that change `disabled_by` — not to the entities simply
becoming visible/settled after their initial creation. So the vehicle kept
streaming the 4-field fallback config indefinitely once the race was lost.

Tesla's ack (`updated_vehicles: 1`) is not evidence the field list was
correct — it only confirms Tesla accepted whatever config was sent. This is
the same gap the diagnostic prompt called out, and it's now documented in
`docs/TROUBLESHOOTING.md` under "Only Metadata Fields Arriving".

## Fix

`custom_components/tesla_fleet_telemetry/__init__.py` — `_async_run_setup_bootstrap`:
the existing single retry-after-delay (originally only for an outright push
failure) now also fires when the first push only resolved the fallback
metadata field count. By the retry (after `BOOTSTRAP_PUSH_RETRY_DELAY_SECONDS`),
the entity registry has settled, so the corrective push resolves the real
field set. This reuses the retry path that already existed rather than adding
new machinery.

## Verification status

Static verification (code correctness, retry-condition unit check) is
complete. **Live MQTT confirmation that the corrected field set actually
starts arriving after the fix ships still needs to happen post-restart** —
per this task's own constraint, a successful push log is not sufficient
evidence. Recommended check after deploying:

```bash
mosquitto_sub -h mqtt.local -t 'tesla/5YJ3E1EA1JF000001/#' -v
```

confirming fields like `BatteryLevel`, `Location`, `ChargeState`, `Locked`
(matching this vehicle's 94 resolved entity-driven fields) appear, not just
the 4 metadata fields.
