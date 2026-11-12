"""Regression tests for fleet config push retries and entity command/state fixes."""

from __future__ import annotations

import asyncio
import datetime as dt
import json
from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_fleet_telemetry.const import (
    CONF_TELEMETRY_SERVER_CA,
    CONF_TELEMETRY_SERVER_HOSTNAME,
    CONF_TELEMETRY_SERVER_PORT,
    DOMAIN,
)
from custom_components.tesla_fleet_telemetry.coordinator import TelemetryCoordinator
from custom_components.tesla_fleet_telemetry.cover import TeslaWindowsCover
from custom_components.tesla_fleet_telemetry.fleet_config import _push_telemetry_config
from custom_components.tesla_fleet_telemetry.media_player import TeslaMediaPlayer
from custom_components.tesla_fleet_telemetry.number import ChargeAmpsNumber
from custom_components.tesla_fleet_telemetry.policy import TelemetryPolicyContext
from custom_components.tesla_fleet_telemetry.proxy_client import TeslaProxyClient
from custom_components.tesla_fleet_telemetry.update import TeslaFirmwareUpdate

from .conftest import TEST_VIN


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, unique_id=TEST_VIN)
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinator(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> Generator[TelemetryCoordinator]:
    coord = TelemetryCoordinator(hass, config_entry, TEST_VIN)
    yield coord
    # Field updates arm a debounce timer; HA's test harness fails on lingering timers.
    coord.async_cancel_pending_update()


def _self_signed_pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "telemetry.example.com")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + dt.timedelta(days=1))
        .sign(key, hashes.SHA256())
    )
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def _policy_context(ca: str) -> TelemetryPolicyContext:
    return TelemetryPolicyContext(
        telemetry_mode="normal",
        config={
            CONF_TELEMETRY_SERVER_HOSTNAME: "telemetry.example.com",
            CONF_TELEMETRY_SERVER_PORT: 443,
            CONF_TELEMETRY_SERVER_CA: ca,
        },
    )


def _unsupported_interval_error(field: str, authorized_ms: int) -> HomeAssistantError:
    return HomeAssistantError(
        json.dumps({"Tags": {"field_name": field, "authorized": str(authorized_ms)}})
    )


async def test_push_retries_each_unsupported_interval_field() -> None:
    """Tesla rejects one field per request; every rejected field must be retried."""
    proxy = MagicMock()
    proxy.proxy_fleet_telemetry_set = AsyncMock(
        side_effect=[
            _unsupported_interval_error("Soc", 30000),
            _unsupported_interval_error("Odometer", 60000),
            {"response": {"updated_vehicles": 1}},
        ]
    )

    result = await _push_telemetry_config(
        proxy,
        TEST_VIN,
        "token",
        {"Soc", "Odometer"},
        _policy_context(_self_signed_pem()),
        wait_for_sync=False,
    )

    assert result["pushed"] is True
    assert result["authorized_interval_overrides"] == {"Soc": 30, "Odometer": 60}
    sent_fields = proxy.proxy_fleet_telemetry_set.await_args.args[0]["fields"]
    assert sent_fields["Soc"]["interval_seconds"] == 30
    assert sent_fields["Odometer"]["interval_seconds"] == 60


async def test_push_reports_learned_overrides_even_on_failure() -> None:
    proxy = MagicMock()
    proxy.proxy_fleet_telemetry_set = AsyncMock(
        side_effect=[_unsupported_interval_error("Soc", 30000), OSError("proxy down")]
    )

    result = await _push_telemetry_config(
        proxy,
        TEST_VIN,
        "token",
        {"Soc"},
        _policy_context(_self_signed_pem()),
        wait_for_sync=False,
    )

    assert result["pushed"] is False
    assert result["authorized_interval_overrides"] == {"Soc": 30}


async def test_push_invalid_ca_raises_home_assistant_error() -> None:
    """A bad stored CA must surface as a HomeAssistantError, not a raw ValueError."""
    proxy = MagicMock()
    proxy.proxy_fleet_telemetry_set = AsyncMock()

    with pytest.raises(HomeAssistantError, match="CA certificate"):
        await _push_telemetry_config(
            proxy, TEST_VIN, "token", {"Soc"}, _policy_context("not a cert"), wait_for_sync=False
        )
    proxy.proxy_fleet_telemetry_set.assert_not_awaited()


