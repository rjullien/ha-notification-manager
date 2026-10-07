"""Notification Manager - Home Assistant Custom Component.

Replaces the ManageTTS YAML script with a proper Python integration.
Supports Alexa TTS, phone/Telegram notifications, and WhatsApp via
the whatsmeow-bridge REST API.

Module map (coarser split):
- ``notify`` — service handler + runtime config helpers
- ``alexa`` — FR/EN TTS, volume stack, emission helpers, resolvers
- ``messaging`` — phone / Telegram / WhatsApp (+ jid_utils)
- ``bridge_services`` — admin bridge logs/restart + recent emissions
"""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv

from .alexa import (
    DATA_ALEXA_EMISSIONS,
    DATA_ALEXA_LAST_GOOD_VOLUMES,
    DATA_ALEXA_LOCK,
    DATA_ALEXA_RESOLVER,
    DEFAULT_EMISSION_QUERY_SECONDS,
    MAX_EMISSION_QUERY_SECONDS,
    _async_send_alexa,
    _async_send_alexa_en,
    _async_send_alexa_en_delayed,
    _get_emission_log,
    _make_alexa_resolver,
    _resolve_alexa_targets,
    _resolve_restore_volume,
    _volumes_close,
)
from .alexa_emissions import AlexaEmissionLog
from .bridge_http import async_close_bridge_sessions
from .bridge_services import _async_require_admin, async_register_bridge_services
from .const import (
    ALEXA_EMISSION_LOG_SIZE,
    ALEXA_EMISSION_RETENTION_MINUTES,
    ALEXA_DEFAULT_KEYWORD,
    ALEXA_KEYWORD_ALIASES,
    ALEXA_KEYWORD_EXCLUDES,
    ALEXA_POST_TTS_DELAY as _CONST_ALEXA_POST_TTS_DELAY,
    ALEXA_TTS_VOLUME as _CONST_ALEXA_TTS_VOLUME,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    SERVICE_NOTIFY,
    SERVICE_RECENT_ALEXA_EMISSIONS,
)
from .messaging import (
    _async_call_telegram,
    _async_send_bridge_alert,
    _async_send_phone,
    _async_send_telegram_group,
    _async_send_whatsapp,
    _async_send_whatsapp_to_jid,
    _resolve_phone_targets,
    _resolve_whatsapp_targets,
)
from .notify import (
    _async_handle_notify,
    _entry_verify_ssl,
    _get_runtime_config,
    _run_logged,
)
from .watchdog import async_setup_watchdog, EntityWatchdog

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
        CONF_BRIDGE_URL: entry.data.get(CONF_BRIDGE_URL, ""),
        CONF_BRIDGE_TOKEN: entry.data.get(CONF_BRIDGE_TOKEN, ""),
        CONF_VERIFY_SSL: _entry_verify_ssl(entry),
    }
    # Serialises Alexa volume save→TTS→restore cycles so overlapping notify
    # calls can't capture the TTS volume as the "original" one.
    hass.data[DOMAIN].setdefault(DATA_ALEXA_LOCK, asyncio.Lock())
    hass.data[DOMAIN].setdefault(DATA_ALEXA_LAST_GOOD_VOLUMES, {})
    # In-memory only, shared by all entries: consumers ask "did a speaker in
    # this house just talk?" instead of re-implementing target resolution.
    hass.data[DOMAIN].setdefault(
        DATA_ALEXA_EMISSIONS,
        AlexaEmissionLog(
            max_entries=ALEXA_EMISSION_LOG_SIZE,
            retention=timedelta(minutes=ALEXA_EMISSION_RETENTION_MINUTES),
        ),
    )
    hass.data[DOMAIN][DATA_ALEXA_RESOLVER] = _make_alexa_resolver(hass, entry)

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

    async_register_bridge_services(hass, entry)

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
        hass.services.async_remove(DOMAIN, SERVICE_RECENT_ALEXA_EMISSIONS)
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


# Re-exports kept for tests and external consumers that historically imported
# symbols from ``notification_manager.__init__``. Prefer importing from the
# owning module (``alexa``, ``messaging``, ``notify``) for new code.
__all__ = [
    "DATA_ALEXA_EMISSIONS",
    "DATA_ALEXA_LAST_GOOD_VOLUMES",
    "DATA_ALEXA_LOCK",
    "DATA_ALEXA_RESOLVER",
    "DEFAULT_EMISSION_QUERY_SECONDS",
    "MAX_EMISSION_QUERY_SECONDS",
    "SERVICE_NOTIFY_SCHEMA",
    "async_setup_entry",
    "async_unload_entry",
    "_async_call_telegram",
    "_async_handle_notify",
    "_async_require_admin",
    "_async_send_alexa",
    "_async_send_alexa_en",
    "_async_send_alexa_en_delayed",
    "_async_send_bridge_alert",
    "_async_send_phone",
    "_async_send_telegram_group",
    "_async_send_whatsapp",
    "_async_send_whatsapp_to_jid",
    "_entry_verify_ssl",
    "_get_emission_log",
    "_get_runtime_config",
    "_make_alexa_resolver",
    "_resolve_alexa_targets",
    "_resolve_phone_targets",
    "_resolve_restore_volume",
    "_resolve_whatsapp_targets",
    "_run_logged",
    "_volumes_close",
    "ALEXA_DEFAULT_KEYWORD",
    "ALEXA_KEYWORD_ALIASES",
    "ALEXA_KEYWORD_EXCLUDES",
    "_CONST_ALEXA_POST_TTS_DELAY",
    "_CONST_ALEXA_TTS_VOLUME",
]
