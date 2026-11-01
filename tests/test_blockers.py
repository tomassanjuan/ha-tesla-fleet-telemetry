"""Blocking Fleet Telemetry errors must surface as repair issues."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tesla_fleet_telemetry.const import (
    CONF_TELEMETRY_SERVER_CA,
    CONF_TELEMETRY_SERVER_HOSTNAME,
    CONF_TELEMETRY_SERVER_PORT,
    DOMAIN,
)
from custom_components.tesla_fleet_telemetry.fleet_config import (
    _classify_blocker,
    _log_push_response_skipped,
    _sync_stream_blockers,
)
from custom_components.tesla_fleet_telemetry.repairs import BlockerRepairFlow
from custom_components.tesla_fleet_telemetry.utils import _pinned_chain

from .conftest import TEST_VIN

EXPIRED = (
    "webconnection error: tls: failed to verify certificate: x509: certificate has expired "
    "or is not yet valid"
)


def test_classify_blocker() -> None:
    assert _classify_blocker(EXPIRED) == "tls_cert_expired"
    assert _classify_blocker("x509: certificate signed by unknown authority") == "tls_cert_invalid"
    assert _classify_blocker("dial tcp: connection refused") == "server_unreachable"
    assert _classify_blocker("vehicle asleep") is None


def test_expired_cert_raises_then_clears(hass) -> None:
    now = datetime.now(UTC).isoformat()
    err = {"error": EXPIRED, "created_at": now, "hostname": "h", "port": 19855}
    _sync_stream_blockers(hass, TEST_VIN, [err])
    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"blocker_tls_cert_expired_{TEST_VIN}")

    _sync_stream_blockers(hass, TEST_VIN, [])
    assert registry.async_get_issue(DOMAIN, f"blocker_tls_cert_expired_{TEST_VIN}") is None


def test_stale_error_is_ignored(hass) -> None:
    err = {"error": EXPIRED, "created_at": "2020-01-01T00:00:00Z"}
    _sync_stream_blockers(hass, TEST_VIN, [err])
    assert (
        ir.async_get(hass).async_get_issue(DOMAIN, f"blocker_tls_cert_expired_{TEST_VIN}") is None
    )


def test_skipped_firmware_raises(hass) -> None:
    resp = {"response": {"skipped_vehicles": {"unsupported_firmware": [TEST_VIN]}}}
    _log_push_response_skipped(TEST_VIN, resp, hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"blocker_unsupported_firmware_{TEST_VIN}")


def test_pinned_chain_keeps_only_the_issuing_ca() -> None:
    assert _pinned_chain([b"leaf", b"intermediate", b"root"]) == [b"intermediate"]
    assert _pinned_chain([b"leaf", b"root"]) == [b"root"]
    assert _pinned_chain([b"self-signed"]) == [b"self-signed"]


async def _run_reload_flow(hass, stored_ca: str, fetched: tuple[str, bool]):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=TEST_VIN,
        data={
            CONF_TELEMETRY_SERVER_HOSTNAME: "ft.example.com",
            CONF_TELEMETRY_SERVER_PORT: 19855,
            CONF_TELEMETRY_SERVER_CA: stored_ca,
        },
    )
    entry.add_to_hass(hass)
    issue_id = f"blocker_tls_cert_invalid_{TEST_VIN}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.ERROR,
        translation_key="blocker_tls_cert_invalid",
        data={"vin": TEST_VIN},
    )
    flow = BlockerRepairFlow()
    flow.hass = hass
    flow.issue_id = issue_id
    shown = await flow.async_step_init()
    assert shown["type"] == "form"
    reload = AsyncMock()
    with (
        patch(
            "custom_components.tesla_fleet_telemetry.repairs._find_entry_for_vin",
            return_value=(entry.entry_id, object()),
        ),
        patch(
            "custom_components.tesla_fleet_telemetry.repairs.async_fetch_server_ca",
            AsyncMock(return_value=fetched),
        ),
        patch.object(hass.config_entries, "async_reload", reload),
    ):
        result = await flow.async_step_init({})
    reload.assert_awaited_once_with(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None
    return result


async def test_reload_repair_warns_when_ca_changed(hass) -> None:
    result = await _run_reload_flow(
        hass,
        "",
        ("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", True),
    )
    assert result["reason"] == "reloaded_ca_changed"


async def test_reload_repair_untrusted_fetch_is_not_a_ca_change(hass) -> None:
    result = await _run_reload_flow(
        hass,
        "",
        ("-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n", False),
    )
    assert result["reason"] == "reloaded"
