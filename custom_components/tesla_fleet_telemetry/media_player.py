"""Media player platform backed by Tesla telemetry media fields."""

from __future__ import annotations

import re
from datetime import datetime

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
)
from homeassistant.components.media_player.const import MediaPlayerState, MediaType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .command_entity import CommandEntity
from .const import MEDIA_PLAYER_VOLUME_INCREMENT, MEDIA_PLAYER_VOLUME_MAX
from .coordinator import TelemetryCoordinator
from .entity_base import telemetry_unique_id
from .oauth2 import TeslaOAuth2Implementation
from .proxy_client import TeslaProxyClient

_MEDIA_FIELDS: tuple[str, ...] = (
    "MediaAudioVolume",
    "MediaAudioVolumeIncrement",
    "MediaAudioVolumeMax",
    "MediaNowPlayingAlbum",
    "MediaNowPlayingArtist",
    "MediaNowPlayingDuration",
    "MediaNowPlayingElapsed",
    "MediaNowPlayingStation",
    "MediaNowPlayingTitle",
    "MediaPlaybackSource",
    "MediaPlaybackStatus",
)

_SUPPORTED_FEATURES = (
    MediaPlayerEntityFeature.PLAY
    | MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.VOLUME_SET
    | MediaPlayerEntityFeature.VOLUME_STEP
)


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
    async_add_entities([TeslaMediaPlayer(coordinator, vin, proxy_client, impl, entry)])