async def test_window_close_sends_vehicle_location(coordinator: TelemetryCoordinator) -> None:
    """Tesla requires lat/lon when closing windows."""
    cover = TeslaWindowsCover(coordinator, TEST_VIN, MagicMock(), MagicMock(), MagicMock())
    cover._send_command = AsyncMock(return_value={})
    coordinator.async_update_field("Location", {"latitude": 1.5, "longitude": 2.5})

    await cover.async_close_cover()

    cover._send_command.assert_awaited_once_with(
        "window_control", {"command": "close", "lat": 1.5, "lon": 2.5}
    )


def test_media_position_timestamp_only_changes_with_position(
    coordinator: TelemetryCoordinator,
) -> None:
    """An unrelated field update must not bump media_position_updated_at."""
    player = TeslaMediaPlayer(coordinator, TEST_VIN, MagicMock(), MagicMock(), MagicMock())
    coordinator.async_update_field("MediaNowPlayingElapsed", 5000)
    player._refresh_position()
    stamped = player.media_position_updated_at
    assert player.media_position == 5
    assert stamped is not None

    coordinator.async_update_field("MediaAudioVolume", 4)
    player._refresh_position()
    assert player.media_position_updated_at is stamped

    coordinator.async_update_field("MediaNowPlayingElapsed", 9000)
    player._refresh_position()
    assert player.media_position == 9


async def test_media_volume_uses_full_range(coordinator: TelemetryCoordinator) -> None:
    player = TeslaMediaPlayer(coordinator, TEST_VIN, MagicMock(), MagicMock(), MagicMock())
    player._send_command = AsyncMock(return_value={})
    player.async_write_ha_state = MagicMock()

    await player.async_set_volume_level(1.0)

    player._send_command.assert_awaited_once_with("adjust_volume", {"volume": 11.0})


def test_update_not_in_progress_without_pending_update(
    coordinator: TelemetryCoordinator,
) -> None:
    entity = TeslaFirmwareUpdate(coordinator, TEST_VIN, MagicMock(), MagicMock(), MagicMock())
    coordinator.async_update_field("Version", "2025.20.1")
    coordinator.async_update_field("SoftwareUpdateDownloadPercentComplete", 0)
    assert entity.in_progress is False
    assert entity.update_percentage is None

    coordinator.async_update_field("SoftwareUpdateVersion", "2025.26.3")
    assert entity.in_progress is True
    assert entity.update_percentage == 0


def test_charge_amps_max_not_capped_at_32(coordinator: TelemetryCoordinator) -> None:
    number = ChargeAmpsNumber(coordinator, TEST_VIN, MagicMock(), MagicMock(), MagicMock())
    number.hass = coordinator.hass
    number._on_max_changed("ChargeCurrentRequestMax", 48)
    assert number.native_max_value == 48.0
    if number._pending_max_update is not None:
        number._pending_max_update.cancel()


async def test_cancelled_wake_caller_does_not_start_second_wake(hass: HomeAssistant) -> None:
    client = TeslaProxyClient(hass, "https://proxy.local", TEST_VIN)
    release = asyncio.Event()
    calls = 0

    async def _fake_wake(_token: str) -> dict:
        nonlocal calls
        calls += 1
        await release.wait()
        return {"response": {"state": "online"}}

    client._do_wake_vehicle = _fake_wake  # type: ignore[method-assign]

    first = hass.async_create_task(client.wake_vehicle("token"))
    await asyncio.sleep(0)
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)

    second = hass.async_create_task(client.wake_vehicle("token"))
    await asyncio.sleep(0)
    release.set()
    assert await second == {"response": {"state": "online"}}
    assert calls == 1
    await hass.async_block_till_done()
    assert client._wake_task is None


def test_charge_port_interval_respects_tesla_minimum() -> None:
    """Tesla only authorizes ChargePort at >=60s; asking for 1s spams MQTT errors."""
    from custom_components.tesla_fleet_telemetry.policy import default_field_policy

    for mode in ("realtime", "normal", "low"):
        policy = default_field_policy("ChargePort", TelemetryPolicyContext(telemetry_mode=mode))
        assert policy["interval_seconds"] == 60
