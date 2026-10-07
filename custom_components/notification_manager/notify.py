"""Notify service handler."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall

from . import alexa as alexa_mod
from . import messaging as messaging_mod
from .const import (
    ALEXA_POST_TTS_DELAY as _CONST_ALEXA_POST_TTS_DELAY,
    ALEXA_TTS_VOLUME as _CONST_ALEXA_TTS_VOLUME,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    DEFAULT_BRIDGE_URL,
)
from .runtime import _entry_verify_ssl, _get_runtime_config, _run_logged

_LOGGER = logging.getLogger(__name__)

# Re-exports for tests/consumers that historically imported helpers from notify.
__all__ = [
    "_async_handle_notify",
    "_entry_verify_ssl",
    "_get_runtime_config",
    "_run_logged",
    "_CONST_ALEXA_POST_TTS_DELAY",
    "_CONST_ALEXA_TTS_VOLUME",
]


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

    # Alexa runs as detached background tasks: the FR flow holds the speakers
    # for post_tts_delay seconds and the EN flow has its own start delay —
    # neither should block the service call nor wait on slow WhatsApp retries.
    # Propagated to the emission log so a caller can recognise its own TTS and
    # avoid correlating against sound it produced itself.
    context_id = getattr(getattr(call, "context", None), "id", None)
    if not isinstance(context_id, str):
        context_id = None

    # Normalize channel selectors consistently (strip + lower) before gating.
    tel_selector = notification_tel.strip().lower()
    wa_selector = notification_whatsapp.strip().lower()
    alexa_selector = notification_alexa.strip().lower()

    if message_alexa and alexa_selector not in (
        "aucun", "none", "off", "disable"
    ):
        hass.async_create_task(
            _run_logged(
                alexa_mod._async_send_alexa(
                    hass, entry, message_alexa, notification_alexa, context_id
                ),
                "Alexa TTS",
            )
        )

    # message_alexa_en / alexa_en_target: unused by the maintainer; left as-is
    # (candidate for later removal). Gating vs notification_alexa unchanged.
    if message_alexa_en:
        hass.async_create_task(
            _run_logged(
                alexa_mod._async_send_alexa_en_delayed(
                    hass, entry, message_alexa_en, context_id
                ),
                "English Alexa TTS",
            )
        )

    # Phone, WhatsApp and Telegram group run concurrently and are awaited so
    # automations calling the service in blocking mode get delivery feedback.
    # Use hass.async_create_task (same as Alexa) so HA tracks/cancels these on unload.
    tasks: list[asyncio.Task] = []

    if message_tel and tel_selector not in ("aucun", "none"):
        tasks.append(
            hass.async_create_task(
                messaging_mod._async_send_phone(
                    hass, entry, message_tel, notification_tel,
                    parse_mode=parse_mode, photo_path=photo_path, photo_url=photo_url,
                )
            )
        )

    # WhatsApp is gated on its selector alone — not on message_tel truthiness —
    # so a WhatsApp-only notify is not skipped when phone/Telegram text is empty.
    # Message source: reuse message_tel (shared push/Telegram/WhatsApp body per
    # schema/README; there is no separate message_whatsapp field). An empty
    # message_tel is forwarded as an empty string to the bridge.
    if wa_selector not in ("none", "aucun", ""):
        bridge_url = entry.data.get(CONF_BRIDGE_URL, "") or DEFAULT_BRIDGE_URL
        bridge_token = entry.data.get(CONF_BRIDGE_TOKEN, "")
        tasks.append(
            hass.async_create_task(
                messaging_mod._async_send_whatsapp(
                    hass, entry, message_tel, notification_whatsapp,
                    bridge_url, bridge_token, _entry_verify_ssl(entry),
                )
            )
        )

    if telegram_group:
        if message_tel or photo_path or photo_url:
            tasks.append(
                hass.async_create_task(
                    messaging_mod._async_send_telegram_group(
                        hass, entry, message_tel, telegram_group,
                        parse_mode=parse_mode, photo_path=photo_path, photo_url=photo_url,
                    )
                )
            )
        else:
            _LOGGER.warning(
                "telegram_group %r requested but no message_tel/photo provided — skipping",
                telegram_group,
            )

    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for idx, result in enumerate(results):
            if isinstance(result, Exception):
                _LOGGER.error("Notification task %d failed: %s", idx, result)