class TeslaMediaPlayer(CommandEntity, MediaPlayerEntity):
    """Media player backed by Tesla telemetry with playback and volume controls."""

    _attr_name = "Media Player"
    _attr_icon = "mdi:music-circle"
    _attr_entity_registry_enabled_default = True
    _attr_supported_features = _SUPPORTED_FEATURES

    def __init__(
        self,
        coordinator: TelemetryCoordinator,
        vin: str,
        proxy_client: TeslaProxyClient,
        impl: TeslaOAuth2Implementation,
        entry: ConfigEntry,
    ) -> None:
        super().__init__(coordinator, vin, proxy_client, impl, entry)
        self._attr_unique_id = telemetry_unique_id(self._vin, "media_player")
        self._optimistic_volume: float | None = None

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and any(
            self.coordinator.get_field(field) is not None for field in _MEDIA_FIELDS
        )

    @property
    def state(self) -> MediaPlayerState:
        status = self._clean_text(self.coordinator.get_field("MediaPlaybackStatus"))
        if status is None:
            return MediaPlayerState.IDLE
        lowered = status.lower().removeprefix("mediastatus").strip()
        if lowered in {"playing", "play"}:
            return MediaPlayerState.PLAYING
        if lowered in {"paused", "pause"}:
            return MediaPlayerState.PAUSED
        if lowered in {"buffering", "loading"}:
            return MediaPlayerState.BUFFERING
        return MediaPlayerState.IDLE

    @property
    def media_content_type(self) -> str:
        return str(MediaType.MUSIC)

    @property
    def media_title(self) -> str | None:
        title = self._clean_text(self.coordinator.get_field("MediaNowPlayingTitle"))
        if title is not None:
            return title
        return self._clean_text(self.coordinator.get_field("MediaNowPlayingStation"))

    @property
    def media_artist(self) -> str | None:
        return self._clean_text(self.coordinator.get_field("MediaNowPlayingArtist"))

    @property
    def media_album_name(self) -> str | None:
        return self._clean_text(self.coordinator.get_field("MediaNowPlayingAlbum"))

    @property
    def media_channel(self) -> str | None:
        return self._clean_text(self.coordinator.get_field("MediaNowPlayingStation"))

    @property
    def source(self) -> str | None:
        return self._clean_text(self.coordinator.get_field("MediaPlaybackSource"))

    @property
    def source_list(self) -> list[str] | None:
        source = self.source
        return [source] if source else None

    @property
    def media_duration(self) -> int | None:
        duration_ms = self._coerce_int(self.coordinator.get_field("MediaNowPlayingDuration"))
        if duration_ms is None or duration_ms < 0:
            return None
        return duration_ms // 1000

    @property
    def media_position(self) -> int | None:
        elapsed_ms = self._coerce_int(self.coordinator.get_field("MediaNowPlayingElapsed"))
        if elapsed_ms is None or elapsed_ms < 0:
            return None
        return elapsed_ms // 1000

    @property
    def media_position_updated_at(self) -> datetime | None:
        if self.media_position is None:
            return None
        return datetime.now()

    @property
    def volume_level(self) -> float | None:
        if self._optimistic_volume is not None:
            return self._optimistic_volume
        current = self._coerce_float(self.coordinator.get_field("MediaAudioVolume"))
        maximum = self._coerce_float(self.coordinator.get_field("MediaAudioVolumeMax"))
        if current is None:
            return None
        if maximum is None or maximum <= 0:
            maximum = MEDIA_PLAYER_VOLUME_MAX
        return max(0.0, min(current / maximum, 1.0))

    def _handle_coordinator_update(self) -> None:
        self._optimistic_volume = None
        super()._handle_coordinator_update()

    # --- Commands ---

    async def async_media_play(self) -> None:
        if self.state != MediaPlayerState.PLAYING:
            await self._send_command("media_toggle_playback")

    async def async_media_pause(self) -> None:
        if self.state == MediaPlayerState.PLAYING:
            await self._send_command("media_toggle_playback")

    async def async_media_next_track(self) -> None:
        await self._send_command("media_next_track")

    async def async_media_previous_track(self) -> None:
        await self._send_command("media_prev_track")

    async def async_select_source(self, source: str) -> None:
        """Source switching is not supported; Tesla does not expose a source-switch command."""

    async def async_set_volume_level(self, volume: float) -> None:
        maximum = self._coerce_float(self.coordinator.get_field("MediaAudioVolumeMax"))
        if maximum is None or maximum <= 0:
            maximum = MEDIA_PLAYER_VOLUME_MAX
        tesla_volume = round(max(0.0, min(volume * maximum, 10.0)), 1)
        await self._send_command("adjust_volume", {"volume": tesla_volume})
        self._optimistic_volume = volume
        self.async_write_ha_state()

    async def async_volume_up(self) -> None:
        current = self._coerce_float(self.coordinator.get_field("MediaAudioVolume"))
        increment = (
            self._coerce_float(self.coordinator.get_field("MediaAudioVolumeIncrement"))
            or MEDIA_PLAYER_VOLUME_INCREMENT
        )
        maximum = (
            self._coerce_float(self.coordinator.get_field("MediaAudioVolumeMax"))
            or MEDIA_PLAYER_VOLUME_MAX
        )
        if current is None:
            return
        new_volume = min(current + increment, maximum)
        await self._send_command("adjust_volume", {"volume": round(min(new_volume, 10.0), 1)})
        self._optimistic_volume = new_volume / maximum
        self.async_write_ha_state()

    async def async_volume_down(self) -> None:
        current = self._coerce_float(self.coordinator.get_field("MediaAudioVolume"))
        increment = (
            self._coerce_float(self.coordinator.get_field("MediaAudioVolumeIncrement"))
            or MEDIA_PLAYER_VOLUME_INCREMENT
        )
        maximum = (
            self._coerce_float(self.coordinator.get_field("MediaAudioVolumeMax"))
            or MEDIA_PLAYER_VOLUME_MAX
        )
        if current is None:
            return
        new_volume = max(current - increment, 0.0)
        await self._send_command("adjust_volume", {"volume": round(min(new_volume, 10.0), 1)})
        self._optimistic_volume = new_volume / maximum
        self.async_write_ha_state()

    # --- Helpers ---

    @staticmethod
    def _clean_text(value: object) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        cleaned = value.strip().strip('"').strip("'").strip()
        if cleaned in {"", "None", "Unknown"}:
            return None
        cleaned = re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), cleaned)
        return cleaned

    @staticmethod
    def _coerce_int(value: object) -> int | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_float(value: object) -> float | None:
        if isinstance(value, bool) or value is None:
            return None
        if isinstance(value, int | float):
            return float(value)
        try:
            return float(str(value).strip())
        except (TypeError, ValueError):
            return None
