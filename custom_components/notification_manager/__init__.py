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
    ALEXA_EN_TARGET,
    ALEXA_PLAYERS,
    ALEXA_POST_TTS_DELAY,
    ALEXA_TTS_VOLUME,
    BRIDGE_ALERT_CHAT_IDS,
    BRIDGE_HEALTH_ENDPOINT,
    BRIDGE_RETRIES,
    BRIDGE_SEND_ENDPOINT,
    BRIDGE_TIMEOUT,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    DOMAIN,
    PHONE_DEFAULT_TARGETS,
    PHONE_TARGETS,
    PLATFORMS,
    SERVICE_NOTIFY,
    VOLUMES_ENTITY,
    WHATSAPP_CONTACTS,
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

    _LOGGER.debug(
        "notify called — tel=%r alexa=%r alexa_en=%r n_tel=%r n_wa=%r n_alexa=%r",
        message_tel,
        message_alexa,
        message_alexa_en,
        notification_tel,
        notification_whatsapp,
        notification_alexa,
    )

    # Run phone, alexa and whatsapp concurrently (parallel mode)
    tasks: list[asyncio.Task] = []

    if message_tel and notification_tel.lower() not in ("aucun", "none"):
        tasks.append(
            asyncio.ensure_future(
                _async_send_phone(hass, message_tel, notification_tel)
            )
        )

    if message_alexa and notification_alexa.lower() != "aucun":
        tasks.append(
            asyncio.ensure_future(
                _async_send_alexa(hass, message_alexa, notification_alexa)
            )
        )

    if message_tel and notification_whatsapp.lower() not in ("none", "aucun", ""):
        bridge_url = entry.data.get(CONF_BRIDGE_URL, "")
        bridge_token = entry.data.get(CONF_BRIDGE_TOKEN, "")
        tasks.append(
            asyncio.ensure_future(
                _async_send_whatsapp(
                    hass, message_tel, notification_whatsapp, bridge_url, bridge_token
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
        await _async_send_alexa_en(hass, message_alexa_en)


# ── Phone + Telegram ──────────────────────────────────────────────────────────

async def _async_send_phone(
    hass: HomeAssistant, message: str, notification_tel: str
) -> None:
    """Send mobile push + Telegram notifications."""
    targets = _resolve_phone_targets(notification_tel)
    _LOGGER.debug("Phone targets resolved: %s", targets)

    for target_key in targets:
        target_cfg = PHONE_TARGETS.get(target_key)
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
                await hass.services.async_call(
                    "telegram_bot",
                    "send_message",
                    {"chat_id": telegram_chat_id, "message": message},
                    blocking=False,
                )
                _LOGGER.debug("Telegram sent to chat_id %s", telegram_chat_id)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "Failed to send Telegram to %s: %s", telegram_chat_id, exc
                )


def _resolve_phone_targets(notification_tel: str) -> list[str]:
    """Resolve notification_tel string to list of lowercase target keys."""
    value = notification_tel.strip().lower()
    if value in ("all", "", "all "):
        return list(PHONE_DEFAULT_TARGETS)
    return [t.strip() for t in value.split() if t.strip()]


# ── Alexa TTS ─────────────────────────────────────────────────────────────────

async def _async_send_alexa(
    hass: HomeAssistant, message: str, notification_alexa: str
) -> None:
    """Send Alexa TTS with volume save/restore."""
    targets = _resolve_alexa_targets(notification_alexa)
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
    for entity_id in targets:
        try:
            await hass.services.async_call(
                "media_player",
                "volume_set",
                {"entity_id": entity_id, "volume_level": ALEXA_TTS_VOLUME},
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
    await asyncio.sleep(ALEXA_POST_TTS_DELAY)

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


def _resolve_alexa_targets(notification_alexa: str) -> list[str]:
    """Resolve notification_alexa string to list of entity_ids."""
    value = notification_alexa.strip().lower()
    if not value:
        # Default: "show" keyword
        keyword = ALEXA_DEFAULT_KEYWORD
        return [p for p in ALEXA_PLAYERS if keyword in p]

    keywords = [k.strip() for k in value.split() if k.strip()]
    matched: list[str] = []
    for keyword in keywords:
        for player in ALEXA_PLAYERS:
            if keyword in player and player not in matched:
                matched.append(player)
    return matched


async def _async_send_alexa_en(hass: HomeAssistant, message: str) -> None:
    """Send English Alexa TTS to the dedicated English Echo."""
    try:
        await hass.services.async_call(
            "notify",
            "alexa_media",
            {
                "message": message,
                "target": [ALEXA_EN_TARGET],
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
    message: str,
    notification_whatsapp: str,
    bridge_url: str,
    bridge_token: str,
) -> None:
    """Send WhatsApp messages via whatsmeow-bridge REST API."""
    if not bridge_url:
        _LOGGER.error("WhatsApp bridge URL not configured")
        return

    targets = _resolve_whatsapp_targets(notification_whatsapp)
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
            await _async_send_bridge_alert(hass, alert)


def _resolve_whatsapp_targets(notification_whatsapp: str) -> list[str]:
    """Resolve notification_whatsapp string to list of JIDs."""
    value = notification_whatsapp.strip().lower()
    if value in ("none", "aucun", ""):
        return []
    names = [n.strip() for n in value.split() if n.strip()]
    jids: list[str] = []
    for name in names:
        jid = WHATSAPP_CONTACTS.get(name)
        if jid:
            jids.append(jid)
        else:
            _LOGGER.warning("Unknown WhatsApp contact: %s", name)
    return jids


async def _async_send_bridge_alert(hass: HomeAssistant, message: str) -> None:
    """Alert admins when the WhatsApp bridge is down."""
    for chat_id in BRIDGE_ALERT_CHAT_IDS:
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
