"""Standalone paho-mqtt client for Tesla Fleet Telemetry — no HA MQTT dependency."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import paho.mqtt.client as mqtt
from homeassistant.core import HomeAssistant

from .const import (
    DEFAULT_CONNECTION_TIMEOUT,
    DEFAULT_MQTT_TOPIC_BASE,
    DOMAIN,
    MQTT_BACKOFF_BASE_SECONDS,
    MQTT_BACKOFF_MAX_SECONDS,
    MQTT_KEEPALIVE_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


OnMessageCallback = Callable[[str, Any], None]
OnAlertCallback = Callable[[str, dict[str, Any]], None]


def _parse_value(raw: str) -> Any:
    """Convert raw MQTT payload string to a typed Python value.

    Rules:
    - "true" / "false" → bool
    - "" / "Unknown" / "None" / "none" → None
    - JSON object string → dict (for Location)
    - Numeric string → float (or int if whole number)
    - Anything else → str (enum values, state names)

    Returns:
        Typed value: bool, int, float, str, dict, or None depending on input.
    """
    # Many enum/string values arrive wrapped in double quotes.
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1].strip()

    if raw in ("", "Unknown", "None", "none", "null"):
        return None
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    if raw.startswith("{"):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
    try:
        val = float(raw)
        # Return int if it's a whole number, else float
        return int(val) if val == int(val) else val
    except ValueError:
        return raw


def _format_mqtt_error(vin: str, topic: str, value: Any, retained: bool) -> str:
    """Build a clean log message for Tesla MQTT error topics."""
    error_name = topic.rsplit("/", 1)[-1]
    retained_suffix = " [retained]" if retained else ""
    if error_name in {"unsupported_interval", "unsuported_interval"} and isinstance(value, dict):
        created_at = value.get("CreatedAt")
        tags = value.get("Tags")
        if isinstance(tags, dict):
            field_name = tags.get("field_name")
            requested = tags.get("requested")
            authorized = tags.get("authorized")
            if field_name and requested and authorized:
                message = (
                    f"Tesla MQTT error for {vin}: unsupported interval for {field_name} "
                    f"(requested={requested}s, authorized={authorized}ms)"
                )
                if created_at:
                    message += f", created_at={created_at}"
                return message + retained_suffix
    return f"Tesla MQTT error for {vin} on {topic}: {value}{retained_suffix}"


class TeslaMQTTClient:
    """Manages a paho-mqtt connection and dispatches telemetry to the coordinator."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        vin: str,
        on_message: OnMessageCallback,
        on_alert: OnAlertCallback | None = None,
        timeout_seconds: int = DEFAULT_CONNECTION_TIMEOUT,
        topic_base: str = DEFAULT_MQTT_TOPIC_BASE,
    ) -> None:
        self._hass = hass
        self._host = host
        self._port = port
        self._vin = vin
        self._on_message = on_message
        self._on_alert = on_alert
        self._topic_base = topic_base
        self._connectivity_topic = f"{topic_base}/{vin}/connectivity"
        self._topic_prefix = f"{topic_base}/{vin}/v/"
        self._alerts_prefix = f"{topic_base}/{vin}/alerts/"
        self._errors_prefix = f"{topic_base}/{vin}/errors/"
        self._root_topic = f"{topic_base}/{vin}/#"
        self._client: mqtt.Client | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._running = False
        self._backoff = MQTT_BACKOFF_BASE_SECONDS
        self._connect_lock = asyncio.Lock()
        self._seen_mqtt_errors: dict[str, tuple[str, float]] = {}
        self._error_cleanup_at = 0.0
        self._connect_waiter: asyncio.Future[None] | None = None
        self._timeout_seconds = timeout_seconds

    def _build_client(self) -> mqtt.Client:
        client_id = f"{DOMAIN}_{self._vin}"
        client = mqtt.Client(
            client_id=client_id,
            protocol=mqtt.MQTTv5,
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_mqtt_message
        return client

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code == 0:
            _LOGGER.info("Tesla MQTT connected, subscribing to %s", self._root_topic)
            client.subscribe(self._root_topic, qos=1)
            self._backoff = MQTT_BACKOFF_BASE_SECONDS
            self._finish_connect()
        else:
            _LOGGER.warning("Tesla MQTT connect failed: reason_code=%s", reason_code)
            self._finish_connect(OSError(f"MQTT connect failed: {reason_code}"))

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties) -> None:
        self._finish_connect(OSError(f"MQTT disconnected: {reason_code}"))
        if self._running:
            _LOGGER.warning(
                "Tesla MQTT disconnected (reason=%s), reconnecting in %ss",
                reason_code,
                self._backoff,
            )
            self._schedule_reconnect()

    def _on_mqtt_message(self, client, userdata, msg) -> None:  # noqa: C901
        topic: str = msg.topic
        raw = msg.payload.decode("utf-8", errors="replace").strip()
        value = _parse_value(raw)
        if topic == self._connectivity_topic and isinstance(value, dict):
            status = value.get("Status")
            if status is not None:
                if isinstance(status, str):
                    status = status.capitalize()
                self._hass.loop.call_soon_threadsafe(
                    self._on_message,
                    "ConnectivityStatus",
                    status,
                )
            network_interface = value.get("NetworkInterface")
            if network_interface is not None:
                self._hass.loop.call_soon_threadsafe(
                    self._on_message,
                    "NetworkInterface",
                    network_interface,
                )
            return
        if topic.startswith(self._alerts_prefix):
            if self._on_alert and isinstance(value, dict):
                alert_code = topic[len(self._alerts_prefix) :]
                if alert_code.endswith("/current"):
                    alert_code = alert_code[: -len("/current")]
                    self._hass.loop.call_soon_threadsafe(self._on_alert, alert_code, value)
                # "/history" topics are backfilled/replayed alerts, not the live
                # current state; ignore them so they don't overwrite the
                # current-alert entity with a stale/replayed value.
            return
        if topic.startswith(self._errors_prefix):
            msg_str = _format_mqtt_error(self._vin, topic, value, msg.retain)
            prev_msg, prev_time = self._seen_mqtt_errors.get(topic, ("", 0.0))
            now = time.monotonic()
            if msg_str != prev_msg or now - prev_time >= 300:
                _LOGGER.warning(msg_str)
                self._seen_mqtt_errors[topic] = (msg_str, now)
            if now >= self._error_cleanup_at:
                cutoff = now - 3600
                self._seen_mqtt_errors = {
                    k: v for k, v in self._seen_mqtt_errors.items() if v[1] > cutoff
                }
                if len(self._seen_mqtt_errors) > 200:
                    sorted_items = sorted(self._seen_mqtt_errors.items(), key=lambda x: x[1][1])
                    self._seen_mqtt_errors = dict(sorted_items[-200:])
                self._error_cleanup_at = now + 3600
            return
        if not topic.startswith(self._topic_prefix):
            return
        field = topic[len(self._topic_prefix) :]
        self._hass.loop.call_soon_threadsafe(self._on_message, field, value)

    def _schedule_reconnect(self) -> None:
        async def _reconnect() -> None:
            await asyncio.sleep(self._backoff)
            if not self._running:
                return
            self._backoff = min(self._backoff * 2, MQTT_BACKOFF_MAX_SECONDS)
            await self.async_connect()

        def _start() -> None:
            if not self._running:
                return
            if self._reconnect_task is None or self._reconnect_task.done():
                self._reconnect_task = self._hass.loop.create_task(_reconnect())

        self._hass.loop.call_soon_threadsafe(_start)

    def _finish_connect(self, err: Exception | None = None) -> None:
        waiter = self._connect_waiter
        if waiter is None or waiter.done():
            return

        def _finish() -> None:
            if waiter.done():
                return
            if err is None:
                waiter.set_result(None)
            else:
                waiter.set_exception(err)

        self._hass.loop.call_soon_threadsafe(_finish)

    async def async_connect(self) -> None:
        """Connect (or reconnect) to the MQTT broker."""
        async with self._connect_lock:
            self._running = True

            if self._client is None:
                self._client = self._build_client()
                self._client.loop_start()
                _LOGGER.info("Tesla MQTT loop started for %s:%s", self._host, self._port)

            if self._client.is_connected():
                return

            try:
                self._connect_waiter = self._hass.loop.create_future()
                self._client.connect_async(
                    self._host,
                    self._port,
                    keepalive=MQTT_KEEPALIVE_SECONDS,
                )
                await asyncio.wait_for(
                    self._connect_waiter,
                    timeout=self._timeout_seconds,
                )
            except TimeoutError as err:
                if self._reconnect_task is None:
                    self._running = False
                    raise TimeoutError("MQTT connect timed out") from err
                _LOGGER.error("Tesla MQTT reconnect timed out — will retry")
                self._reconnect_task = None
                self._schedule_reconnect()
            except Exception as err:
                if self._reconnect_task is None:
                    self._running = False
                    raise
                _LOGGER.error("Tesla MQTT reconnect error: %s — will retry", err)
                self._reconnect_task = None
                self._schedule_reconnect()
            finally:
                self._connect_waiter = None

    async def async_disconnect(self) -> None:
        """Clean shutdown — stop reconnect loop and disconnect."""
        self._running = False
        if self._connect_waiter and not self._connect_waiter.done():
            self._connect_waiter.cancel()
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
            finally:
                self._reconnect_task = None
        if self._client:
            try:
                self._client.disconnect()
                self._client.loop_stop()
            except Exception as e:  # noqa: S110
                _LOGGER.debug("Error disconnecting MQTT client: %s", e)
            self._client = None
        _LOGGER.info("Tesla MQTT client disconnected")
