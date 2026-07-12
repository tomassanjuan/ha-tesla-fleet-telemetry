"""Shared fixtures for Tesla Fleet Telemetry tests."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest_plugins = "pytest_homeassistant_custom_component"

TEST_VIN = "5YJ3E1EA1JF000001"
TEST_VIN_2 = "5YJ3E1EA1JF000002"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Enable custom integrations for every test in this suite."""
    yield


@pytest.fixture
def mock_mqtt_client() -> Generator[MagicMock]:
    """Mock TeslaMQTTClient so tests never open a real socket."""
    client = MagicMock()
    client.async_connect = AsyncMock()
    client.async_disconnect = AsyncMock()
    yield client


@pytest.fixture
def mock_proxy_client() -> Generator[MagicMock]:
    """Mock TeslaProxyClient so tests never make a real HTTP call."""
    client = MagicMock()
    client.async_setup = AsyncMock()
    client.async_close = AsyncMock()
    client.ready = True
    yield client
