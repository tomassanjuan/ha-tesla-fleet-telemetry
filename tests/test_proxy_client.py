"""Tests for Tesla Fleet API exception -> HomeAssistantError mapping."""

from __future__ import annotations

from tesla_fleet_api.exceptions import DeviceUnexpectedResponse, Forbidden, VehicleOffline

from custom_components.tesla_fleet_telemetry.proxy_client import _map_fleet_exception


def test_vehicle_offline_maps_to_asleep_marker() -> None:
    """A real HTTP 408 must produce a message CommandEntity recognizes as 'asleep'.

    CommandEntity._ASLEEP_MARKERS triggers the wake-and-retry flow by substring
    match on the mapped error message, so this mapping must keep that substring.
    """
    result = _map_fleet_exception(VehicleOffline())
    assert "vehicle is offline" in str(result).lower()


def test_forbidden_mentions_oauth_scope() -> None:
    result = _map_fleet_exception(Forbidden())
    assert "scope" in str(result).lower()


def test_device_unexpected_response_mapped() -> None:
    result = _map_fleet_exception(DeviceUnexpectedResponse())
    assert "reboot" in str(result).lower() or "service" in str(result).lower()
