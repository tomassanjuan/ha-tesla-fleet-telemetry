"""Tests for unique_id construction/parsing in entity_registry.py.

Regression coverage for the class of bug that actually occurred: unique_id was
changed from `telemetry_{field}` to `telemetry_{vin}_{field}` to fix a
multi-vehicle collision, but the registry-side parsing that strips the prefix
back off to recover the field name assumed the old shape and silently started
returning empty results for every entity. These tests assert the full round
trip (construct -> parse) holds for the current shape, and would fail
immediately if that shape or its parsing ever drift apart again.
"""

from __future__ import annotations

from custom_components.tesla_fleet_telemetry.const import ENTITY_FIELD_MAPPINGS, FIELD_DEFS
from custom_components.tesla_fleet_telemetry.entity_base import telemetry_unique_id
from custom_components.tesla_fleet_telemetry.entity_registry import (
    _default_enabled_for_unique_id,
    _fields_for_unique_id,
    _unique_id_suffix,
)

from .conftest import TEST_VIN, TEST_VIN_2


def test_telemetry_unique_id_includes_vin() -> None:
    """unique_id must be VIN-scoped so multiple vehicles never collide."""
    assert telemetry_unique_id(TEST_VIN, "battery_level") == f"telemetry_{TEST_VIN}_battery_level"


def test_two_vehicles_never_produce_the_same_unique_id() -> None:
    """The exact bug class being guarded against: cross-vehicle collision."""
    assert telemetry_unique_id(TEST_VIN, "climate") != telemetry_unique_id(TEST_VIN_2, "climate")


def test_unique_id_suffix_round_trip_direct_field() -> None:
    """A unique_id built from a bare FIELD_DEFS key round-trips back to it."""
    field_name = next(iter(FIELD_DEFS))
    unique_id = telemetry_unique_id(TEST_VIN, field_name.lower())
    assert _unique_id_suffix(unique_id, TEST_VIN) == field_name.lower()
    assert _fields_for_unique_id(unique_id, TEST_VIN) == {field_name}


def test_unique_id_suffix_returns_none_for_wrong_vin() -> None:
    """Parsing with the wrong VIN must not accidentally match."""
    unique_id = telemetry_unique_id(TEST_VIN, "climate")
    assert _unique_id_suffix(unique_id, TEST_VIN_2) is None
    assert _fields_for_unique_id(unique_id, TEST_VIN_2) == set()


def test_fields_for_unique_id_covers_every_entity_field_mapping() -> None:
    """Every declared multi-field/aliased mapping must resolve via its own VIN."""
    for suffix, mapping in ENTITY_FIELD_MAPPINGS.items():
        unique_id = telemetry_unique_id(TEST_VIN, suffix)
        assert _fields_for_unique_id(unique_id, TEST_VIN) == set(
            mapping.fields
        ), f"suffix {suffix!r} did not round-trip to its mapped fields"


def test_destination_and_origin_location_are_mapped() -> None:
    """Regression guard for the destination/origin tracker mapping gap."""
    dest_id = telemetry_unique_id(TEST_VIN, "destination_location")
    origin_id = telemetry_unique_id(TEST_VIN, "origin_location")
    assert _fields_for_unique_id(dest_id, TEST_VIN) == {"DestinationLocation"}
    assert _fields_for_unique_id(origin_id, TEST_VIN) == {"OriginLocation"}


def test_default_enabled_for_unique_id_uses_mapping_when_present() -> None:
    """Mapped suffixes should reflect TelemetryEntityMapping.enabled_by_default."""
    unique_id = telemetry_unique_id(TEST_VIN, "origin_location")
    assert _default_enabled_for_unique_id(unique_id, TEST_VIN) is False


def test_unrecognised_unique_id_returns_empty() -> None:
    """A unique_id from some other integration/domain must not match."""
    assert _fields_for_unique_id("switch.some_other_thing", TEST_VIN) == set()
    assert _unique_id_suffix("switch.some_other_thing", TEST_VIN) is None
