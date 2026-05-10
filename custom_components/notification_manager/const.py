"""Constants for the Notification Manager integration."""

DOMAIN = "notification_manager"
CONF_BRIDGE_URL = "bridge_url"
CONF_BRIDGE_TOKEN = "bridge_token"

# Default values
DEFAULT_BRIDGE_URL = ""
DEFAULT_BRIDGE_TOKEN = ""

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

# Storage entity for volumes (input_text helper)
VOLUMES_ENTITY = "input_text.media_player_volumes"

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

# ── Telegram groups (override in const_private.py) ────────────────────────────

# Dict of {name: chat_id} for Telegram group targets
TELEGRAM_GROUPS: dict = {}

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
# const_private.py is gitignored and contains your personal data (contacts,
# entity_ids, chat_ids). Copy const_private.example.py and fill in your values.

try:
    from .const_private import *  # noqa: F401, F403
except ImportError:
    pass
