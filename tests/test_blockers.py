"""Blocking Fleet Telemetry errors must surface as repair issues."""

from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.helpers import issue_registry as ir

from custom_components.tesla_fleet_telemetry.const import DOMAIN
from custom_components.tesla_fleet_telemetry.fleet_config import (
    _classify_blocker,
    _log_push_response_skipped,
    _sync_stream_blockers,
)

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
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"blocker_tls_cert_expired_{TEST_VIN}") is None


def test_skipped_firmware_raises(hass) -> None:
    resp = {"response": {"skipped_vehicles": {"unsupported_firmware": [TEST_VIN]}}}
    _log_push_response_skipped(TEST_VIN, resp, hass)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"blocker_unsupported_firmware_{TEST_VIN}")
