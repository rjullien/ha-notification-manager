"""Phone, Telegram and WhatsApp delivery."""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .bridge_http import async_get_bridge_session
from .const import (
    BRIDGE_RETRIES,
    BRIDGE_SEND_ENDPOINT,
    BRIDGE_TIMEOUT,
    DEFAULT_VERIFY_SSL,
)
from .jid_utils import jid_to_phone
from .runtime import _get_runtime_config
from .telegram_text import PARSE_MODE_PLAIN

_LOGGER = logging.getLogger(__name__)

async def _async_send_phone(
    hass: HomeAssistant, entry: ConfigEntry, message: str, notification_tel: str,
    parse_mode: str = "", photo_path: str = "", photo_url: str = "",
) -> None:
    """Send mobile push + Telegram notifications (all targets in parallel)."""
    cfg = _get_runtime_config(entry)
    targets = _resolve_phone_targets(notification_tel, cfg["phone_default_targets"])
    _LOGGER.debug("Phone targets resolved: %s", targets)

    sends: list[Awaitable] = []
    for target_key in targets:
        target_cfg = cfg["phone_targets"].get(target_key)
        if not target_cfg:
            _LOGGER.warning("Unknown phone target: %s", target_key)
            continue
        sends.append(
            _async_send_phone_target(
                hass, target_cfg, message,
                parse_mode=parse_mode, photo_path=photo_path, photo_url=photo_url,
            )
        )

    if sends:
        await asyncio.gather(*sends)


async def _async_send_phone_target(
    hass: HomeAssistant, target_cfg: dict, message: str,
    parse_mode: str = "", photo_path: str = "", photo_url: str = "",
) -> None:
    """Send mobile push + Telegram to a single phone target."""
    # Mobile push — blocking=True so delivery errors actually surface here.
    mobile_service = target_cfg["mobile"]
    domain, service = mobile_service.split(".", 1)
    try:
        await hass.services.async_call(
            domain,
            service,
            {"message": message},
            blocking=True,
        )
        _LOGGER.debug("Mobile push sent to %s", mobile_service)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("Failed to send mobile push to %s: %s", mobile_service, exc)

    # Telegram
    telegram_chat_id = target_cfg.get("telegram_chat_id")
    if telegram_chat_id:
        try:
            await _async_call_telegram(
                hass, telegram_chat_id, message,
                parse_mode=parse_mode, photo_path=photo_path, photo_url=photo_url,
            )
            _LOGGER.debug("Telegram sent to chat_id %s", telegram_chat_id)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Failed to send Telegram to %s: %s", telegram_chat_id, exc
            )


async def _async_call_telegram(
    hass: HomeAssistant, chat_id: int, message: str,
    parse_mode: str = "", photo_path: str = "", photo_url: str = "",
) -> None:
    """Call telegram_bot.send_photo / send_message (blocking, errors raise).

    ``parse_mode`` is *always* sent. Leaving the key out does not give plain
    text: telegram_bot falls back to its platform default, which is markdown
    (see telegram_text module docstring), so a message holding an unbalanced
    "_" or "*" — entity_ids, snake_case labels, file names — is rejected by
    Telegram with "Can't parse entities" and the notification is lost.
    An empty ``parse_mode`` therefore resolves to PARSE_MODE_PLAIN, matching
    what services.yaml already advertises ("Aucun (texte brut)" as default).
    """
    effective_parse_mode = parse_mode or PARSE_MODE_PLAIN

    if photo_path or photo_url:
        # Explicit rule: when both are set, photo_path wins (photo_url ignored).
        photo_data: dict = {"chat_id": chat_id}
        if photo_path:
            photo_data["file"] = photo_path
        else:
            photo_data["url"] = photo_url
        if message:
            photo_data["caption"] = message
        photo_data["parse_mode"] = effective_parse_mode
        await hass.services.async_call(
            "telegram_bot", "send_photo", photo_data, blocking=True,
        )
    else:
        msg_data: dict = {
            "chat_id": chat_id,
            "message": message,
            "parse_mode": effective_parse_mode,
        }
        await hass.services.async_call(
            "telegram_bot", "send_message", msg_data, blocking=True,
        )


