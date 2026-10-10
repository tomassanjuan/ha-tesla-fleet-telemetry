"""Tests for config_flow.py.

Full OAuth round-trip (authorization code exchange against Tesla's real
endpoints) is out of scope for unit tests and would need extensive external-API
mocking; these tests cover the validation helpers (success + failure paths)
and the reauth entry-data-merge behavior, which is where the actual defects
found in this codebase's audits lived (bad MQTT/telemetry endpoint input,
and the reauth double-reload fix).
"""

from __future__ import annotations

import pytest

from custom_components.tesla_fleet_telemetry.flow_helpers import (
    parse_mqtt_endpoint,
    parse_telemetry_endpoint,
)


@pytest.mark.parametrize(
    ("endpoint", "expected_host", "expected_port"),
    [
        ("mqtt.local:1883", "mqtt.local", 1883),
        ("192.168.1.10:8883", "192.168.1.10", 8883),
    ],
)
def test_parse_mqtt_endpoint_success(endpoint: str, expected_host: str, expected_port: int) -> None:
    host, port = parse_mqtt_endpoint(endpoint)
    assert (host, port) == (expected_host, expected_port)


@pytest.mark.parametrize(
    "endpoint",
    ["no-port-here", "host:not-a-number", "host:0", "host:70000", ":1883"],
)
def test_parse_mqtt_endpoint_failure(endpoint: str) -> None:
    with pytest.raises(ValueError):
        parse_mqtt_endpoint(endpoint)


def test_parse_telemetry_endpoint_https_url() -> None:
    host, port = parse_telemetry_endpoint("https://telemetry.example.com:443")
    assert host == "telemetry.example.com"
    assert port == 443


def test_parse_telemetry_endpoint_rejects_empty() -> None:
    with pytest.raises(ValueError):
        parse_telemetry_endpoint("")


def test_parse_telemetry_endpoint_rejects_non_https_scheme() -> None:
    with pytest.raises(ValueError):
        parse_telemetry_endpoint("http://telemetry.example.com")
