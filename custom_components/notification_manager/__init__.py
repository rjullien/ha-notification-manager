"""Notification Manager - Home Assistant Custom Component.

Replaces the ManageTTS YAML script with a proper Python integration.
Supports Alexa TTS, phone/Telegram notifications, and WhatsApp via
the whatsmeow-bridge REST API.

Module map (coarser split):
- ``runtime`` — shared helpers (no sibling imports; breaks import cycles)
- ``notify`` — service handler
- ``alexa`` — FR/EN TTS, volume stack, emission helpers, resolvers
- ``messaging`` — phone / Telegram / WhatsApp (+ jid_utils)
- ``bridge_services`` — admin bridge logs/restart + recent emissions

Multi-entry notes
-----------------
Several config entries may load. Domain services are registered once
(``has_service`` guard) and bind dynamically to a primary loaded entry.
Per-entry state (resolver, watchdog, coordinator, bridge credentials in
``entry.data``) lives under ``hass.data[DOMAIN][entry.entry_id]``.

Shared across all entries (intentional):
- ``DATA_ALEXA_LOCK`` — one volume save/restore lock for the house
- ``DATA_ALEXA_EMISSIONS`` — one in-memory emission log
- ``DATA_ALEXA_LAST_GOOD_VOLUMES`` — resting-volume cache per media_player
- ``DATA_ALEXA_GENERATIONS`` / ``DATA_ALEXA_RERESTORE_UNSUBS`` — deferred
  volume recovery jobs, retained across entry reloads
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import timedelta
from functools import partial

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STARTED
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .alexa import (
    DATA_ALEXA_EMISSIONS,
    DATA_ALEXA_GENERATIONS,
    DATA_ALEXA_LAST_GOOD_VOLUMES,
    DATA_ALEXA_LOCK,
    DATA_ALEXA_RERESTORE_UNSUBS,
    DATA_ALEXA_STORE,
    DATA_ALEXA_RESOLVER,
    DEFAULT_EMISSION_QUERY_SECONDS,
    MAX_EMISSION_QUERY_SECONDS,
    _async_boot_recovery,
    _async_restore_saved_volumes,
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
    ALEXA_BOOT_RECOVERY_DELAY,
    ALEXA_VOLUME_SAVE_DELAY,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    SERVICE_NOTIFY,
    SERVICE_RECENT_ALEXA_EMISSIONS,
    SERVICE_RESTORE_ALEXA_VOLUMES,
    SENSOR_BRIDGE_STATUS_KEY,
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
from .notify import _async_handle_notify
from .runtime import (
    _entry_bridge_url,
    _entry_verify_ssl,
    _get_primary_entry,
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
    domain_data = hass.data[DOMAIN]
    first_setup = DATA_ALEXA_STORE not in domain_data
    bridge_url = _entry_bridge_url(entry)
    platforms = PLATFORMS if bridge_url else []
    domain_data[entry.entry_id] = {
        CONF_BRIDGE_URL: bridge_url,
        CONF_BRIDGE_TOKEN: entry.data.get(CONF_BRIDGE_TOKEN, ""),
        CONF_VERIFY_SSL: _entry_verify_ssl(entry),
        "platforms": platforms,
        # Per-entry resolver — do not store a single domain-level resolver that
        # the last-loaded entry would clobber.
        DATA_ALEXA_RESOLVER: _make_alexa_resolver(hass, entry),
    }
    # Serialises Alexa volume save→TTS→restore cycles so overlapping notify
    # calls can't capture the TTS volume as the "original" one.
    # Shared across entries: one lock / emission log / volume cache for the house.
    domain_data.setdefault(DATA_ALEXA_LOCK, asyncio.Lock())
    last_good: dict[str, float] = domain_data.setdefault(
        DATA_ALEXA_LAST_GOOD_VOLUMES, {}
    )
    domain_data.setdefault(DATA_ALEXA_GENERATIONS, {})
    domain_data.setdefault(DATA_ALEXA_RERESTORE_UNSUBS, {})
    # In-memory only, shared by all entries: consumers ask "did a speaker in
    # this house just talk?" instead of re-implementing target resolution.
    domain_data.setdefault(
        DATA_ALEXA_EMISSIONS,
        AlexaEmissionLog(
            max_entries=ALEXA_EMISSION_LOG_SIZE,
            retention=timedelta(minutes=ALEXA_EMISSION_RETENTION_MINUTES),
        ),
    )

    alexa_enabled = bool(_get_runtime_config(entry)["alexa_players"])
    if first_setup and alexa_enabled:
        store = Store(hass, 1, "notification_manager.alexa_volumes")
        domain_data[DATA_ALEXA_STORE] = store
        try:
            stored = await store.async_load()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to load stored Alexa volumes: %s", exc)
            stored = None

        stored_volumes = (
            stored.get("last_good", {}) if isinstance(stored, dict) else {}
        )
        allowed_players = set(_get_runtime_config(entry)["alexa_players"])
        if isinstance(stored_volumes, dict):
            for entity_id, value in stored_volumes.items():
                if (
                    not isinstance(entity_id, str)
                    or entity_id not in allowed_players
                    or isinstance(value, bool)
                    or not isinstance(value, (int, float))
                ):
                    continue
                try:
                    volume = float(value)
                except (OverflowError, ValueError):
                    continue
                if not math.isfinite(volume) or not 0 <= volume <= 1:
                    continue
                last_good.setdefault(entity_id, volume)

        if not hass.is_running:
            def schedule_boot_recovery(_event) -> None:
                cancel_timer = async_call_later(
                    hass,
                    ALEXA_BOOT_RECOVERY_DELAY,
                    partial(_async_boot_recovery, hass, entry),
                )
                entry.async_on_unload(cancel_timer)

            unsubscribe = hass.bus.async_listen_once(
                EVENT_HOMEASSISTANT_STARTED,
                schedule_boot_recovery,
            )
            entry.async_on_unload(unsubscribe)
    if platforms:
        await hass.config_entries.async_forward_entry_setups(entry, platforms)
    else:
        registry = er.async_get(hass)
        for sensor_key in (SENSOR_BRIDGE_STATUS_KEY,):
            unique_id = f"{entry.entry_id}_{sensor_key}"
            entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
            if entity_id:
                registry.async_remove(entity_id)
                _LOGGER.info("Removed stale WhatsApp bridge status sensor %s", entity_id)

    # Reload the entry whenever its config changes (options flow or reconfigure
    # flow). Without this, the coordinator keeps polling the old bridge URL/token
    # until Home Assistant restarts.
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    # Domain-level notify: register once; handler binds to a live primary entry.
    if not hass.services.has_service(DOMAIN, SERVICE_NOTIFY):
        async def handle_notify(call: ServiceCall) -> None:
            active = _get_primary_entry(hass)
            if active is None:
                _LOGGER.error("notify called but no config entry is loaded")
                return
            await _async_handle_notify(hass, active, call)

        hass.services.async_register(
            DOMAIN,
            SERVICE_NOTIFY,
            handle_notify,
            schema=SERVICE_NOTIFY_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_RESTORE_ALEXA_VOLUMES):
        async def handle_restore_alexa_volumes(call: ServiceCall) -> None:
            """Restore each available Alexa to its last-known resting volume."""
            await _async_require_admin(hass, call)
            domain_data = hass.data.get(DOMAIN, {})
            if domain_data.get(DATA_ALEXA_STORE) is None:
                return

            alexa_configured = False
            for entry_id in domain_data:
                if entry_id.startswith("_"):
                    continue
                active = hass.config_entries.async_get_entry(entry_id)
                if active and _get_runtime_config(active)["alexa_players"]:
                    alexa_configured = True
                    break
            if not alexa_configured:
                return
            await _async_restore_saved_volumes(hass)

        hass.services.async_register(
            DOMAIN,
            SERVICE_RESTORE_ALEXA_VOLUMES,
            handle_restore_alexa_volumes,
            schema=vol.Schema({}),
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

    platforms = entry_data.get("platforms", PLATFORMS)
    unload_ok = (
        await hass.config_entries.async_unload_platforms(entry, platforms)
        if platforms
        else True
    )
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)

    # Only remove services when the last entry is removed (internal keys
    # such as the Alexa lock or cached sessions start with "_").
    remaining_entries = [k for k in hass.data.get(DOMAIN, {}) if not k.startswith("_")]
    if not remaining_entries:
        hass.services.async_remove(DOMAIN, SERVICE_NOTIFY)
        hass.services.async_remove(DOMAIN, SERVICE_RECENT_ALEXA_EMISSIONS)
        hass.services.async_remove(DOMAIN, SERVICE_RESTORE_ALEXA_VOLUMES)
        hass.services.async_remove(DOMAIN, "whatsapp_bridge_logs")
        hass.services.async_remove(DOMAIN, "whatsapp_bridge_restart")
        store = hass.data[DOMAIN].get(DATA_ALEXA_STORE)
        if store is not None:
            try:
                await store.async_save(
                    {
                        "last_good": dict(
                            hass.data[DOMAIN].get(DATA_ALEXA_LAST_GOOD_VOLUMES, {})
                        )
                    }
                )
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error("Failed to save Alexa resting volumes: %s", exc)
        await async_close_bridge_sessions(hass)
        for cancel in hass.data[DOMAIN].get(DATA_ALEXA_RERESTORE_UNSUBS, {}).values():
            cancel()
        hass.data[DOMAIN].get(DATA_ALEXA_RERESTORE_UNSUBS, {}).clear()

    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when its data changes.

    Triggered by the options flow and the reconfigure flow so the coordinator
    picks up the new bridge URL/token without requiring a HA restart.
    """
    await hass.config_entries.async_reload(entry.entry_id)


# Re-exports kept for tests and external consumers that historically imported
# symbols from ``notification_manager.__init__``. Prefer importing from the
# owning module (``alexa``, ``messaging``, ``runtime``, ``notify``) for new code.
__all__ = [
    "DATA_ALEXA_EMISSIONS",
    "DATA_ALEXA_GENERATIONS",
    "DATA_ALEXA_LAST_GOOD_VOLUMES",
    "DATA_ALEXA_LOCK",
    "DATA_ALEXA_RERESTORE_UNSUBS",
    "DATA_ALEXA_STORE",
    "DATA_ALEXA_RESOLVER",
    "DEFAULT_EMISSION_QUERY_SECONDS",
    "MAX_EMISSION_QUERY_SECONDS",
    "SERVICE_NOTIFY_SCHEMA",
    "SERVICE_RESTORE_ALEXA_VOLUMES",
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
    "_get_primary_entry",
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
