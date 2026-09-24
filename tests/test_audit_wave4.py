"""Tests for region detection, telemetry CA chain handling and MQTT auth wiring."""

from __future__ import annotations

import asyncio
import datetime as dt
import ipaddress
import socket
import ssl
import threading
from collections.abc import Generator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from tesla_fleet_api.exceptions import InternalServerError, InvalidToken

from custom_components.tesla_fleet_telemetry import oauth2
from custom_components.tesla_fleet_telemetry.climate import TeslaClimate
from custom_components.tesla_fleet_telemetry.const import (
    MQTT_BACKOFF_BASE_SECONDS,
    MQTT_BACKOFF_MAX_SECONDS,
)
from custom_components.tesla_fleet_telemetry.coordinator import TelemetryCoordinator
from custom_components.tesla_fleet_telemetry.mqtt_client import TeslaMQTTClient
from custom_components.tesla_fleet_telemetry.proxy_client import TeslaProxyClient
from custom_components.tesla_fleet_telemetry.utils import _fetch_server_ca_blocking

from .conftest import TEST_VIN


async def test_vin_fetch_uses_detected_region(hass) -> None:
    """The VIN list must come from the account's regional host, which is returned too."""
    na = "https://fleet-api.prd.na.vn.cloud.tesla.com"
    api = MagicMock()
    api.server = oauth2.FLEET_API_BASE

    async def _find_server() -> str:
        api.server = na
        return "na"

    api.find_server = _find_server
    api._request = AsyncMock(return_value={"response": [{"vin": TEST_VIN.lower()}]})

    with patch.object(oauth2, "TeslaFleetApi", return_value=api):
        vins, api_base = await oauth2.async_fetch_available_vins(hass, "token")

    assert vins == [TEST_VIN]
    assert api_base == na


def _cert(
    subject: str,
    key: ec.EllipticCurvePrivateKey,
    issuer: x509.Name | None = None,
    issuer_key: ec.EllipticCurvePrivateKey | None = None,
    *,
    ca: bool,
) -> x509.Certificate:
    now = dt.datetime.now(dt.UTC)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, subject)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(issuer or name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=1))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        # Python 3.13's default context enforces X.509 strict mode, which requires these.
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key((issuer_key or key).public_key()),
            critical=False,
        )
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
    return builder.sign(issuer_key or key, hashes.SHA256())