def _resolve_phone_targets(notification_tel: str, phone_default_targets: list) -> list[str]:
    """Resolve notification_tel string to list of lowercase target keys."""
    value = notification_tel.strip().lower()
    if value in ("all", ""):
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
        chat_id_int = int(chat_id)
    except (TypeError, ValueError):
        _LOGGER.error(
            "Invalid chat_id %r for Telegram group %s — must be an integer",
            chat_id, group_name,
        )
        return

    try:
        await _async_call_telegram(
            hass, chat_id_int, message,
            parse_mode=parse_mode, photo_path=photo_path, photo_url=photo_url,
        )
        _LOGGER.debug("Telegram group '%s' (chat_id=%s) sent", group_name, chat_id_int)
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error(
            "Failed to send to Telegram group %s (chat_id=%s): %s",
            group_name, chat_id_int, exc,
        )


async def _async_send_whatsapp(
    hass: HomeAssistant,
    entry: ConfigEntry,
    message: str,
    notification_whatsapp: str,
    bridge_url: str,
    bridge_token: str,
    verify_ssl: bool = DEFAULT_VERIFY_SSL,
) -> None:
    """Send WhatsApp messages via whatsmeow-bridge REST API (recipients in parallel).

    WhatsApp is plain-text only: the bridge payload is ``{phone, message}`` with
    no Telegram-style ``parse_mode``. Callers' ``parse_mode`` applies to Telegram
    channels only.
    """
    if not bridge_url:
        _LOGGER.error("WhatsApp bridge URL not configured")
        return

    cfg = _get_runtime_config(entry)
    targets = _resolve_whatsapp_targets(notification_whatsapp, cfg["whatsapp_contacts"])
    if not targets:
        _LOGGER.debug("No WhatsApp targets for %r", notification_whatsapp)
        return

    _LOGGER.debug("WhatsApp targets: %s", targets)

    session = async_get_bridge_session(hass, verify_ssl)
    headers = {
        "Content-Type": "application/json",
    }
    url = bridge_url.rstrip("/") + BRIDGE_SEND_ENDPOINT

    results = await asyncio.gather(
        *(
            _async_send_whatsapp_to_jid(session, url, headers, jid, message)
            for jid in targets
        )
    )

    failed = [jid for jid, ok in zip(targets, results) if not ok]
    if failed:
        # Single aggregated alert instead of one per recipient
        summary = message[:100] + ("…" if len(message) > 100 else "")
        alert = (
            f"⚠️ WhatsApp bridge indisponible — message non délivré "
            f"({len(failed)}/{len(targets)} destinataires): {summary}"
        )
        await _async_send_bridge_alert(hass, entry, alert)


async def _async_send_whatsapp_to_jid(
    session: aiohttp.ClientSession,
    url: str,
    headers: dict,
    jid: str,
    message: str,
) -> bool:
    """Send one WhatsApp message with retries. Returns True on success."""
    last_error: Exception | None = None
    # Convert JID to phone number for GoWA bridge (groups @g.us pass through).
    phone = jid_to_phone(jid)

    for attempt in range(1, BRIDGE_RETRIES + 1):
        try:
            async with session.post(
                url,
                json={"phone": phone, "message": message},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=BRIDGE_TIMEOUT),
            ) as resp:
                if resp.status < 300:
                    _LOGGER.debug("WhatsApp sent to %s (attempt %d)", jid, attempt)
                    return True
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

    _LOGGER.error(
        "WhatsApp delivery failed for %s after %d retries: %s",
        jid,
        BRIDGE_RETRIES,
        last_error,
    )
    return False


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
    """Alert admins when the WhatsApp bridge is down.

    Always send ``parse_mode=plain_text``: omitting it lets telegram_bot default
    to markdown, so entity_ids / underscores in the alert body can fail silently.
    """
    cfg = _get_runtime_config(entry)
    for chat_id in cfg["bridge_alert_chat_ids"]:
        try:
            await hass.services.async_call(
                "telegram_bot",
                "send_message",
                {
                    "chat_id": chat_id,
                    "message": message,
                    "parse_mode": PARSE_MODE_PLAIN,
                },
                blocking=True,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Failed to send bridge alert to Telegram %s: %s", chat_id, exc
            )
