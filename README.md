# Notification Manager for Home Assistant

[![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/custom-components/hacs)
[![GitHub Release](https://img.shields.io/github/release/rjullien/ha-notification-manager.svg)](https://github.com/rjullien/ha-notification-manager/releases)

A production-grade Home Assistant custom component for multi-channel notifications. Supports **Alexa TTS** (with automatic volume save/restore), **mobile push**, **Telegram**, and **WhatsApp** via [whatsmeow-bridge](https://github.com/tulir/whatsmeow).

---

## Features

| Channel | What it does |
|---------|-------------|
| 📱 **Phone (push)** | iOS/Android push via `notify.mobile_app_*` |
| 💬 **Telegram** | Direct message via `telegram_bot.send_message` |
| 🔊 **Alexa TTS** | Multi-room TTS with automatic volume save → set → restore |
| 🟢 **WhatsApp** | REST API calls to whatsmeow-bridge |
| 📡 **Status sensor** | `sensor.notification_manager_whatsapp_status` (polls every 5 min) |

---

## Requirements

- Home Assistant **2024.12+**
- [Alexa Media Player](https://github.com/custom-components/alexa_media_player) integration (for Alexa TTS)
- [Telegram Bot](https://www.home-assistant.io/integrations/telegram_bot/) integration (for Telegram)
- Mobile Companion App installed on target phones
- [whatsmeow-bridge](https://github.com/tulir/whatsmeow) running and accessible (for WhatsApp)

> Since v1.6.0 the `input_text.media_player_volumes` helper is **no longer needed** — volumes are kept in memory during the TTS cycle (the helper was written but never read back, and overflowed its 255-char limit beyond ~8 players).

---

## Installation

### Via HACS (recommended)

1. Open HACS → Integrations → ⋮ → Custom repositories
2. Add `https://github.com/rjullien/ha-notification-manager` as **Integration**
3. Search for "Notification Manager" and install
4. Restart Home Assistant

### Manual installation

1. Copy `custom_components/notification_manager/` into your HA `custom_components/` directory
2. Restart Home Assistant

---

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Notification Manager**
3. Enter your whatsmeow-bridge URL and bearer token
4. **Verify TLS certificate** is enabled by default — disable it only if your bridge uses a self-signed certificate

> **No WhatsApp?** You can enter any placeholder URL — the component works fine for phone/Alexa even if the bridge is down.

---

## Service: `notification_manager.notify`

### Parameters

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `message_tel` | string | `""` | Message for push/Telegram/WhatsApp |
| `message_alexa` | string | `""` | TTS message for Alexa (primary language) |
| `message_alexa_en` | string | `""` | TTS message for English Echo devices (sent after 3s delay) |
| `notification_tel` | string | `"all"` | Phone targets — person names (space-separated) or `"none"` |
| `notification_whatsapp` | string | `"none"` | WhatsApp targets — person names (space-separated) or `"none"` |
| `notification_alexa` | string | `""` | Alexa targets — keywords matched against entity IDs (space-separated) or `"aucun"` |

### Target resolution

**Phone/Telegram** (`notification_tel`):
- Space-separated **person names** (case-insensitive)
- `"all"` or empty → sends to default targets list
- `"none"` / `"aucun"` → skip
- Names are resolved against `PHONE_TARGETS` dict

**WhatsApp** (`notification_whatsapp`):
- Space-separated **person names** (case-insensitive)
- `"none"` / `"aucun"` / empty → skip
- Names are resolved against `WHATSAPP_CONTACTS` dict

**Alexa** (`notification_alexa`) — pattern matching:
- Space-separated **keywords** matched as substrings against `media_player.*` entity IDs
- Example: `"show"` → matches all entities containing "show" (e.g. `media_player.rene_echo_show_2`)
- Example: `"salon chambre"` → matches entities containing "salon" OR "chambre"
- Empty → defaults to keyword `"show"` (Echo Show devices)
- `"aucun"` / `"none"` / `"off"` → skip
- ⚠️ **No `"all"` keyword** — speakers span multiple locations, broadcasting everywhere is forbidden

> The algorithm is inherited from the original mamagetts automation: substring matching gives flexibility without maintaining a separate name→entity mapping.

---

## Usage examples

### Basic notification — all channels

```yaml
service: notification_manager.notify
data:
  message_tel: "The delivery has arrived"
  message_alexa: "Your delivery is here!"
  notification_tel: "all"
  notification_alexa: ""
```

### Specific person, push + WhatsApp

```yaml
service: notification_manager.notify
data:
  message_tel: "Your package is at the door"
  notification_tel: "John"
  notification_whatsapp: "John"
```

### Alexa announcement in specific rooms

```yaml
service: notification_manager.notify
data:
  message_alexa: "Dinner is ready!"
  notification_alexa: "kitchen bedroom"
  notification_tel: "none"
```

---

## Sensor: `sensor.notification_manager_whatsapp_status`

| State | Meaning |
|-------|---------|
| `connected` | Bridge is up and responding |
| `disconnected` | Bridge unreachable or returned error |
| `unknown` | Bridge URL not configured or not yet polled |

Polls every **5 minutes**. Use in automations to alert when WhatsApp goes down.

---

## Alexa TTS — volume management

1. **Read** current volume from each target entity (kept in memory)
2. **Set** all targets to TTS volume (default `0.7`) — awaited *before* TTS so speech never starts at the old level
3. **Send** TTS via `notify.alexa_media`
4. **Wait** configurable delay (default `8s`)
5. **Restore** each player to its saved volume

The full cycle is serialised behind a lock: overlapping `notify` calls queue up instead of capturing the TTS volume as the "original" one. The English message (`message_alexa_en`) runs as an independent task with its own 3 s delay — it is never delayed by slow channels (e.g. WhatsApp retries).

---

## Customization

Edit `custom_components/notification_manager/const.py` to configure:

- `ALEXA_PLAYERS` — list of all Alexa `media_player.*` entity IDs
- `PHONE_TARGETS` — mobile app service names and Telegram chat IDs per person
- `WHATSAPP_CONTACTS` — WhatsApp JIDs per person
- `ALEXA_TTS_VOLUME` — TTS volume level (default `0.7`)
- `ALEXA_POST_TTS_DELAY` — wait after TTS before restoring volume (default `8s`)

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Service not available | Restart HA; check logs for `notification_manager` |
| Alexa TTS not working | Verify Alexa Media Player integration + entity IDs in `const.py` |
| WhatsApp not delivered | Check `sensor.notification_manager_whatsapp_status`; verify bridge URL/token |
| Volume not restored | Restore manually if HA restarted mid-TTS (volumes are held in memory during the cycle) |

---

## Security notes

- **TLS verification is on by default** (v1.6.0). Disable it per-entry in the config flow only for self-signed bridge certificates.
- The diagnostic services `whatsapp_bridge_logs` and `whatsapp_bridge_restart` are **restricted to administrator users** (bridge logs may contain phone numbers and message contents).
- The status sensor no longer exposes the bridge URL as a state attribute.
- Tailscale MagicDNS hostnames are resolved **in-process** via `TAILSCALE_DNS_OVERRIDES` (see `const_private.example.py`) — `/etc/hosts` is never modified.
- Keep all personal data (names, chat IDs, JIDs) in `/config/notification_manager_private.py`; never commit it.

---

## License

MIT — see [LICENSE](LICENSE)
