"""Button platform — vehicle action buttons."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import _REAUTH_MESSAGE, CommandEntity
from .coordinator import TelemetryCoordinator
from .device import build_device_info
from .entity_base import telemetry_unique_id
from .oauth2 import TeslaOAuth2Implementation
from .proxy_client import TeslaProxyClient, TokenRevokedError

_LOGGER = logging.getLogger(__name__)


@dataclass
class ButtonDef:
    key: str
    name: str
    icon: str
    endpoint: str
    body: dict[str, Any] | None = None


BUTTON_DEFS: list[ButtonDef] = [
    ButtonDef("honk", "Honk", "mdi:bullhorn", "honk_horn"),
    ButtonDef("flash", "Flash", "mdi:car-light-high", "flash_lights"),
    ButtonDef("wake", "Wake Vehicle", "mdi:sleep-off", "wake_up"),
    ButtonDef("boombox", "Fart", "mdi:smoke", "remote_boombox", {"sound": 0}),
    ButtonDef("locate", "Locate", "mdi:map-marker", "locate"),
]


@dataclass
class ConfigButtonDef:
    key: str
    name: str
    icon: str


CONFIG_BUTTON_DEFS: list[ConfigButtonDef] = [
    ConfigButtonDef("push_telemetry_config", "Push Telemetry Config", "mdi:cloud-upload-outline"),
    ConfigButtonDef("reload_telemetry_data", "Reload Telemetry Data", "mdi:refresh"),
    ConfigButtonDef("get_telemetry_config", "Get Telemetry Config", "mdi:cloud-download-outline"),
    ConfigButtonDef(
        "delete_telemetry_config",
        "Delete Telemetry Config",
        "mdi:cloud-remove-outline",
    ),
    ConfigButtonDef("get_telemetry_errors", "Get Telemetry Errors", "mdi:cloud-alert-outline"),
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    entry_data = entry.runtime_data
    coordinator: TelemetryCoordinator = entry_data.coordinator
    proxy_client: TeslaProxyClient = entry_data.proxy_client
    impl: TeslaOAuth2Implementation = entry_data.implementation
    vin: str = entry_data.vin

    entities: list[ButtonEntity] = [
        TeslaButton(coordinator, vin, proxy_client, impl, entry, btn_def) for btn_def in BUTTON_DEFS
    ]
    for cfg_def in CONFIG_BUTTON_DEFS:
        entities.append(
            TeslaConfigActionButton(
                coordinator, vin, getattr(entry_data, cfg_def.key), cfg_def, entry
            )
        )
    async_add_entities(entities)


class TeslaButton(CommandEntity, ButtonEntity):
    """A button that triggers a single vehicle command."""

    def __init__(self, coordinator, vin, proxy_client, impl, entry, btn_def: ButtonDef) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._btn_def = btn_def
        self._attr_unique_id = telemetry_unique_id(self._vin, btn_def.key)
        self._attr_name = btn_def.name
        self._attr_icon = btn_def.icon

    async def async_press(self) -> None:
        if self._btn_def.endpoint == "locate":
            await self._send_command("flash_lights")
            await self._send_command("honk_horn")
            return
        result = await self._send_command(self._btn_def.endpoint, self._btn_def.body)
        if self._btn_def.endpoint == "wake_up":
            response = result.get("response") if isinstance(result, dict) else None
            state = None
            if isinstance(response, dict):
                state = response.get("state")
            if state:
                _LOGGER.info("wake_up accepted for %s, Tesla reports state=%s", self._vin, state)
            else:
                _LOGGER.info("wake_up accepted for %s", self._vin)


class TeslaConfigActionButton(ButtonEntity):
    """Generic config action button (push/get/delete config, get errors)."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        vin: str,
        action: Callable[..., Awaitable[Any]],
        defn: ConfigButtonDef,
        entry: ConfigEntry,
    ) -> None:
        self._coordinator = coordinator
        self._vin = vin
        self._action = action
        self._defn = defn
        self._entry = entry
        self._attr_unique_id = telemetry_unique_id(vin, defn.key)
        self._attr_name = defn.name
        self._attr_icon = defn.icon

    @property
    def device_info(self) -> DeviceInfo:
        return build_device_info(self._coordinator, self._vin)

    async def async_press(self) -> None:
        try:
            if self._defn.key in (
                "push_telemetry_config",
                "delete_telemetry_config",
                "get_telemetry_config",
            ):
                _LOGGER.info("%s button pressed for %s", self._defn.key, self._vin)
            result = await self._action()
            if self._defn.key == "get_telemetry_config":
                _LOGGER.debug("get_telemetry_config result for %s: %s", self._vin, result)
            if (
                self._defn.key == "push_telemetry_config"
                and isinstance(result, dict)
                and result.get("skipped")
            ):
                skip_reason = result.get("skip_reason", "unknown")
                raise ServiceValidationError(f"Push skipped: {skip_reason}")
        except (ConfigEntryAuthFailed, TokenRevokedError):
            self._entry.async_start_reauth(self.hass)
            raise ServiceValidationError(_REAUTH_MESSAGE) from None
        except Exception as err:
            raise ServiceValidationError(f"Action failed: {err}") from err
