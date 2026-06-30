"""Constants for the Notification Manager integration."""

DOMAIN = "notification_manager"
CONF_BRIDGE_URL = "bridge_url"
CONF_BRIDGE_TOKEN = "bridge_token"
CONF_VERIFY_SSL = "verify_ssl"

# Default values
DEFAULT_BRIDGE_URL = ""
DEFAULT_BRIDGE_TOKEN = ""
# Secure by default — disable explicitly in the config flow for
# self-signed certificates on the bridge.
DEFAULT_VERIFY_SSL = True

# ── Alexa defaults (override in const_private.py) ─────────────────────────────

# Alexa media players - entity_ids of your Echo devices
ALEXA_PLAYERS: list[str] = []

# Default keyword when notification_alexa is empty (matches show entities)
ALEXA_DEFAULT_KEYWORD = "show"

# Alexa TTS volume
ALEXA_TTS_VOLUME = 0.7

# Default volume when unavailable
ALEXA_DEFAULT_VOLUME = 0.5

# Delay after sending TTS before restoring volumes (seconds)
ALEXA_POST_TTS_DELAY = 8

# Delay before sending English Alexa message (seconds)
ALEXA_EN_DELAY = 3

# English Alexa target entity_id
ALEXA_EN_TARGET = ""

# ── Phone targets (override in const_private.py) ──────────────────────────────

# Dict of {name: {"mobile": "notify.xxx", "telegram_chat_id": int|None}}
PHONE_TARGETS: dict = {}

# Default target names when "all" is specified
PHONE_DEFAULT_TARGETS: list[str] = []

# ── WhatsApp (override in const_private.py) ───────────────────────────────────

# Dict of {name: "phone@s.whatsapp.net"}
WHATSAPP_CONTACTS: dict = {}

# WhatsApp bridge endpoints
BRIDGE_SEND_ENDPOINT = "/send"
BRIDGE_HEALTH_ENDPOINT = "/health"

# HTTP timeouts (seconds)
BRIDGE_TIMEOUT = 10
BRIDGE_RETRIES = 3

# Telegram chat_ids to alert when bridge is down
BRIDGE_ALERT_CHAT_IDS: list[int] = []

# Tailscale DNS overrides: {hostname: ip}. HA OS containers can't resolve
# MagicDNS names; entries here are resolved in-process by a custom aiohttp
# resolver (see bridge_http.py) — the system /etc/hosts is never touched.
TAILSCALE_DNS_OVERRIDES: dict[str, str] = {}

# ── Telegram groups (override in const_private.py) ────────────────────────────

# Dict of {name: chat_id} for Telegram group targets
TELEGRAM_GROUPS: dict = {}

# ── Entity Watchdog (override in const_private.py) ───────────────────────────

# List of entity_ids to monitor for unavailable state
WATCHDOG_ENTITIES: list[str] = []

# Critical entities checked more frequently (e.g. irrigation valves)
# These are checked every WATCHDOG_CRITICAL_INTERVAL_MINUTES with a shorter threshold.
WATCHDOG_CRITICAL_ENTITIES: list[str] = []

# Minutes an entity must be unavailable before alerting
WATCHDOG_THRESHOLD_MINUTES = 15

# Threshold for critical entities (minutes)
WATCHDOG_CRITICAL_THRESHOLD_MINUTES = 5

# How often to check (minutes)
WATCHDOG_CHECK_INTERVAL_MINUTES = 60

# How often to check critical entities (minutes)
WATCHDOG_CRITICAL_INTERVAL_MINUTES = 10

# Hours between repeated alerts for the same entity
WATCHDOG_COOLDOWN_HOURS = 6

# Telegram chat_ids to receive watchdog alerts (falls back to BRIDGE_ALERT_CHAT_IDS if empty)
WATCHDOG_TELEGRAM_CHAT_IDS: list[int] = []

# ── Sensor ────────────────────────────────────────────────────────────────────

SENSOR_POLL_INTERVAL_MINUTES = 5

SENSOR_STATE_CONNECTED = "connected"
SENSOR_STATE_DISCONNECTED = "disconnected"
SENSOR_STATE_UNKNOWN = "unknown"

# ── Service ───────────────────────────────────────────────────────────────────

SERVICE_NOTIFY = "notify"

# Platforms
PLATFORMS = ["sensor"]


# ── Load private overrides ────────────────────────────────────────────────────
# Private config is loaded from /config/notification_manager_private.py
# This file lives OUTSIDE the component directory so HACS updates don't erase it.
# Fallback: also try .const_private (legacy, inside component dir).
# Only UPPERCASE names are imported, so stray imports (os, json, …) in the
# private file can never shadow this module's machinery.

import importlib.util as _ilu
import os as _os

_PRIVATE_PATH = _os.path.join(
    _os.environ.get("HASS_CONFIG", "/config"),
    "notification_manager_private.py",
)

if _os.path.isfile(_PRIVATE_PATH):
    _spec = _ilu.spec_from_file_location("_nm_private", _PRIVATE_PATH)
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    for _name in dir(_mod):
        if _name.isupper():
            globals()[_name] = getattr(_mod, _name)
else:
    try:
        from . import const_private as _mod_legacy

        for _name in dir(_mod_legacy):
            if _name.isupper():
                globals()[_name] = getattr(_mod_legacy, _name)
    except ImportError:
        pass
