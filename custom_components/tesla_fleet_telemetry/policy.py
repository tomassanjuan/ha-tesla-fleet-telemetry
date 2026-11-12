"""Telemetry policy helpers for fleet_telemetry_config generation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, TypeAlias

from .const import (
    DEFAULT_LOCATION_MINIMUM_DELTA_METERS,
    DEFAULT_RESEND_INTERVAL_SECONDS,
    DEFAULT_TELEMETRY_MODE,
    DEVICE_METADATA_FIELDS,
    MIN_INTERVAL_SECONDS,
    RUNTIME_INTERVAL_OVERRIDES,
    STATIC_METADATA_INTERVAL_SECONDS,
    TelemetryMode,
)
from .descriptions import (
    FIELD_DEFS,
    LOCATION_FIELDS,
)
from .polling_profiles import INSTANT_FIELDS, POLLING_PROFILES, field_group_for_desc

# Type aliases for telemetry policies
FieldPolicy: TypeAlias = dict[str, int | float]


@dataclass(frozen=True)
class TelemetryPolicyContext:
    """Inputs required to resolve fleet telemetry field policy."""

    telemetry_mode: TelemetryMode = DEFAULT_TELEMETRY_MODE  # type: ignore[assignment]
    config: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.telemetry_mode not in POLLING_PROFILES:
            raise ValueError(f"Unknown telemetry_mode: {self.telemetry_mode!r}")

    @property
    def policy_key(self) -> str:
        """Return a stable key describing the currently selected policy."""
        return self.telemetry_mode


def precision_to_raw_delta(desc: Any) -> float | None:
    """Compute minimum_delta from display precision, adjusted for value_multiplier."""
    if getattr(desc, "binary", True):
        return None
    precision = getattr(desc, "suggested_display_precision", None)
    if precision is None:
        return None
    delta = 10.0 ** (-int(precision))
    multiplier = getattr(desc, "value_multiplier", None)
    if multiplier:
        delta = delta / abs(multiplier)
    return delta


def minimum_delta_for_field(field: str, desc: Any, context: TelemetryPolicyContext) -> float | None:
    """Resolve minimum_delta from field, field group, and option context."""
    if field in INSTANT_FIELDS:
        if not getattr(desc, "binary", True):
            override = desc.minimum_delta_override
            if override != "auto":
                return float(override)
        return None

    if getattr(desc, "binary", False):
        return None

    if field in LOCATION_FIELDS:
        return DEFAULT_LOCATION_MINIMUM_DELTA_METERS

    if not getattr(desc, "binary", True):
        override = desc.minimum_delta_override
        if override != "auto":
            return float(override)

    base_delta = precision_to_raw_delta(desc)
    if base_delta is None:
        return base_delta

    group = field_group_for_desc(desc)
    multiplier = POLLING_PROFILES[context.telemetry_mode][group]["delta_multiplier"]
    if multiplier is None:
        return None
    return float(base_delta * multiplier)


def interval_seconds_for_field(field: str, desc: Any, context: TelemetryPolicyContext) -> int:
    """Resolve interval_seconds from field group and option context."""
    runtime_interval_overrides = (context.config or {}).get(RUNTIME_INTERVAL_OVERRIDES, {})
    if isinstance(runtime_interval_overrides, dict):
        override = runtime_interval_overrides.get(field)
        if isinstance(override, int):
            return override

    if field in MIN_INTERVAL_SECONDS:
        return MIN_INTERVAL_SECONDS[field]

    if field in INSTANT_FIELDS:
        return 1

    if getattr(desc, "binary", False):
        return 1

    if field in LOCATION_FIELDS:
        return 1

    if field in DEVICE_METADATA_FIELDS:
        return STATIC_METADATA_INTERVAL_SECONDS

    group = field_group_for_desc(desc)
    return int(POLLING_PROFILES[context.telemetry_mode][group]["interval"])


def default_field_policy(field: str, context: TelemetryPolicyContext) -> FieldPolicy:
    """Return telemetry policy for a field."""
    desc = FIELD_DEFS.get(field)
    interval_seconds = max(1, int(interval_seconds_for_field(field, desc, context)))
    minimum_delta = minimum_delta_for_field(field, desc, context)

    policy: dict[str, Any] = {
        "interval_seconds": interval_seconds,
        "resend_interval_seconds": DEFAULT_RESEND_INTERVAL_SECONDS,
    }
    if minimum_delta is not None:
        policy["minimum_delta"] = minimum_delta

    return policy


def extract_unsupported_interval(err: Exception) -> tuple[str, int] | None:
    """Extract unsupported interval metadata from Tesla proxy error text."""
    message = str(err)
    match = re.search(r"(\{.*\})", message)
    if match is None:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None

    tags = payload.get("Tags")
    if not isinstance(tags, dict):
        return None
    field_name = tags.get("field_name")
    authorized = tags.get("authorized")
    if not isinstance(field_name, str) or not isinstance(authorized, str):
        return None
    try:
        interval_seconds = max(1, round(float(authorized) / 1000))
    except ValueError:
        return None
    return field_name, interval_seconds
