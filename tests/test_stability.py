"""Regression tests for crashes: reload on token refresh, bad values, listener failures."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState, OptionsFlowWithReload
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_fleet_telemetry.config_flow import TeslaFleetTelemetryOptionsFlow
from custom_components.tesla_fleet_telemetry.const import (
    CONF_MQTT_HOST,
    CONF_MQTT_PORT,
    CONF_PROXY_URL,
    CONF_VIN,
    DESCRIPTIONS,
    DOMAIN,
)
from custom_components.tesla_fleet_telemetry.coordinator import TelemetryCoordinator
from custom_components.tesla_fleet_telemetry.fleet_config import _build_push_result
from custom_components.tesla_fleet_telemetry.mqtt_client import TeslaMQTTClient, _parse_value
from custom_components.tesla_fleet_telemetry.number import ChargeLimitNumber
from custom_components.tesla_fleet_telemetry.oauth2 import _get_oauth_session
from custom_components.tesla_fleet_telemetry.sensor import TelemetrySensor

from .conftest import TEST_VIN

_PKG = "custom_components.tesla_fleet_telemetry"


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_VIN,
        title=TEST_VIN,
        data={
            CONF_VIN: TEST_VIN,
            CONF_MQTT_HOST: "mqtt.local",
            CONF_MQTT_PORT: 1883,
            CONF_PROXY_URL: "https://proxy.local:4443",
            "auth_implementation": DOMAIN,
            "token": {"access_token": "old-token", "expires_at": 9999999999},
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def coordinator(hass: HomeAssistant, config_entry: MockConfigEntry) -> TelemetryCoordinator:
    return TelemetryCoordinator(hass, config_entry, TEST_VIN)


def _sensor(hass: HomeAssistant, coordinator: TelemetryCoordinator, key: str) -> TelemetrySensor:
    description = next(d for d in DESCRIPTIONS if d.key == key)
    entity = TelemetrySensor(coordinator, description, TEST_VIN)
    entity.hass = hass
    entity.entity_id = f"sensor.test_{key.lower()}"
    return entity


async def test_token_refresh_does_not_reload_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    mock_mqtt_client: MagicMock,
    mock_proxy_client: MagicMock,
) -> None:
    """A routine OAuth refresh rewrites entry.data; that must not tear the entry down."""
    with (
        patch(
            f"{_PKG}.config_entry_oauth2_flow.async_get_config_entry_implementation",
            AsyncMock(return_value=MagicMock()),
        ),
        patch(f"{_PKG}.async_get_valid_access_token", AsyncMock(return_value=("tok", {}))),
        patch(f"{_PKG}.TeslaMQTTClient", return_value=mock_mqtt_client) as mqtt_cls,
        patch(f"{_PKG}.TeslaProxyClient", return_value=mock_proxy_client),
        patch(f"{_PKG}._async_run_setup_bootstrap", AsyncMock()),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert config_entry.state is ConfigEntryState.LOADED

        hass.config_entries.async_update_entry(
            config_entry,
            data={
                **config_entry.data,
                "token": {"access_token": "new-token", "expires_at": 9999999999},
            },
        )
        await hass.async_block_till_done()

        assert config_entry.state is ConfigEntryState.LOADED
        assert mqtt_cls.call_count == 1
        mock_proxy_client.async_close.assert_not_awaited()
        mock_mqtt_client.async_disconnect.assert_not_awaited()


def test_options_flow_reloads_itself() -> None:
    assert issubclass(TeslaFleetTelemetryOptionsFlow, OptionsFlowWithReload)


def test_oauth_session_is_shared_per_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry
) -> None:
    implementation = MagicMock()
    first = _get_oauth_session(hass, config_entry, implementation)
    assert _get_oauth_session(hass, config_entry, implementation) is first
    assert _get_oauth_session(hass, config_entry, MagicMock()) is not first


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-09-24T10:00:00Z", datetime(2026, 9, 24, 10, tzinfo=UTC)),
        ("2026-09-24T10:00:00", datetime(2026, 9, 24, 10, tzinfo=UTC)),
        (1790244000, datetime.fromtimestamp(1790244000, UTC)),
        (1790244000000, datetime.fromtimestamp(1790244000, UTC)),
        ("garbage", None),
        ({"nested": 1}, None),
    ],
)
async def test_timestamp_sensor_state_never_raises(
    hass: HomeAssistant, coordinator: TelemetryCoordinator, raw: object, expected: object
) -> None:
    entity = _sensor(hass, coordinator, "ScheduledChargingStartTime")
    coordinator.async_update_field("ScheduledChargingStartTime", raw)

    assert entity.native_value == expected
    entity.state  # noqa: B018 - HA validates the value here and raised before the fix


@pytest.mark.parametrize("raw", ["not-a-number", {"a": 1}, [1, 2], True])
async def test_numeric_sensor_state_never_raises(
    hass: HomeAssistant, coordinator: TelemetryCoordinator, raw: object
) -> None:
    entity = _sensor(hass, coordinator, "MediaAudioVolume")
    coordinator.async_update_field("MediaAudioVolume", raw)

    assert entity.native_value is None
    entity.state  # noqa: B018


async def test_charge_limit_ignores_non_numeric(coordinator: TelemetryCoordinator) -> None:
    entity = ChargeLimitNumber(coordinator, TEST_VIN, MagicMock(), MagicMock(), MagicMock())
    coordinator.async_update_field("ChargeLimitSoc", "Unavailable")
    assert entity.native_value is None


async def test_failing_listener_does_not_block_others(
    coordinator: TelemetryCoordinator,
) -> None:
    calls: list[str] = []

    def _bad() -> None:
        raise ValueError("boom")

    coordinator.async_add_listener(_bad)
    coordinator.async_add_listener(lambda: calls.append("ok"))

    coordinator.async_update_listeners()

    assert calls == ["ok"]


def test_alert_timestamps_of_mixed_types_do_not_raise(
    coordinator: TelemetryCoordinator,
) -> None:
    coordinator.update_alert("A", {"StartedAt": "2026-09-24T10:00:00Z"})
    coordinator.update_alert("B", {"StartedAt": 1790244000})


@pytest.mark.parametrize("raw", ["inf", "-inf", "1e400", "nan"])
def test_parse_value_non_finite_is_none(raw: str) -> None:
    assert _parse_value(raw) is None


def test_mqtt_message_handler_never_raises(hass: HomeAssistant) -> None:
    """paho re-raises callback errors on its network thread, killing telemetry."""
    client = TeslaMQTTClient(hass, "mqtt.local", 1883, TEST_VIN, on_message=MagicMock())
    msg = SimpleNamespace(topic=f"telemetry/{TEST_VIN}/v/Soc", payload=b"80", retain=False)
    with patch.object(client, "_handle_mqtt_message", side_effect=KeyError("boom")):
        client._on_mqtt_message(None, None, msg)
    assert client._build_client().suppress_exceptions is True


def test_failed_push_is_not_sync_pending() -> None:
    result = _build_push_result({}, pushed=False, error="proxy down")
    assert result["sync_pending"] is False
    assert _build_push_result({}, pushed=True)["sync_pending"] is True
