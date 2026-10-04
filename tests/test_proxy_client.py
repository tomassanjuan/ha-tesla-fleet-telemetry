"""Tests for Tesla Fleet API exception -> HomeAssistantError mapping, and for
the seat/wheel comfort-command JSON passthrough regression.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.exceptions import HomeAssistantError
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
    plain JSON through the proxy's REST passthrough (`_raw_request`), not built
    as a signed protobuf command via the wrapper.
    """
    client = TeslaProxyClient.__new__(TeslaProxyClient)
    client._vin = TEST_VIN
    client._vehicle = MagicMock()
    client._raw_request = AsyncMock(return_value={"response": {"result": True}})

    await client._dispatch_command(endpoint, body)

    client._raw_request.assert_awaited_once_with(
        "POST", f"{FLEET_API_VERSION}/vehicles/{TEST_VIN}/command/{endpoint}", json=body
    )
    assert client._vehicle.method_calls == []


async def _start_proxy(hass, handler) -> tuple[TeslaProxyClient, TestServer]:
    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    server = TestServer(app)
    await server.start_server()
    client = TeslaProxyClient(hass, str(server.make_url("")), TEST_VIN)
    await client.async_setup()
    return client, server


async def test_typed_calls_use_current_token(hass, socket_enabled: None) -> None:
    """The token reaches the library through its public callable hook."""
    seen: list[str] = []

    async def handler(request: web.Request) -> web.Response:
        seen.append(request.headers["Authorization"])
        return web.json_response({"response": {"result": True}})

    client, server = await _start_proxy(hass, handler)
    try:
        await client.send_command("door_lock", None, "tok-1")
        await client.send_command("door_lock", None, "tok-2")
    finally:
        await client.async_close()
        await server.close()
    assert seen == ["Bearer tok-1", "Bearer tok-2"]


async def test_raw_request_posts_json_with_token(hass, socket_enabled: None) -> None:
    received: dict = {}

    async def handler(request: web.Request) -> web.Response:
        received["path"] = request.path
        received["auth"] = request.headers["Authorization"]
        received["body"] = await request.json()
        return web.json_response({"response": {"result": True}})

    client, server = await _start_proxy(hass, handler)
    try:
        client._set_token("tok")
        result = await client._raw_request("POST", "api/1/x", json={"a": 1, "b": None})
    finally:
        await client.async_close()
        await server.close()
    assert result == {"response": {"result": True}}
    assert received == {"path": "/api/1/x", "auth": "Bearer tok", "body": {"a": 1}}


async def test_raw_request_errors_map_like_typed_calls(hass, socket_enabled: None) -> None:
    async def handler(request: web.Request) -> web.Response:
        return web.json_response({"error": "vehicle unavailable"}, status=408)

    client, server = await _start_proxy(hass, handler)
    try:
        with pytest.raises(HomeAssistantError, match="(?i)offline"):
            await client.proxy_fleet_telemetry_errors_get("tok")
    finally:
        await client.async_close()
        await server.close()
