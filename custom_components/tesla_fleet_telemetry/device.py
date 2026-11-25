"""Shared device metadata helpers."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import DeviceInfo, EntityDescription

from .const import DOMAIN
from .coordinator import TelemetryCoordinator

_LOGGER = logging.getLogger(__name__)


def _clean_text(value: object) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, str):
        if isinstance(value, int | float):
            value = str(value)
        else:
            _LOGGER.debug(
                "Ignoring invalid device metadata value type: %s",
                type(value).__name__,
            )
            return None
    cleaned = value.strip().strip('"').strip("'").strip()
    return cleaned or None


def _pretty_model(car_type: object) -> str | None:
    cleaned_model = _clean_text(car_type)
    if cleaned_model is None:
        return None
    for prefix in ("ModelName", "CarType"):
        if cleaned_model.startswith(prefix):
            cleaned_model = cleaned_model[len(prefix) :].strip()
            break
    normalized = cleaned_model.replace("_", "").replace("-", "").lower()
    if normalized in {"model3", "m3"}:
        return "Model 3"
    if normalized in {"modely", "my"}:
        return "Model Y"
    if normalized in {"models", "ms"}:
        return "Model S"
    if normalized in {"modelx", "mx"}:
        return "Model X"
    return cleaned_model


def _clean_vehicle_detail(value: object, prefixes: tuple[str, ...]) -> str | None:
    cleaned = _clean_text(value)
    if cleaned is None:
        return None
    for prefix in prefixes:
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :].strip()
            break
    return cleaned or None


def device_registry_metadata(
    coordinator: TelemetryCoordinator,
    vin: str,
) -> tuple[str, str | None, str | None, str | None]:
    """Return normalized device registry metadata."""
    vehicle_name = _clean_text(coordinator.get_field("VehicleName")) or vin
    efficiency_package = _clean_vehicle_detail(
        coordinator.get_field("EfficiencyPackage"),
        ("EfficiencyPackage",),
    )
    model = _pretty_model(coordinator.get_field("CarType"))
    sw_version = _clean_vehicle_detail(coordinator.get_field("Version"), ("Version",))
    return vehicle_name, model, efficiency_package, sw_version


def vehicle_model(hass: HomeAssistant, coordinator: TelemetryCoordinator, vin: str) -> str | None:
    """Return the model as a lowercase alphanumeric token ("modely", "cybertruck"), or None.

    Live CarType wins; the device registry model (written by _sync_device_metadata)
    covers restarts before the first telemetry push arrives.
    """
    model = _pretty_model(coordinator.get_field("CarType"))
    if model is None:
        device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, vin)})
        model = device.model if device else None
    if not model:
        return None
    return "".join(ch for ch in model.lower() if ch.isalnum()) or None


def description_applies(
    description: EntityDescription, model: str | None, options: Mapping[str, Any] | None = None
) -> bool:
    """Whether a description is created: not user-hidden, and matching the vehicle model.

    An unknown model hides model-restricted descriptions.
    """
    hide_option = getattr(description, "hide_option", None)
    if hide_option and options and options.get(hide_option):
        return False
    models = getattr(description, "models", None)
    return models is None or (model is not None and any(m in model for m in models))


def build_device_info(coordinator: TelemetryCoordinator, vin: str) -> DeviceInfo:
    vehicle_name, model, hw_version, sw_version = device_registry_metadata(coordinator, vin)
    return DeviceInfo(
        identifiers={(DOMAIN, vin)},
        name=vehicle_name,
        manufacturer="Tesla",
        model=model,
        hw_version=hw_version,
        serial_number=vin,
        sw_version=sw_version,
    )
