"""Regression test: seat/wheel comfort commands must go out as plain JSON.

The signed Vehicle Command Protocol wrapper methods (tesla-fleet-api's
`v.remote_seat_heater_request()` etc.) were rejected by some vehicles with
InvalidRequest/InvalidCommand. These endpoints must instead be posted as
plain JSON through the proxy's REST passthrough (`a._request`), not built
as a signed protobuf command via the wrapper.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.tesla_fleet_telemetry.const import FLEET_API_VERSION
from custom_components.tesla_fleet_telemetry.proxy_client import TeslaProxyClient

from .conftest import TEST_VIN

_JSON_PASSTHROUGH_ENDPOINTS = [
    ("remote_seat_heater_request", {"seat_position": 0, "level": 1}),
    ("remote_seat_cooler_request", {"seat_position": 1, "seat_cooler_level": 1}),
    ("remote_auto_seat_climate_request", {"auto_seat_position": 1, "auto_climate_on": True}),
    ("remote_steering_wheel_heater_request", {"on": True}),
    ("remote_steering_wheel_heat_level_request", {"level": 1}),
]


@pytest.mark.parametrize(("endpoint", "body"), _JSON_PASSTHROUGH_ENDPOINTS)
async def test_seat_and_wheel_commands_use_json_passthrough(
    endpoint: str, body: dict
) -> None:
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
