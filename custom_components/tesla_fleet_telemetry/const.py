"""Constants for Tesla Fleet Telemetry.

Telemetry field and entity definitions live in descriptions.py.
"""

from __future__ import annotations

from typing import Literal, TypeAlias

DOMAIN = "tesla_fleet_telemetry"

# Options keys
CONF_TELEMETRY_MODE = "telemetry_mode"
CONF_TELEMETRY_SERVER_HOSTNAME = "telemetry_server_hostname"
CONF_TELEMETRY_SERVER_PORT = "telemetry_server_port"
CONF_TELEMETRY_SERVER_CA = "telemetry_server_ca"
DEFAULT_CONNECTION_TIMEOUT = 10
# MQTT topic root the self-hosted fleet-telemetry server publishes under
# (its "topic_base" setting in fleet-telemetry.json). Must match the user's
# own fleet-telemetry deployment; "tesla" is that project's documented default.
CONF_MQTT_TOPIC_BASE = "mqtt_topic_base"
DEFAULT_MQTT_TOPIC_BASE = "tesla"
DEFAULT_CONNECTION_CHECK_TIMEOUT = 3
DEFAULT_RESEND_INTERVAL_SECONDS = 43200

DEFAULT_TELEMETRY_MODE = "realtime"
DEFAULT_TELEMETRY_SERVER_ENDPOINT = ""
DEFAULT_TELEMETRY_ALERT_TYPES: list[str] = ["customer", "service", "service-fix"]
DEFAULT_TELEMETRY_DELIVERY_POLICY = "latest"
DEFAULT_LOCATION_MINIMUM_DELTA_METERS = 10.0

TELEMETRY_MODE_OPTIONS: tuple[str, ...] = ("realtime", "normal", "low")

# Type aliases for telemetry mode validation
TelemetryMode: TypeAlias = Literal["realtime", "normal", "low"]
TelemetryDeliveryPolicy: TypeAlias = Literal["latest"]
TelemetryAlertType: TypeAlias = Literal["customer", "service", "service-fix"]

# Runtime / fleet-config-sync tuning
FLEET_CONFIG_SYNC_DELAYS_SECONDS: tuple[int, ...] = (15, 45)
FLEET_TELEMETRY_ERROR_LOG_LIMIT = 50  # max errors logged per check cycle
MIN_AUTOMATIC_PUSH_INTERVAL_SECONDS = 300
MIN_UNIVERSAL_PUSH_INTERVAL_SECONDS = 30
RATE_LIMIT_RETRY_AFTER_DEFAULT_SECONDS = 300  # 5 minutes; fallback when Retry-After header absent
STATIC_METADATA_INTERVAL_SECONDS = 60

# Storage key used to pass authorized interval overrides through TelemetryPolicyContext.config
RUNTIME_INTERVAL_OVERRIDES = "_runtime_interval_overrides"

# Vehicle metadata fields required for device registry population.
# Also used by policy to assign a slower push interval (STATIC_METADATA_INTERVAL_SECONDS).
DEVICE_METADATA_FIELDS: frozenset[str] = frozenset(
    {"VehicleName", "CarType", "EfficiencyPackage", "Version"}
)

# Fields Tesla refuses below this interval. It reports the refusal asynchronously on the
# MQTT errors topic (after the config push was accepted), so the push-time retry never sees it.
MIN_INTERVAL_SECONDS: dict[str, int] = {"ChargePort": 60}

# MQTT client tuning
MQTT_BACKOFF_BASE_SECONDS = 1
MQTT_BACKOFF_MAX_SECONDS = 60
MQTT_KEEPALIVE_SECONDS = 60

# Wake-and-retry timing for commands sent while the vehicle is asleep
WAKE_UP_RETRY_DELAY_SECONDS = 12  # max wait for the vehicle to report connected
WAKE_UP_CONNECTED_SETTLE_SECONDS = 2  # grace after connect before retrying a command
WAKE_UP_SYNC_RECHECK_DELAY_SECONDS = 30
# Retry delay for failed fleet_telemetry_config push during bootstrap
BOOTSTRAP_PUSH_RETRY_DELAY_SECONDS = 30

# Media player
MEDIA_PLAYER_VOLUME_MAX = 11.0
MEDIA_PLAYER_VOLUME_INCREMENT = 1.0

# Fleet API version (used in command endpoints)
FLEET_API_VERSION = "api/1"

# OAuth2 endpoints
OAUTH_TOKEN_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/token"
OAUTH_AUTHORIZE_URL = "https://fleet-auth.prd.vn.cloud.tesla.com/oauth2/v3/authorize"
# Default Fleet API host. The account's actual regional host is detected during
# setup/reauth and stored per entry under CONF_FLEET_API_BASE.
FLEET_API_BASE = "https://fleet-api.prd.eu.vn.cloud.tesla.com"

OAUTH_SCOPES = (
    "openid offline_access user_data vehicle_device_data vehicle_location "
    "vehicle_cmds vehicle_charging_cmds"
)

# Config entry data keys
CONF_VIN = "vin"
CONF_MQTT_HOST = "mqtt_host"
CONF_MQTT_PORT = "mqtt_port"
CONF_MQTT_USERNAME = "mqtt_username"
CONF_MQTT_PASSWORD = "mqtt_password"
CONF_MQTT_TLS = "mqtt_tls"
CONF_PROXY_URL = "proxy_url"
CONF_PROXY_VERIFY_SSL = "proxy_verify_ssl"
CONF_CLIENT_ID = "client_id"
CONF_CLIENT_SECRET = "client_secret"
CONF_DOMAIN = "domain"
CONF_FLEET_API_BASE = "fleet_api_base"


# Platforms
PLATFORMS = [
    "sensor",
    "binary_sensor",
    "device_tracker",
    "climate",
    "select",
    "cover",
    "lock",
    "switch",
    "number",
    "button",
    "media_player",
    "update",
]
