"""Update platform — firmware version sensor."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import CommandEntity
from .coordinator import TelemetryCoordinator
from .entity_base import telemetry_unique_id
from .oauth2 import TeslaOAuth2Implementation
from .proxy_client import TeslaProxyClient
from .utils import normalize_enum

_LOGGER = logging.getLogger(__name__)

_RELEASE_NOTES_BASE_URL = (
    "https://www.notateslaapp.com/software-updates/version/{version}/release-notes"
)


def _normalize_version(value: object) -> str | None:
    if value is not None and not isinstance(value, str):
        value = str(value)
    cleaned = normalize_enum(value)
    return None if cleaned in ("None", "Unknown") else cleaned


def _normalize_percent(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        percent = int(float(value))
    else:
        try:
            percent = int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
    return max(0, min(percent, 100))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = entry.runtime_data
    coordinator: TelemetryCoordinator = entry_data.coordinator
    vin: str = entry_data.vin
    proxy_client: TeslaProxyClient = entry_data.proxy_client
    impl: TeslaOAuth2Implementation = entry_data.implementation
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service(
        "schedule_software_update",
        {
            vol.Optional("offset_sec", default=300): vol.All(
                vol.Coerce(int),
                vol.Range(min=0, max=86400),
            )
        },
        "async_schedule_update",
    )
    platform.async_register_entity_service(
        "cancel_software_update",
        {},
        "async_cancel_update",
    )
    async_add_entities([TeslaFirmwareUpdate(coordinator, vin, proxy_client, impl, entry)])


class TeslaFirmwareUpdate(CommandEntity, UpdateEntity):
    """Firmware version entity showing installed and available versions."""

    _attr_has_entity_name = True
    _attr_name = "Firmware"
    _attr_icon = "mdi:update"
    _attr_supported_features = UpdateEntityFeature.INSTALL
    _attr_entity_registry_enabled_default = True
    _state_fields = ("Version", "SoftwareUpdateVersion")

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        vin: str,
        proxy_client: TeslaProxyClient,
        impl: TeslaOAuth2Implementation,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "firmware")

    @property
    def installed_version(self) -> str | None:
        return _normalize_version(self.coordinator.get_field("Version"))

    @property
    def latest_version(self) -> str | None:
        available = _normalize_version(self.coordinator.get_field("SoftwareUpdateVersion"))
        return available or self.installed_version

    @property
    def release_url(self) -> str | None:
        version = self.latest_version
        if version is None:
            return None
        return _RELEASE_NOTES_BASE_URL.format(version=version)

    @property
    def _update_pending(self) -> bool:
        available = _normalize_version(self.coordinator.get_field("SoftwareUpdateVersion"))
        return available is not None and available != self.installed_version

    @property
    def in_progress(self) -> bool:
        """Show update in progress while downloading or installing."""
        # Progress fields keep reporting 0 when no update is pending; without
        # this gate the entity would sit "in progress" indefinitely.
        if not self._update_pending:
            return False
        download = self._download_percent_complete
        installation = self._installation_percent_complete

        download_active = download is not None and 0 <= download < 100
        installation_active = installation is not None and 0 <= installation < 100

        return download_active or installation_active

    @property
    def update_percentage(self) -> int | None:
        """Return the active update progress percentage (download then installation)."""
        if not self._update_pending:
            return None
        download = self._download_percent_complete
        installation = self._installation_percent_complete

        if download is not None and download < 100:
            return download

        # Show installation progress once download is done
        if installation is not None and installation < 100:
            return installation

        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        attrs: dict[str, Any] = {}
        if self._download_percent_complete is not None:
            attrs["download_percent_complete"] = self._download_percent_complete
        if self._installation_percent_complete is not None:
            attrs["installation_percent_complete"] = self._installation_percent_complete
        expected_duration = self.coordinator.get_field("SoftwareUpdateExpectedDurationMinutes")
        if expected_duration is not None:
            attrs["expected_duration_minutes"] = expected_duration
        scheduled_start = self.coordinator.get_field("SoftwareUpdateScheduledStartTime")
        if scheduled_start is not None:
            attrs["scheduled_start_time"] = scheduled_start
        return attrs

    @property
    def _download_percent_complete(self) -> int | None:
        return _normalize_percent(
            self.coordinator.get_field("SoftwareUpdateDownloadPercentComplete")
        )

    @property
    def _installation_percent_complete(self) -> int | None:
        return _normalize_percent(
            self.coordinator.get_field("SoftwareUpdateInstallationPercentComplete")
        )

    async def async_schedule_update(self, offset_sec: int = 300) -> None:
        await self._send_command("schedule_software_update", {"offset_sec": offset_sec})
        _LOGGER.info("Scheduled software update for %s in %s seconds", self._vin, offset_sec)

    async def async_cancel_update(self) -> None:
        """Cancel the scheduled software update installation."""
        await self._send_command("cancel_software_update")
        _LOGGER.info("Cancelled software update for %s", self._vin)

    async def async_install(
        self, version: str | None = None, backup: bool = False, **kwargs: Any
    ) -> None:
        download = self._download_percent_complete
        if download is not None and download < 100:
            raise ServiceValidationError(
                f"Download in progress ({download}%). "
                "Wait for download to complete before installing."
            )

        await self.async_schedule_update(offset_sec=300)