@pytest.fixture
def tls_server(tmp_path: Path, socket_enabled: None) -> Generator[tuple[int, Path]]:
    """Serve a CA-signed leaf on 127.0.0.1; yield (port, ca_pem_path)."""
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_cert = _cert("Test Root", ca_key, ca=True)
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf_cert = _cert("127.0.0.1", leaf_key, ca_cert.subject, ca_key, ca=False)

    ca_path = tmp_path / "ca.pem"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path = tmp_path / "leaf.pem"
    cert_path.write_bytes(leaf_cert.public_bytes(serialization.Encoding.PEM))
    key_path = tmp_path / "leaf.key"
    key_path.write_bytes(
        leaf_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)
    listener = socket.create_server(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    stop = threading.Event()

    def _serve() -> None:
        listener.settimeout(0.2)
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except TimeoutError:
                continue
            try:
                with server_ctx.wrap_socket(conn, server_side=True) as tls:
                    tls.recv(1)
            except (OSError, ssl.SSLError):
                pass

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    yield port, ca_path
    stop.set()
    thread.join(timeout=2)
    listener.close()


def test_trusted_server_returns_full_chain(tls_server: tuple[int, Path]) -> None:
    """A trusted server's CA must include the root, not just the renewable leaf."""
    port, ca_path = tls_server
    trusting_ctx = ssl.create_default_context(cafile=str(ca_path))

    with patch(
        "custom_components.tesla_fleet_telemetry.utils.ssl.create_default_context",
        return_value=trusting_ctx,
    ):
        pem, trusted = _fetch_server_ca_blocking("127.0.0.1", port)

    assert trusted is True
    assert pem.count("BEGIN CERTIFICATE") == 2
    assert ca_path.read_text().split("-----BEGIN CERTIFICATE-----")[1][:40] in pem


def test_untrusted_server_returns_leaf_only(tls_server: tuple[int, Path]) -> None:
    port, _ = tls_server
    pem, trusted = _fetch_server_ca_blocking("127.0.0.1", port)
    assert trusted is False
    assert pem.count("BEGIN CERTIFICATE") == 1


def test_mqtt_client_applies_credentials_and_tls(hass) -> None:
    tls_ctx = ssl.create_default_context()
    client = TeslaMQTTClient(
        hass,
        host="mqtt.local",
        port=8883,
        vin=TEST_VIN,
        on_message=MagicMock(),
        username="user",
        password="secret",
        ssl_context=tls_ctx,
        client_id="tesla_fleet_telemetry_test_abcd1234",
    )
    paho = client._build_client()
    assert paho._username == b"user"
    assert paho._password == b"secret"
    assert paho._ssl_context is tls_ctx
    assert paho._client_id == b"tesla_fleet_telemetry_test_abcd1234"


def test_mqtt_client_defaults_to_anonymous(hass) -> None:
    client = TeslaMQTTClient(
        hass, host="mqtt.local", port=1883, vin=TEST_VIN, on_message=MagicMock()
    )
    paho = client._build_client()
    assert paho._username is None
    assert paho._ssl_context is None


async def test_vin_fetch_maps_rejected_token_to_auth_error(hass) -> None:
    """Reauth relies on the exception type, not on the message text."""
    api = MagicMock()
    api.find_server = AsyncMock(side_effect=InvalidToken())

    with (
        patch.object(oauth2, "TeslaFleetApi", return_value=api),
        pytest.raises(oauth2.TeslaAuthError),
    ):
        await oauth2.async_fetch_available_vins(hass, "token")


async def test_vin_fetch_server_error_is_not_auth_error(hass) -> None:
    api = MagicMock()
    api.find_server = AsyncMock(side_effect=InternalServerError())

    with patch.object(oauth2, "TeslaFleetApi", return_value=api):
        with pytest.raises(HomeAssistantError) as exc_info:
            await oauth2.async_fetch_available_vins(hass, "token")
    assert not isinstance(exc_info.value, oauth2.TeslaAuthError)


@pytest.mark.parametrize("verify", [False, True])
async def test_proxy_client_tls_verification_toggle(hass, verify: bool) -> None:
    client = TeslaProxyClient(hass, "https://proxy.local:4443", TEST_VIN, verify_ssl=verify)
    await client.async_setup()
    try:
        ctx = client._session.connector._ssl  # type: ignore[union-attr]
        assert ctx.verify_mode == (ssl.CERT_REQUIRED if verify else ssl.CERT_NONE)
    finally:
        await client.async_close()


async def test_set_temperature_keeps_passenger_setpoint(hass) -> None:
    entry = MockConfigEntry(domain="tesla_fleet_telemetry", unique_id=TEST_VIN)
    entry.add_to_hass(hass)
    coordinator = TelemetryCoordinator(hass, entry, TEST_VIN)
    climate = TeslaClimate(coordinator, TEST_VIN, MagicMock(), MagicMock(), entry)
    climate._send_command = AsyncMock(return_value={})
    coordinator.async_update_field("HvacRightTemperatureRequest", 19.5)

    await climate.async_set_temperature(temperature=22.0)

    climate._send_command.assert_awaited_once_with(
        "set_temps", {"driver_temp": 22.0, "passenger_temp": 19.5}
    )
    coordinator.async_cancel_pending_update()


def test_mqtt_client_lets_paho_own_reconnect(hass) -> None:
    client = TeslaMQTTClient(
        hass, host="mqtt.local", port=1883, vin=TEST_VIN, on_message=MagicMock()
    )
    paho = client._build_client()
    assert paho._reconnect_min_delay == MQTT_BACKOFF_BASE_SECONDS
    assert paho._reconnect_max_delay == MQTT_BACKOFF_MAX_SECONDS
    assert not hasattr(client, "_schedule_reconnect")


async def test_mqtt_connect_fail_fails_setup_fast(hass) -> None:
    """A refused connection must fail setup at once, not after the full timeout."""
    client = TeslaMQTTClient(
        hass,
        host="mqtt.local",
        port=1883,
        vin=TEST_VIN,
        on_message=MagicMock(),
        timeout_seconds=30,
    )
    paho = MagicMock()
    paho.is_connected.return_value = False
    paho.connect_async.side_effect = lambda *a, **kw: client._on_connect_fail(paho, None)

    with patch.object(client, "_build_client", return_value=paho):
        async with asyncio.timeout(2):
            with pytest.raises(OSError, match="connection failed"):
                await client.async_connect()
    await client.async_disconnect()
