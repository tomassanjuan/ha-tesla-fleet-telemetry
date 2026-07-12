"""Shared value-normalization helpers for Tesla Fleet Telemetry platforms."""

from __future__ import annotations

import re
import ssl
from typing import Any

# Lowercase prefixes stripped before bool evaluation (Tesla HVAC enum prefixes).
_COERCE_BOOL_PREFIXES: tuple[str, ...] = (
    "hvacpowerstate",
    "hvacautomodestate",
)

_BOOL_TRUE: frozenset[str] = frozenset(
    {
        "on",
        "true",
        "enabled",
        "active",
        "open",
        "opened",
        "yes",
        "1",
        "locked",
        "precondition",
        "overheatprotect",
        "sentrymodestateon",
        "sentrymodestateidle",
        "sentrymodestatearmed",
        "sentrymodestateaware",
        "sentrymodestatepanic",
        "sentrymodestatequiet",
    }
)

_BOOL_FALSE: frozenset[str] = frozenset(
    {
        "off",
        "false",
        "disabled",
        "inactive",
        "closed",
        "no",
        "0",
        "unlocked",
        "sentrymodestateoff",
    }
)

_DOOR_KEY_MAP: dict[str, frozenset[str]] = {
    "DriverFront": frozenset({"df", "fd", "driver_front", "front_left"}),
    "PassengerFront": frozenset({"pf", "fp", "passenger_front", "front_right"}),
    "DriverRear": frozenset({"dr", "rd", "driver_rear", "rear_left"}),
    "PassengerRear": frozenset({"pr", "rp", "passenger_rear", "rear_right"}),
    "TrunkFront": frozenset({"ft", "frunk", "front_trunk"}),
    "TrunkRear": frozenset({"rt", "trunk", "rear_trunk", "liftgate"}),
}

_PEM_CERT_PATTERN = re.compile(
    r"-----BEGIN CERTIFICATE-----\s*(.*?)\s*-----END CERTIFICATE-----",
    re.DOTALL,
)


def coerce_bool(value: object) -> bool | None:
    """Convert a Tesla telemetry value to bool.

    Handles bool/int/float directly. For strings: strips quotes and whitespace,
    strips known HVAC enum prefixes, then checks against true/false string sets.
    Covers SentryMode, lock state, HVAC power state, cover open/close, and generic
    on/off/true/false values. Returns None for unknown or ambiguous values.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().strip('"').strip("'").lower()
        for prefix in _COERCE_BOOL_PREFIXES:
            if lowered.startswith(prefix):
                lowered = lowered[len(prefix) :]
                break
        lowered = lowered.strip()
        if lowered in _BOOL_TRUE:
            return True
        if lowered in _BOOL_FALSE:
            return False
    return None


def normalize_enum(value: object, prefixes: tuple[str, ...] = ()) -> str | None:
    """Strip quotes and optional CamelCase prefixes from a Tesla enum string.

    Prefix matching is case-sensitive to match Tesla's CamelCase wire format
    (e.g. "LockStateLocked" with prefix "LockState" → "Locked").
    Returns None if the result is empty or value is not a string.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().strip('"').strip("'")
    for prefix in prefixes:
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix) :]
            break
    normalized = normalized.strip()
    return normalized or None


def door_state_tokens(value: object) -> set[str]:
    """Parse a DoorState telemetry payload into a set of open-door tokens.

    Accepts the protobuf dict form (keys: DriverFront, PassengerFront, DriverRear,
    PassengerRear, TrunkFront, TrunkRear), a list of strings, or a raw string.
    Returns an empty set when value is None or indicates all doors closed.
    """
    if value is None:
        return set()
    if isinstance(value, dict):
        tokens: set[str] = set()
        for key, aliases in _DOOR_KEY_MAP.items():
            raw = value.get(key)
            if isinstance(raw, bool | int | float) and bool(raw):
                tokens.update(aliases)
        return tokens
    if isinstance(value, list):
        text = " ".join(item for item in value if isinstance(item, str))
    else:
        text = str(value)
    normalized_text = text.strip().strip('"').strip("'").lower()
    if not normalized_text or normalized_text in {"closed", "none", "null", "unknown"}:
        return set()
    return set(re.findall(r"[a-z0-9_]+", normalized_text))


def normalize_pem_cert_bundle(value: object) -> str:
    """Normalize one or more PEM certificates into a canonical bundle string."""
    if not isinstance(value, str):
        raise ValueError("invalid_ca")

    normalized = value.strip()
    if "\\n" in normalized and "\n" not in normalized:
        normalized = normalized.replace("\\n", "\n")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")

    matches = list(_PEM_CERT_PATTERN.finditer(normalized))
    if not matches:
        raise ValueError("invalid_ca")

    normalized_blocks: list[str] = []
    for match in matches:
        body = re.sub(r"\s+", "", match.group(1))
        if not body:
            raise ValueError("invalid_ca")
        wrapped_body = "\n".join(body[idx : idx + 64] for idx in range(0, len(body), 64))
        pem = f"-----BEGIN CERTIFICATE-----\n{wrapped_body}\n-----END CERTIFICATE-----"
        try:
            ssl.PEM_cert_to_DER_cert(f"{pem}\n")
        except ValueError as err:
            raise ValueError("invalid_ca") from err
        normalized_blocks.append(pem)

    return "\n".join(normalized_blocks) + "\n"


def der_cert_chain_to_pem_bundle(chain: object) -> str:
    """Convert one DER certificate or a chain into a normalized PEM bundle string."""
    if not isinstance(chain, list | tuple) or not chain:
        raise ValueError("invalid_ca")

    pem_blocks: list[str] = []
    for cert_der in chain:
        if not isinstance(cert_der, bytes):
            raise ValueError("invalid_ca")
        try:
            pem_blocks.append(ssl.DER_cert_to_PEM_cert(cert_der))
        except ValueError as err:
            raise ValueError("invalid_ca") from err

    return normalize_pem_cert_bundle("".join(pem_blocks))


def extract_first_fields_config(payload: Any) -> dict[str, Any] | None:
    """Find first dict that looks like a fleet telemetry config (contains `fields` dict)."""
    if isinstance(payload, dict):
        fields = payload.get("fields")
        if isinstance(fields, dict):
            return payload
        for value in payload.values():
            extracted = extract_first_fields_config(value)
            if extracted is not None:
                return extracted
    elif isinstance(payload, list):
        for item in payload:
            extracted = extract_first_fields_config(item)
            if extracted is not None:
                return extracted
    return None
