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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

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
        # Raw MediaAudioVolume when the optimistic value was set; the optimistic
        # value is dropped once telemetry reports a different volume.
        self._optimistic_volume_base: object = None
        self._position: int | None = None
        self._position_updated_at: datetime | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._refresh_position()

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

    def _elapsed_seconds(self) -> int | None:
        elapsed_ms = self._coerce_int(self.coordinator.get_field("MediaNowPlayingElapsed"))
        if elapsed_ms is None or elapsed_ms < 0:
            return None
        return elapsed_ms // 1000

    def _refresh_position(self) -> None:
        # Stamp the position only when it changes: a fresh timestamp on every
        # write would change the state attributes (and add a recorder row) on
        # every unrelated telemetry update.
        position = self._elapsed_seconds()
        if position != self._position:
            self._position = position
            self._position_updated_at = dt_util.utcnow() if position is not None else None

    @property
    def media_position(self) -> int | None:
        return self._position

    @property
    def media_position_updated_at(self) -> datetime | None:
        return self._position_updated_at

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

    @callback
    def _handle_coordinator_update(self) -> None:
        if (
            self._optimistic_volume is not None
            and self.coordinator.get_field("MediaAudioVolume") != self._optimistic_volume_base
        ):
            self._optimistic_volume = None
        self._refresh_position()
        super()._handle_coordinator_update()

    def _volume_max(self) -> float:
        maximum = self._coerce_float(self.coordinator.get_field("MediaAudioVolumeMax"))
        if maximum is None or maximum <= 0:
            maximum = MEDIA_PLAYER_VOLUME_MAX
        return min(maximum, MEDIA_PLAYER_VOLUME_MAX)

    def _set_optimistic_volume(self, level: float) -> None:
        self._optimistic_volume = level
        self._optimistic_volume_base = self.coordinator.get_field("MediaAudioVolume")
        self.async_write_ha_state()

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
        maximum = self._volume_max()
        tesla_volume = round(max(0.0, min(volume * maximum, maximum)), 1)
        await self._send_command("adjust_volume", {"volume": tesla_volume})
        self._set_optimistic_volume(tesla_volume / maximum)

    async def _async_step_volume(self, direction: int) -> None:
        current = self._coerce_float(self.coordinator.get_field("MediaAudioVolume"))
        if current is None:
            return
        increment = (
            self._coerce_float(self.coordinator.get_field("MediaAudioVolumeIncrement"))
            or MEDIA_PLAYER_VOLUME_INCREMENT
        )
        maximum = self._volume_max()
        new_volume = round(max(0.0, min(current + direction * increment, maximum)), 1)
        await self._send_command("adjust_volume", {"volume": new_volume})
        self._set_optimistic_volume(new_volume / maximum)

    async def async_volume_up(self) -> None:
        await self._async_step_volume(1)

    async def async_volume_down(self) -> None:
        await self._async_step_volume(-1)

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
