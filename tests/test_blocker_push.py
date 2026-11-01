"""A config push invalidates telemetry errors reported before it."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

from homeassistant.helpers import issue_registry as ir

from custom_components.tesla_fleet_telemetry.const import DOMAIN
from custom_components.tesla_fleet_telemetry.fleet_config import (
    _do_push_and_sync,
    _sync_stream_blockers,
)

from .conftest import TEST_VIN


async def test_push_ignores_errors_from_the_previous_config(hass) -> None:
    """An error reported just before a push (old CA, car asleep) must not raise a repair."""
    before = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    err = {"error": "x509: certificate signed by unknown authority", "created_at": before}
    issue = f"blocker_tls_cert_invalid_{TEST_VIN}"

    _sync_stream_blockers(hass, TEST_VIN, [err])  # no push yet: counts
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue)

    proxy = AsyncMock()
    proxy.proxy_fleet_telemetry_set.return_value = {"response": {"updated_vehicles": 1}}
    await _do_push_and_sync(proxy, TEST_VIN, "tok", {}, {}, False, None, None, hass)

    _sync_stream_blockers(hass, TEST_VIN, [err])  # same error, now older than the push
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue) is None
