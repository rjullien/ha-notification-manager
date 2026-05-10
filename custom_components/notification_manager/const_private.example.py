"""Private constants — YOUR personal configuration.

Copy this file to const_private.py and fill in your real values.
const_private.py is gitignored and will never be committed.
"""

# ── Alexa media players ───────────────────────────────────────────────────────
ALEXA_PLAYERS = [
    "media_player.your_echo_show",
    "media_player.your_echo_dot",
    # Add all your Echo entity_ids here
]

# English Alexa target
ALEXA_EN_TARGET = "media_player.your_english_echo"

# ── Phone notification targets ────────────────────────────────────────────────
PHONE_TARGETS = {
    "person1": {
        "mobile": "notify.mobile_app_person1_phone",
        "telegram_chat_id": 123456789,  # or None
    },
    "person2": {
        "mobile": "notify.mobile_app_person2_phone",
        "telegram_chat_id": 987654321,  # or None
    },
}

# Default targets when "all" is specified
PHONE_DEFAULT_TARGETS = ["person1", "person2"]

# ── WhatsApp contacts (JID format: phone@s.whatsapp.net) ──────────────────────
WHATSAPP_CONTACTS = {
    "person1": "33600000001@s.whatsapp.net",
    "person2": "33600000002@s.whatsapp.net",
}

# ── Alert recipients (Telegram chat_ids) ──────────────────────────────────────
BRIDGE_ALERT_CHAT_IDS = [123456789, 987654321]

# ── Telegram groups (no mobile push, message/photo only) ──────────────────────
TELEGRAM_GROUPS = {
    "family": -5162092129,  # Group chat_id (negative for groups)
    # "another_group": -1001234567890,
}
