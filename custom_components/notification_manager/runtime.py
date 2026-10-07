"""Shared runtime helpers with no intra-package sibling imports (avoids cycles).

Imported by ``notify``, ``alexa``, ``messaging``, and ``bridge_services``.
Only ``.const`` is imported from this package — never ``notify`` / ``alexa`` /
``messaging`` / ``bridge_services``.
"""
from __future__ import annotations

import logging
from typing import Awaitable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    ALEXA_EN_TARGET as _CONST_ALEXA_EN_TARGET,
    ALEXA_LOCAL_PLAYERS as _CONST_ALEXA_LOCAL_PLAYERS,
    ALEXA_PLAYERS as _CONST_ALEXA_PLAYERS,
    ALEXA_POST_TTS_DELAY as _CONST_ALEXA_POST_TTS_DELAY,
    ALEXA_TTS_VOLUME as _CONST_ALEXA_TTS_VOLUME,
    BRIDGE_ALERT_CHAT_IDS as _CONST_BRIDGE_ALERT_CHAT_IDS,
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PHONE_DEFAULT_TARGETS as _CONST_PHONE_DEFAULT_TARGETS,
    PHONE_TARGETS as _CONST_PHONE_TARGETS,
    TELEGRAM_GROUPS as _CONST_TELEGRAM_GROUPS,
    WHATSAPP_CONTACTS as _CONST_WHATSAPP_CONTACTS,
)

_LOGGER = logging.getLogger(__name__)


async def _run_logged(coro: Awaitable, label: str) -> None:
    """Await a background coroutine and log (never raise) its failure."""
    try:
        await coro
    except Exception as exc:  # noqa: BLE001
        _LOGGER.error("%s failed: %s", label, exc)


def _entry_verify_ssl(entry: ConfigEntry) -> bool:
    """Return the configured TLS verification flag for the bridge."""
    return entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)


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
        "alexa_local_players": d.get("alexa_local_players") or list(_CONST_ALEXA_LOCAL_PLAYERS),
        "alexa_en_target": d.get("alexa_en_target") or _CONST_ALEXA_EN_TARGET,
        "alexa_tts_volume": (
            d.get("alexa_tts_volume")
            if d.get("alexa_tts_volume") is not None
            else _CONST_ALEXA_TTS_VOLUME
        ),
        "alexa_post_tts_delay": (
            d.get("alexa_post_tts_delay")
            if d.get("alexa_post_tts_delay") is not None
            else _CONST_ALEXA_POST_TTS_DELAY
        ),
        "bridge_alert_chat_ids": d.get("bridge_alert_chat_ids") or list(_CONST_BRIDGE_ALERT_CHAT_IDS),
        "telegram_groups": d.get("telegram_groups") or _CONST_TELEGRAM_GROUPS,
    }


def _iter_loaded_entry_ids(hass: HomeAssistant) -> list[str]:
    """Return config-entry ids currently stored under ``hass.data[DOMAIN]``."""
    return [
        key
        for key, value in hass.data.get(DOMAIN, {}).items()
        if not key.startswith("_") and isinstance(value, dict)
    ]


def _get_primary_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Return one loaded config entry for domain-level singleton services.

    Domain services (``notify``, bridge diagnostics, recent emissions) are
    registered once for the whole integration. They bind dynamically to the
    first still-loaded entry so unloading the entry that originally registered
    the service does not strand callers while another entry remains.
    """
    for entry_id in _iter_loaded_entry_ids(hass):
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry is not None:
            return entry
    return None
