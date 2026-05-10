"""Notification Manager - Home Assistant Custom Component.

Replaces the ManageTTS YAML script with a proper Python integration.
Supports Alexa TTS, phone/Telegram notifications, and WhatsApp via
the whatsmeow-bridge REST API.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ALEXA_DEFAULT_KEYWORD,
    ALEXA_DEFAULT_VOLUME,
    ALEXA_EN_DELAY,
    ALEXA_EN_TARGET as _CONST_ALEXA_EN_TARGET,
    ALEXA_PLAYERS as _CONST_ALEXA_PLAYERS,
    ALEXA_POST_TTS_DELAY as _CONST_ALEXA_POST_TTS_DELAY,
    ALEXA_TTS_VOLUME as _CONST_ALEXA_TTS_VOLUME,
    BRIDGE_ALERT_CHAT_IDS as _CONST_BRIDGE_ALERT_CHAT_IDS,
    BRIDGE_HEALTH_ENDPOINT,
    BRIDGE_RETRIES,
    BRIDGE_SEND_ENDPOINT,
    BRIDGE_TIMEOUT,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    DOMAIN,
    PHONE_DEFAULT_TARGETS as _CONST_PHONE_DEFAULT_TARGETS,
    PHONE_TARGETS as _CONST_PHONE_TARGETS,
    PLATFORMS,
    SERVICE_NOTIFY,
    VOLUMES_ENTITY,
    WHATSAPP_CONTACTS as _CONST_WHATSAPP_CONTACTS,
    TELEGRAM_GROUPS as _CONST_TELEGRAM_GROUPS,
)

_LOGGER = logging.getLogger(__name__)

# ── Service schema ────────────────────────────────────────────────────────────
SERVICE_NOTIFY_SCHEMA = vol.Schema(
    {
        vol.Optional("message_tel", default=""): cv.string,
        vol.Optional("message_alexa", default=""): cv.string,
        vol.Optional("message_alexa_en", default=""): cv.string,
        vol.Optional("notification_tel", default="all"): cv.string,
        vol.Optional("notification_whatsapp", default="none"): cv.string,
        vol.Optional("notification_alexa", default=""): cv.string,
        vol.Optional("telegram_group", default=""): cv.string,
        vol.Optional("photo_path", default=""): cv.string,
        vol.Optional("photo_url", default=""): cv.string,
        vol.Optional("parse_mode", default=""): cv.string,
    }
)


# ── Setup / teardown ──────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Notification Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        CONF_BRIDGE_URL: entry.data[CONF_BRIDGE_URL],
        CONF_BRIDGE_TOKEN: entry.data[CONF_BRIDGE_TOKEN],
    }

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register the notification service
    async def handle_notify(call: ServiceCall) -> None:
        await _async_handle_notify(hass, entry, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_NOTIFY,
        handle_notify,
        schema=SERVICE_NOTIFY_SCHEMA,
    )

    _LOGGER.info("Notification Manager integration loaded (entry %s)", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Shutdown coordinator (cancel polling)
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    coordinator = entry_data.get("coordinator") if isinstance(entry_data, dict) else None
    if coordinator:
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # Only remove service when last entry is removed
    if not hass.data[DOMAIN]:
        hass.services.async_remove(DOMAIN, SERVICE_NOTIFY)

    return unload_ok


# ── Core notification handler ─────────────────────────────────────────────────

def _get_runtime_config(entry: ConfigEntry) -> dict:
    """Return the effective runtime configuration.

    Priority: entry.data (set via reconfigure flow) → const (= const_private.py fallback).
    This lets the component work immediately after the first install (before reconfigure)
    while also respecting any values the user has saved through the UI.
    """
    d = entry.data
    return {
        "phone_targets": d.get("phone_targets") or _CONST_PHONE_TARGETS,
        "phone_default_targets": d.get("phone_default_targets") or list(_CONST_PHONE_DEFAULT_TARGETS),
        "whatsapp_contacts": d.get("whatsapp_contacts") or _CONST_WHATSAPP_CONTACTS,
        "alexa_players": d.get("alexa_players") or _CONST_ALEXA_PLAYERS,
        "alexa_en_target": d.get("alexa_en_target") or _CONST_ALEXA_EN_TARGET,
        "alexa_tts_volume": d.get("alexa_tts_volume") if d.get("alexa_tts_volume") is not None else _CONST_ALEXA_TTS_VOLUME,
        "alexa_post_tts_delay": d.get("alexa_post_tts_delay") if d.get("alexa_post_tts_delay") is not None else _CONST_ALEXA_POST_TTS_DELAY,
        "bridge_alert_chat_ids": d.get("bridge_alert_chat_ids") or list(_CONST_BRIDGE_ALERT_CHAT_IDS),
        "telegram_groups": d.get("telegram_groups") or _CONST_TELEGRAM_GROUPS,
    }


async def _async_handle_notify(
    hass: HomeAssistant, entry: ConfigEntry, call: ServiceCall
) -> None:
    """Handle the notification_manager.notify service call."""
    data = call.data
    message_tel: str = data.get("message_tel", "")
    message_alexa: str = data.get("message_alexa", "")
    message_alexa_en: str = data.get("message_alexa_en", "")
    notification_tel: str = data.get("notification_tel", "all")
    notification_whatsapp: str = data.get("notification_whatsapp", "none")
    notification_alexa: str = data.get("notification_alexa", "")
    telegram_group: str = data.get("telegram_group", "")
    photo_path: str = data.get("photo_path", "")
    photo_url: str = data.get("photo_url", "")
    parse_mode: str = data.get("parse_mode", "")

    _LOGGER.debug(
        "notify called — tel=%r alexa=%r alexa_en=%r n_tel=%r n_wa=%r n_alexa=%r group=%r photo=%r",
        message_tel,
        message_alexa,
        message_alexa_en,
        notification_tel,
        notification_whatsapp,
        notification_alexa,
        telegram_group,
        photo_path or photo_url,
    )

    # Run phone, alexa and whatsapp concurrently (parallel mode)
    tasks: list[asyncio.Task] = []

    if message_tel and notification_tel.lower() not in ("aucun", "none"):
        tasks.append(
            asyncio.ensure_future(
                _async_send_phone(
                    hass, entry, message_tel, notification_tel,
                    parse_mode=parse_mode, photo_path=photo_path, photo_url=photo_url,
                )
            )
        )

    if message_alexa and notification_alexa.lower() != "aucun":
        tasks.append(
            asyncio.ensure_future(
                _async_send_alexa(hass, entry, message_alexa, notification_alexa)
            )
        )

    if message_tel and notification_whatsapp.lower() not in ("none", "aucun", ""):
        bridge_url = entry.data.get(CONF_BRIDGE_URL, "")
        bridge_token = entry.data.get(CONF_BRIDGE_TOKEN, "")
        tasks.append(
            asyncio.ensure_future(
                _async_send_whatsapp(
                    hass, entry, message_tel, notification_whatsapp, bridge_url, bridge_token
                )
            )
        )

    if telegram_group:
        tasks.append(
            asyncio.ensure_future(
                _async_send_telegram_group(
                    hass, entry, message_tel, telegram_group,
                    parse_mode=parse_mode, photo_path=photo_path, photo_url=photo_url,
                )
            )
        )

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                _LOGGER.error("Notification task %d failed: %s", idx, result)

    # English Alexa — after 3-second delay
    if message_alexa_en:
        await asyncio.sleep(ALEXA_EN_DELAY)
        await _async_send_alexa_en(hass, entry, message_alexa_en)


# ── Phone + Telegram ──────────────────────────────────────────────────────────

async def _async_send_phone(
    hass: HomeAssistant, entry: ConfigEntry, message: str, notification_tel: str,
    parse_mode: str = "", photo_path: str = "", photo_url: str = "",
) -> None:
    """Send mobile push + Telegram notifications."""
    cfg = _get_runtime_config(entry)
    targets = _resolve_phone_targets(notification_tel, cfg["phone_default_targets"])
    _LOGGER.debug("Phone targets resolved: %s", targets)

    for target_key in targets:
        target_cfg = cfg["phone_targets"].get(target_key)
        if not target_cfg:
            _LOGGER.warning("Unknown phone target: %s", target_key)
            continue

        # Mobile push
        mobile_service = target_cfg["mobile"]
        domain, service = mobile_service.split(".", 1)
        try:
            await hass.services.async_call(
                domain,
                service,
                {"message": message},
                blocking=False,
            )
            _LOGGER.debug("Mobile push sent to %s", mobile_service)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to send mobile push to %s: %s", mobile_service, exc)

        # Telegram
        telegram_chat_id = target_cfg.get("telegram_chat_id")
        if telegram_chat_id:
            try:
                if photo_path or photo_url:
                    # Send photo with optional caption
                    photo_data: dict = {"chat_id": telegram_chat_id}
                    if photo_path:
                        photo_data["file"] = photo_path
                    elif photo_url:
                        photo_data["url"] = photo_url
                    if message:
                        photo_data["caption"] = message
                    if parse_mode:
                        photo_data["parse_mode"] = parse_mode
                    await hass.services.async_call(
                        "telegram_bot", "send_photo", photo_data, blocking=False,
                    )
                else:
                    # Send text message
                    msg_data: dict = {"chat_id": telegram_chat_id, "message": message}
                    if parse_mode:
                        msg_data["parse_mode"] = parse_mode
                    await hass.services.async_call(
                        "telegram_bot", "send_message", msg_data, blocking=False,
                    )
                _LOGGER.debug("Telegram sent to chat_id %s", telegram_chat_id)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "Failed to send Telegram to %s: %s", telegram_chat_id, exc
                )


def _resolve_phone_targets(notification_tel: str, phone_default_targets: list) -> list[str]:
    """Resolve notification_tel string to list of lowercase target keys."""
    value = notification_tel.strip().lower()
    if value in ("all", "", "all "):
        return list(phone_default_targets)
    return [t.strip() for t in value.split() if t.strip()]


# ── Telegram Groups ─────────────────────────────────────────────────────────────

async def _async_send_telegram_group(
    hass: HomeAssistant, entry: ConfigEntry, message: str, group_name: str,
    parse_mode: str = "", photo_path: str = "", photo_url: str = "",
) -> None:
    """Send a message or photo to a Telegram group by name."""
    cfg = _get_runtime_config(entry)
    telegram_groups = cfg.get("telegram_groups", {})
    chat_id = telegram_groups.get(group_name.strip().lower())
    if not chat_id:
        _LOGGER.warning("Unknown Telegram group: %s", group_name)
        return

    try:
        if photo_path or photo_url:
            photo_data: dict = {"chat_id": int(chat_id)}
            if photo_path:
                photo_data["file"] = photo_path
            elif photo_url:
                photo_data["url"] = photo_url
            if message:
                photo_data["caption"] = message
            if parse_mode:
                photo_data["parse_mode"] = parse_mode
            await hass.services.async_call(
                "telegram_bot", "send_photo", photo_data, blocking=False,
            )
        else:
            msg_data: dict = {"chat_id": int(chat_id), "message": message}
            if parse_mode:
                msg_data["parse_mode"] = parse_mode
            await hass.services.async_call(
                "telegram_bot", "send_message", msg_data, blocking=False,
            )
        _LOGGER.debug("Telegram group '%s' (chat_id=%s) sent", group_name, chat_id)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error(
            "Failed to send to Telegram group %s (chat_id=%s): %s",
            group_name, chat_id, exc,
        )


# ── Alexa TTS ─────────────────────────────────────────────────────────────────

async def _async_send_alexa(
    hass: HomeAssistant, entry: ConfigEntry, message: str, notification_alexa: str
) -> None:
    """Send Alexa TTS with volume save/restore."""
    cfg = _get_runtime_config(entry)
    targets = _resolve_alexa_targets(notification_alexa, cfg["alexa_players"])
    if not targets:
        _LOGGER.debug("No Alexa targets resolved for %r", notification_alexa)
        return

    _LOGGER.debug("Alexa targets: %s", targets)

    # 1. Save current volumes
    original_volumes: dict[str, float] = {}
    for entity_id in targets:
        state = hass.states.get(entity_id)
        if state is None or state.state in ("unavailable", "unknown"):
            _LOGGER.debug(
                "Alexa player %s unavailable, using default volume", entity_id
            )
            original_volumes[entity_id] = ALEXA_DEFAULT_VOLUME
        else:
            vol_attr = state.attributes.get("volume_level", ALEXA_DEFAULT_VOLUME)
            try:
                original_volumes[entity_id] = float(vol_attr)
            except (TypeError, ValueError):
                original_volumes[entity_id] = ALEXA_DEFAULT_VOLUME

    # Store volumes in input_text entity (comma-separated "entity:vol" pairs)
    vol_string = ",".join(
        f"{eid}:{v:.2f}" for eid, v in original_volumes.items()
    )
    try:
        await hass.services.async_call(
            "input_text",
            "set_value",
            {"entity_id": VOLUMES_ENTITY, "value": vol_string},
            blocking=False,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Could not store volumes in %s: %s", VOLUMES_ENTITY, exc)

    # 2. Set volume to TTS level
    alexa_tts_volume = cfg["alexa_tts_volume"]
    for entity_id in targets:
        try:
            await hass.services.async_call(
                "media_player",
                "volume_set",
                {"entity_id": entity_id, "volume_level": alexa_tts_volume},
                blocking=False,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to set volume for %s: %s", entity_id, exc)

    # 3. Send TTS
    try:
        await hass.services.async_call(
            "notify",
            "alexa_media",
            {"message": message, "target": targets, "data": {"type": "tts"}},
            blocking=False,
        )
        _LOGGER.debug("Alexa TTS sent to %s", targets)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to send Alexa TTS: %s", exc)

    # 4. Wait for speech to finish
    await asyncio.sleep(cfg["alexa_post_tts_delay"])

    # 5. Restore original volumes
    for entity_id, vol_level in original_volumes.items():
        try:
            await hass.services.async_call(
                "media_player",
                "volume_set",
                {"entity_id": entity_id, "volume_level": vol_level},
                blocking=False,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to restore volume for %s: %s", entity_id, exc)


def _resolve_alexa_targets(notification_alexa: str, alexa_players: list) -> list[str]:
    """Resolve notification_alexa string to list of entity_ids."""
    value = notification_alexa.strip().lower()
    if not value:
        # Default: "show" keyword
        keyword = ALEXA_DEFAULT_KEYWORD
        return [p for p in alexa_players if keyword in p]

    keywords = [k.strip() for k in value.split() if k.strip()]
    matched: list[str] = []
    for keyword in keywords:
        for player in alexa_players:
            if keyword in player and player not in matched:
                matched.append(player)
    return matched


async def _async_send_alexa_en(hass: HomeAssistant, entry: ConfigEntry, message: str) -> None:
    """Send English Alexa TTS to the dedicated English Echo."""
    cfg = _get_runtime_config(entry)
    alexa_en_target = cfg["alexa_en_target"]
    try:
        await hass.services.async_call(
            "notify",
            "alexa_media",
            {
                "message": message,
                "target": [alexa_en_target],
                "data": {"type": "tts"},
            },
            blocking=False,
        )
        _LOGGER.debug("English Alexa TTS sent: %r", message)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to send English Alexa TTS: %s", exc)


# ── WhatsApp ──────────────────────────────────────────────────────────────────

async def _async_send_whatsapp(
    hass: HomeAssistant,
    entry: ConfigEntry,
    message: str,
    notification_whatsapp: str,
    bridge_url: str,
    bridge_token: str,
) -> None:
    """Send WhatsApp messages via whatsmeow-bridge REST API."""
    if not bridge_url:
        _LOGGER.error("WhatsApp bridge URL not configured")
        return

    cfg = _get_runtime_config(entry)
    targets = _resolve_whatsapp_targets(notification_whatsapp, cfg["whatsapp_contacts"])
    if not targets:
        _LOGGER.debug("No WhatsApp targets for %r", notification_whatsapp)
        return

    _LOGGER.debug("WhatsApp targets: %s", targets)

    session = async_get_clientsession(hass, verify_ssl=False)
    headers = {
        "Authorization": f"Bearer {bridge_token}",
        "Content-Type": "application/json",
    }
    url = bridge_url.rstrip("/") + BRIDGE_SEND_ENDPOINT

    for jid in targets:
        success = False
        last_error: Exception | None = None

        for attempt in range(1, BRIDGE_RETRIES + 1):
            try:
                async with session.post(
                    url,
                    json={"to": jid, "text": message},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=BRIDGE_TIMEOUT),
                ) as resp:
                    if resp.status < 300:
                        _LOGGER.debug("WhatsApp sent to %s (attempt %d)", jid, attempt)
                        success = True
                        break
                    body = await resp.text()
                    _LOGGER.warning(
                        "WhatsApp bridge returned %d for %s (attempt %d): %s",
                        resp.status,
                        jid,
                        attempt,
                        body[:200],
                    )
                    last_error = Exception(f"HTTP {resp.status}: {body[:200]}")
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning(
                    "WhatsApp bridge error for %s (attempt %d): %s",
                    jid,
                    attempt,
                    exc,
                )
                last_error = exc

            if attempt < BRIDGE_RETRIES:
                await asyncio.sleep(2**attempt)  # exponential backoff

        if not success:
            _LOGGER.error(
                "WhatsApp delivery failed for %s after %d retries: %s",
                jid,
                BRIDGE_RETRIES,
                last_error,
            )
            # Notify René and Nicole via Telegram
            summary = message[:100] + ("…" if len(message) > 100 else "")
            alert = (
                f"⚠️ WhatsApp bridge indisponible — message non délivré: {summary}"
            )
            await _async_send_bridge_alert(hass, entry, alert)


def _resolve_whatsapp_targets(notification_whatsapp: str, whatsapp_contacts: dict) -> list[str]:
    """Resolve notification_whatsapp string to list of JIDs."""
    value = notification_whatsapp.strip().lower()
    if value in ("none", "aucun", ""):
        return []
    names = [n.strip() for n in value.split() if n.strip()]
    jids: list[str] = []
    for name in names:
        jid = whatsapp_contacts.get(name)
        if jid:
            jids.append(jid)
        else:
            _LOGGER.warning("Unknown WhatsApp contact: %s", name)
    return jids


async def _async_send_bridge_alert(hass: HomeAssistant, entry: ConfigEntry, message: str) -> None:
    """Alert admins when the WhatsApp bridge is down."""
    cfg = _get_runtime_config(entry)
    for chat_id in cfg["bridge_alert_chat_ids"]:
        try:
            await hass.services.async_call(
                "telegram_bot",
                "send_message",
                {"chat_id": chat_id, "message": message},
                blocking=False,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Failed to send bridge alert to Telegram %s: %s", chat_id, exc
            )
