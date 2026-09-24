"""Tests for region detection, telemetry CA chain handling and MQTT auth wiring."""

from __future__ import annotations

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

from custom_components.tesla_fleet_telemetry import oauth2
from custom_components.tesla_fleet_telemetry.mqtt_client import TeslaMQTTClient
from custom_components.tesla_fleet_telemetry.utils import _fetch_server_ca_blocking

from .conftest import TEST_VIN


async def test_vin_fetch_uses_detected_region() -> None:
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
        vins, api_base = await oauth2.async_fetch_available_vins("token")

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
