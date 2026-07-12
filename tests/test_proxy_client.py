"""Tests for Tesla Fleet API exception -> HomeAssistantError mapping, and for
the seat/wheel comfort-command JSON passthrough regression.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from tesla_fleet_api.exceptions import DeviceUnexpectedResponse, Forbidden, VehicleOffline

from custom_components.tesla_fleet_telemetry.const import FLEET_API_VERSION
from custom_components.tesla_fleet_telemetry.proxy_client import (
    TeslaProxyClient,
    _map_fleet_exception,
)

from .conftest import TEST_VIN


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


_JSON_PASSTHROUGH_ENDPOINTS = [
    ("remote_seat_heater_request", {"seat_position": 0, "level": 1}),
    ("remote_seat_cooler_request", {"seat_position": 1, "seat_cooler_level": 1}),
    ("remote_auto_seat_climate_request", {"auto_seat_position": 1, "auto_climate_on": True}),
    ("remote_steering_wheel_heater_request", {"on": True}),
    ("remote_steering_wheel_heat_level_request", {"level": 1}),
    ("remote_auto_steering_wheel_heat_climate_request", {"on": True}),
]


@pytest.mark.parametrize(("endpoint", "body"), _JSON_PASSTHROUGH_ENDPOINTS)
async def test_seat_and_wheel_commands_use_json_passthrough(endpoint: str, body: dict) -> None:
    """The signed Vehicle Command Protocol wrapper methods (tesla-fleet-api's
    `v.remote_seat_heater_request()` etc.) were rejected by some vehicles with
    InvalidRequest/InvalidCommand. These endpoints must instead be posted as
    plain JSON through the proxy's REST passthrough (`a._request`), not built
    as a signed protobuf command via the wrapper.
    """
    client = TeslaProxyClient.__new__(TeslaProxyClient)
    client._vin = TEST_VIN
    client._vehicle = MagicMock()
    client._api = MagicMock()
    client._api._request = AsyncMock(return_value={"response": {"result": True}})

    await client._dispatch_command(endpoint, body)

    client._api._request.assert_awaited_once_with(
        "POST", f"{FLEET_API_VERSION}/vehicles/{TEST_VIN}/command/{endpoint}", json=body
    )
    assert client._vehicle.method_calls == []
