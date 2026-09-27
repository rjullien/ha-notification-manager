"""Private constants — YOUR personal configuration.

Preferred location: copy this file to /config/notification_manager_private.py
(outside the component directory, so HACS updates never erase it).
Legacy fallback: const_private.py next to this file (gitignored).

Never commit real names, chat IDs, phone numbers or tokens.
"""

# ── Alexa media players ───────────────────────────────────────────────────────
ALEXA_PLAYERS = [
    "media_player.your_echo_show",
    "media_player.your_echo_dot",
    # Add all your Echo entity_ids here
]

# English Alexa target
ALEXA_EN_TARGET = "media_player.your_english_echo"

# Optional: exclude specific players from a keyword after matching, while
# keeping them in ALEXA_PLAYERS for more specific compounds (e.g. show_11).
#
# Keys are POST-ALIAS keywords (see ALEXA_KEYWORD_ALIASES in const.py).
# Technical example: notification_alexa="show_2" resolves to "rene_show",
# so an exclude for that path must use "rene_show", not "show_2".
# Bare "rene" may also substring-match the same device — add that key too
# if needed.
#
# Site-specific player lists and exclude maps live in the private Home
# Assistant config repo (not in this public component). Placeholders only:
# ALEXA_KEYWORD_EXCLUDES = {
#     "show": ["media_player.your_bedroom_echo_show"],
#     "rene_show": ["media_player.your_bedroom_echo_show"],
#     "rene": ["media_player.your_bedroom_echo_show"],
# }

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
    "family": -1001234567890,  # Placeholder — group chat_ids are negative
    # "another_group": -1009876543210,
}

# ── Tailscale DNS overrides (optional) ────────────────────────────────────────
# HA OS containers can't resolve MagicDNS names. Map hostname → Tailscale IP;
# resolution happens in-process (no /etc/hosts modification).
# TAILSCALE_DNS_OVERRIDES = {
#     "bridge-host.your-tailnet.ts.net": "100.64.0.10",
# }
