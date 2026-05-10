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

- Home Assistant **2023.6+**
- [Alexa Media Player](https://github.com/custom-components/alexa_media_player) integration (for Alexa TTS)
- [Telegram Bot](https://www.home-assistant.io/integrations/telegram_bot/) integration (for Telegram)
- Mobile Companion App installed on target phones
- [whatsmeow-bridge](https://github.com/tulir/whatsmeow) running and accessible (for WhatsApp)
- `input_text.media_player_volumes` entity created in HA (for volume persistence)

### Create the input_text entity

Add to your `configuration.yaml`:

```yaml
input_text:
  media_player_volumes:
    name: Media Player Volumes
    max: 255
```

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

- **Phone/Telegram targets** (`notification_tel`): Map person names to `notify.mobile_app_*` services and Telegram chat IDs. Configure in `const.py` → `PHONE_TARGETS`.
- **WhatsApp targets** (`notification_whatsapp`): Map person names to WhatsApp JIDs. Configure in `const.py` → `WHATSAPP_CONTACTS`.
- **Alexa targets** (`notification_alexa`): Keywords are matched as substrings against your `media_player.*` entity IDs. Configure in `const.py` → `ALEXA_PLAYERS`.

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

1. **Read** current volume from each target entity
2. **Store** volumes in `input_text.media_player_volumes`
3. **Set** all targets to TTS volume (default `0.7`)
4. **Send** TTS via `notify.alexa_media`
5. **Wait** configurable delay (default `8s`)
6. **Restore** each player to its saved volume

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
| Volume not restored | Check `input_text.media_player_volumes` value; restore manually if HA restarted mid-TTS |

---

## License

MIT — see [LICENSE](LICENSE)
