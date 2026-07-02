"""Notification Manager - Home Assistant Custom Component.

Replaces the ManageTTS YAML script with a proper Python integration.
Supports Alexa TTS, phone/Telegram notifications, and WhatsApp via
the whatsmeow-bridge REST API.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import config_validation as cv

from .bridge_http import async_close_bridge_sessions, async_get_bridge_session
from .watchdog import async_setup_watchdog, EntityWatchdog
from .const import (
    ALEXA_DEFAULT_KEYWORD,
    ALEXA_DEFAULT_VOLUME,
    ALEXA_EN_DELAY,
    ALEXA_EN_TARGET as _CONST_ALEXA_EN_TARGET,
    ALEXA_PLAYERS as _CONST_ALEXA_PLAYERS,
    ALEXA_POST_TTS_DELAY as _CONST_ALEXA_POST_TTS_DELAY,
    ALEXA_TTS_VOLUME as _CONST_ALEXA_TTS_VOLUME,
    BRIDGE_ALERT_CHAT_IDS as _CONST_BRIDGE_ALERT_CHAT_IDS,
    BRIDGE_RETRIES,
    BRIDGE_SEND_ENDPOINT,
    BRIDGE_TIMEOUT,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_VERIFY_SSL,
    DEFAULT_BRIDGE_URL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PHONE_DEFAULT_TARGETS as _CONST_PHONE_DEFAULT_TARGETS,
    PHONE_TARGETS as _CONST_PHONE_TARGETS,
    PLATFORMS,
    SERVICE_NOTIFY,
    WHATSAPP_CONTACTS as _CONST_WHATSAPP_CONTACTS,
    TELEGRAM_GROUPS as _CONST_TELEGRAM_GROUPS,
)

_LOGGER = logging.getLogger(__name__)

DATA_ALEXA_LOCK = "_alexa_lock"

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


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _run_logged(coro: Awaitable, label: str) -> None:
    """Await a background coroutine and log (never raise) its failure."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("%s failed: %s", label, exc)


async def _async_require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Restrict a service to admin users.

    Calls without a user context (automations, scripts, system) are allowed.
    """
    user_id = getattr(call.context, "user_id", None)
    if not user_id:
        return
    user = await hass.auth.async_get_user(user_id)
    if user is None or not user.is_admin:
        raise Unauthorized()


def _entry_verify_ssl(entry: ConfigEntry) -> bool:
    """Return the configured TLS verification flag for the bridge."""
    return entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)


# ── Setup / teardown ──────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Notification Manager from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        CONF_BRIDGE_URL: entry.data.get(CONF_BRIDGE_URL, ""),
        CONF_BRIDGE_TOKEN: entry.data.get(CONF_BRIDGE_TOKEN, ""),
        CONF_VERIFY_SSL: _entry_verify_ssl(entry),
    }
    # Serialises Alexa volume save→TTS→restore cycles so overlapping notify
    # calls can't capture the TTS volume as the "original" one.
    hass.data[DOMAIN].setdefault(DATA_ALEXA_LOCK, asyncio.Lock())

    # Forward to sensor platform
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry whenever its config changes (options flow or reconfigure
    # flow). Without this, the coordinator keeps polling the old bridge URL/token
    # until Home Assistant restarts.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Register the notification service
    async def handle_notify(call: ServiceCall) -> None:
        await _async_handle_notify(hass, entry, call)

    hass.services.async_register(
        DOMAIN,
        SERVICE_NOTIFY,
        handle_notify,
        schema=SERVICE_NOTIFY_SCHEMA,
    )

    # ── Bridge diagnostic services (admin only — logs may contain numbers
    #    and message contents) ──────────────────────────────────────────────

    BRIDGE_LOGS_SCHEMA = vol.Schema({
        vol.Optional("limit", default=100): vol.All(int, vol.Range(min=1, max=500)),
        vol.Optional("level", default=""): vol.In(["", "error", "warn", "info"]),
    })

    async def handle_bridge_logs(call: ServiceCall) -> dict:
        """Fetch logs from the WhatsApp bridge."""
        await _async_require_admin(hass, call)

        bridge_url = entry.data.get(CONF_BRIDGE_URL, "") or DEFAULT_BRIDGE_URL
        bridge_token = entry.data.get(CONF_BRIDGE_TOKEN, "")

        if not bridge_url:
            return {"error": "bridge_url not configured"}

        url = bridge_url.rstrip("/") + "/logs"
        params = {"limit": str(call.data.get("limit", 100))}
        level = call.data.get("level", "").strip()
        if level:
            params["level"] = level

        headers = {"Authorization": f"Bearer {bridge_token}"}
        http_session = async_get_bridge_session(hass, _entry_verify_ssl(entry))

        try:
            async with http_session.get(
                url,
                headers=headers,
                params=params,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return {"error": f"HTTP {resp.status}", "body": await resp.text()}
                return await resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    hass.services.async_register(
        DOMAIN,
        "whatsapp_bridge_logs",
        handle_bridge_logs,
        schema=BRIDGE_LOGS_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )

    async def handle_bridge_restart(call: ServiceCall) -> dict:
        """Restart the WhatsApp bridge (soft reconnect)."""
        await _async_require_admin(hass, call)

        bridge_url = entry.data.get(CONF_BRIDGE_URL, "") or DEFAULT_BRIDGE_URL
        bridge_token = entry.data.get(CONF_BRIDGE_TOKEN, "")

        if not bridge_url:
            return {"error": "bridge_url not configured"}

        url = bridge_url.rstrip("/") + "/restart"
        headers = {
            "Authorization": f"Bearer {bridge_token}",
            "Content-Type": "application/json",
        }
        http_session = async_get_bridge_session(hass, _entry_verify_ssl(entry))

        try:
            async with http_session.post(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return {"error": f"HTTP {resp.status}", "body": await resp.text()}
                return await resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)}

    hass.services.async_register(
        DOMAIN,
        "whatsapp_bridge_restart",
        handle_bridge_restart,
        schema=vol.Schema({}),
        supports_response=SupportsResponse.ONLY,
    )

    # Start entity watchdog
    watchdog = async_setup_watchdog(hass, entry)
    hass.data[DOMAIN][entry.entry_id]["watchdog"] = watchdog

    _LOGGER.info("Notification Manager integration loaded (entry %s)", entry.entry_id)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Stop entity watchdog
    entry_data = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    watchdog = entry_data.get("watchdog") if isinstance(entry_data, dict) else None
    if watchdog and isinstance(watchdog, EntityWatchdog):
        watchdog.stop()

    # Shutdown coordinator (cancel polling)
    coordinator = entry_data.get("coordinator") if isinstance(entry_data, dict) else None
    if coordinator:
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # Only remove services when the last entry is removed (internal keys
    # such as the Alexa lock or cached sessions start with "_").
    remaining_entries = [k for k in hass.data.get(DOMAIN, {}) if not k.startswith("_")]
    if not remaining_entries:
        hass.services.async_remove(DOMAIN, SERVICE_NOTIFY)
        hass.services.async_remove(DOMAIN, "whatsapp_bridge_logs")
        hass.services.async_remove(DOMAIN, "whatsapp_bridge_restart")
        await async_close_bridge_sessions(hass)
        hass.data.pop(DOMAIN, None)

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its data changes.

    Triggered by the options flow and the reconfigure flow so the coordinator
    picks up the new bridge URL/token without requiring a HA restart.
    """
    await hass.config_entries.async_reload(entry.entry_id)


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

    # Alexa runs as detached background tasks: the FR flow holds the speakers
    # for post_tts_delay seconds and the EN flow has its own start delay —
    # neither should block the service call nor wait on slow WhatsApp retries.
    if message_alexa and notification_alexa.strip().lower() not in (
        "aucun", "none", "off", "disable"
    ):
        hass.async_create_task(
            _run_logged(
                _async_send_alexa(hass, entry, message_alexa, notification_alexa),
                "Alexa TTS",
            )
        )

    if message_alexa_en:
        hass.async_create_task(
            _run_logged(
                _async_send_alexa_en_delayed(hass, entry, message_alexa_en),
                "English Alexa TTS",
            )
        )

    # Phone, WhatsApp and Telegram group run concurrently and are awaited so
    # automations calling the service in blocking mode get delivery feedback.
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

    if message_tel and notification_whatsapp.lower() not in ("none", "aucun", ""):
        bridge_url = entry.data.get(CONF_BRIDGE_URL, "") or DEFAULT_BRIDGE_URL
        bridge_token = entry.data.get(CONF_BRIDGE_TOKEN, "")
        tasks.append(
            asyncio.ensure_future(
                _async_send_whatsapp(
                    hass, entry, message_tel, notification_whatsapp,
                    bridge_url, bridge_token, _entry_verify_ssl(entry),
                )
            )
        )

    if telegram_group:
        if message_tel or photo_path or photo_url:
            tasks.append(
                asyncio.ensure_future(
                    _async_send_telegram_group(
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


# ── Phone + Telegram ──────────────────────────────────────────────────────────

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
    """Call telegram_bot.send_photo / send_message (blocking, errors raise)."""
    if photo_path or photo_url:
        photo_data: dict = {"chat_id": chat_id}
        if photo_path:
            photo_data["file"] = photo_path
        elif photo_url:
            photo_data["url"] = photo_url
        if message:
            photo_data["caption"] = message
        if parse_mode:
            photo_data["parse_mode"] = parse_mode
        await hass.services.async_call(
            "telegram_bot", "send_photo", photo_data, blocking=True,
        )
    else:
        msg_data: dict = {"chat_id": chat_id, "message": message}
        if parse_mode:
            msg_data["parse_mode"] = parse_mode
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


# ── Alexa TTS ─────────────────────────────────────────────────────────────────

async def _async_set_volume(hass: HomeAssistant, entity_id: str, volume: float) -> None:
    """Set a media_player volume (blocking, warning on failure)."""
    try:
        await hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": entity_id, "volume_level": volume},
            blocking=True,
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.warning("Failed to set volume for %s: %s", entity_id, exc)


async def _async_send_alexa(
    hass: HomeAssistant, entry: ConfigEntry, message: str, notification_alexa: str
) -> None:
    """Send Alexa TTS with volume save/restore.

    The whole save→set→TTS→restore cycle is serialised behind a lock so that
    overlapping notify calls cannot capture the TTS volume as the "original"
    volume (which would leave the speakers stuck at TTS level).
    """
    # Skip if alexa_media integration is not available on this instance
    if not hass.services.has_service("notify", "alexa_media"):
        _LOGGER.debug("Alexa TTS skipped: notify.alexa_media service not available")
        return

    cfg = _get_runtime_config(entry)
    targets = _resolve_alexa_targets(notification_alexa, cfg["alexa_players"])
    if not targets:
        _LOGGER.debug("No Alexa targets resolved for %r", notification_alexa)
        return

    # Filter out unavailable players (e.g. stale _2 duplicates from re-added integrations)
    available_targets = [
        t for t in targets
        if (state := hass.states.get(t)) is not None and state.state != "unavailable"
    ]
    if not available_targets:
        _LOGGER.warning(
            "All resolved Alexa targets are unavailable: %s (skipping TTS)", targets
        )
        return
    targets = available_targets

    _LOGGER.debug("Alexa targets: %s", targets)

    lock: asyncio.Lock = hass.data.setdefault(DOMAIN, {}).setdefault(
        DATA_ALEXA_LOCK, asyncio.Lock()
    )

    async with lock:
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

        # 2. Set volume to TTS level — blocking + awaited BEFORE the TTS is
        #    sent, so speech can never start at the old volume.
        alexa_tts_volume = cfg["alexa_tts_volume"]
        await asyncio.gather(
            *(_async_set_volume(hass, eid, alexa_tts_volume) for eid in targets)
        )

        # 3. Send TTS
        try:
            await hass.services.async_call(
                "notify",
                "alexa_media",
                {"message": message, "target": targets, "data": {"type": "tts"}},
                blocking=True,
            )
            _LOGGER.debug("Alexa TTS sent to %s", targets)
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error("Failed to send Alexa TTS: %s", exc)

        # 4. Wait for speech to finish
        await asyncio.sleep(cfg["alexa_post_tts_delay"])

        # 5. Restore original volumes
        await asyncio.gather(
            *(
                _async_set_volume(hass, eid, vol_level)
                for eid, vol_level in original_volumes.items()
            )
        )


def _resolve_alexa_targets(notification_alexa: str, alexa_players: list) -> list[str]:
    """Resolve notification_alexa string to list of entity_ids."""
    value = notification_alexa.strip().lower()
    if not value:
        # Default: "show" keyword
        keyword = ALEXA_DEFAULT_KEYWORD
        return [p for p in alexa_players if keyword in p]

    # Special values
    if value in ("aucun", "none", "off", "disable"):
        return []

    keywords = [k.strip() for k in value.split() if k.strip()]
    matched: list[str] = []
    for keyword in keywords:
        for player in alexa_players:
            if keyword in player and player not in matched:
                matched.append(player)
    return matched


async def _async_send_alexa_en_delayed(
    hass: HomeAssistant, entry: ConfigEntry, message: str
) -> None:
    """Send the English Alexa TTS after its fixed delay.

    Runs as an independent task so the delay starts immediately and is not
    pushed back by slow channels (e.g. WhatsApp retries when the bridge is
    down used to delay it by 30+ seconds).
    """
    await asyncio.sleep(ALEXA_EN_DELAY)
    await _async_send_alexa_en(hass, entry, message)


async def _async_send_alexa_en(hass: HomeAssistant, entry: ConfigEntry, message: str) -> None:
    """Send English Alexa TTS to the dedicated English Echo."""
    # Skip if alexa_media integration is not available on this instance
    if not hass.services.has_service("notify", "alexa_media"):
        _LOGGER.debug("Alexa EN TTS skipped: notify.alexa_media service not available")
        return

    cfg = _get_runtime_config(entry)
    alexa_en_target = cfg["alexa_en_target"]
    if not alexa_en_target:
        _LOGGER.warning("English Alexa target not configured — skipping")
        return
    try:
        await hass.services.async_call(
            "notify",
            "alexa_media",
            {
                "message": message,
                "target": [alexa_en_target],
                "data": {"type": "tts"},
            },
            blocking=True,
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
    verify_ssl: bool = DEFAULT_VERIFY_SSL,
) -> None:
    """Send WhatsApp messages via whatsmeow-bridge REST API (recipients in parallel)."""
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
        "Authorization": f"Bearer {bridge_token}",
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
    """Alert admins when the WhatsApp bridge is down."""
    cfg = _get_runtime_config(entry)
    for chat_id in cfg["bridge_alert_chat_ids"]:
        try:
            await hass.services.async_call(
                "telegram_bot",
                "send_message",
                {"chat_id": chat_id, "message": message},
                blocking=True,
            )
        except Exception as exc:  # noqa: BLE001
            _LOGGER.error(
                "Failed to send bridge alert to Telegram %s: %s", chat_id, exc
            )
