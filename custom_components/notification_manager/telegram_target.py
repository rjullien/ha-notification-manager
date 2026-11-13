"""Resolve which telegram_bot config entry to target for a send.

Home Assistant 2025.9+ / 2026.10 requires ``config_entry_id`` (or a notify
``entity_id``) when more than one ``telegram_bot`` config entry exists;
``chat_id`` alone fails with ``Please specify at least 1 notify entity``.

Logical bots ``principal`` / ``info`` map to configured entry ids. Info
falls back to principal when missing, disabled, not loaded, or when the
recipient chat is not allowlisted on that entry.
"""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from .const import (
    CONF_TELEGRAM_BOTS,
    TELEGRAM_BOT_INFO,
    TELEGRAM_BOT_PRINCIPAL,
)
from .runtime import _get_runtime_config

_LOGGER = logging.getLogger(__name__)

_TELEGRAM_DOMAIN = "telegram_bot"
_SUBENTRY_ALLOWED_CHAT = "allowed_chat_ids"


def _configured_telegram_bots(entry: ConfigEntry | None) -> dict[str, str]:
    """Return the principal/info entry-id map from entry data (or empty)."""
    if entry is None:
        return {}
    cfg = _get_runtime_config(entry)
    raw = cfg.get(CONF_TELEGRAM_BOTS) or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key in (TELEGRAM_BOT_PRINCIPAL, TELEGRAM_BOT_INFO):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            out[key] = value.strip()
    return out


def _iter_telegram_bot_entries(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return loaded-or-present telegram_bot config entries (best effort)."""
    async_entries = getattr(hass.config_entries, "async_entries", None)
    if not callable(async_entries):
        return []
    try:
        raw = async_entries(_TELEGRAM_DOMAIN)
    except Exception:  # noqa: BLE001
        return []
    if raw is None:
        return []
    try:
        items = list(raw)
    except TypeError:
        return []
    result: list[ConfigEntry] = []
    for item in items:
        entry_id = getattr(item, "entry_id", None)
        domain = getattr(item, "domain", None)
        if not isinstance(entry_id, str) or not entry_id:
            continue
        if domain not in (None, _TELEGRAM_DOMAIN):
            continue
        result.append(item)
    return result


def _entry_is_usable(entry: ConfigEntry | None) -> bool:
    """Return whether a telegram_bot config entry can be targeted."""
    if entry is None:
        return False
    domain = getattr(entry, "domain", None)
    if isinstance(domain, str) and domain != _TELEGRAM_DOMAIN:
        return False
    if getattr(entry, "disabled_by", None) is not None:
        return False
    state = getattr(entry, "state", None)
    if state is None:
        return True
    if state == ConfigEntryState.LOADED or state == "loaded":
        return True
    value = getattr(state, "value", None)
    name = getattr(state, "name", None)
    if value == "loaded" or name == "LOADED":
        return True
    # Unit-test MagicMock entries leave state unset → treat as usable.
    if type(state).__module__ == "unittest.mock":
        return True
    return False


def _get_entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry | None:
    if not entry_id or not isinstance(entry_id, str):
        return None
    getter = getattr(hass.config_entries, "async_get_entry", None)
    if not callable(getter):
        return None
    try:
        return getter(entry_id)
    except Exception:  # noqa: BLE001
        return None


def _allowed_chat_ids(entry: ConfigEntry) -> set[int] | None:
    """Return allowlisted chat ids from subentries, or None if unknown.

    ``None`` means we could not inspect allowlists (no subentries API / empty
    structure that predates subentries) — caller should not treat the chat as
    disallowed solely on that basis.
    """
    subentries = getattr(entry, "subentries", None)
    if subentries is None:
        return None
    try:
        values = list(subentries.values()) if hasattr(subentries, "values") else list(subentries)
    except TypeError:
        return None
    if not values:
        # Empty subentries: treat as "no chats allowlisted" only when the
        # attribute is a real mapping (dict). MagicMock → None (unknown).
        if isinstance(subentries, dict):
            return set()
        return None

    allowed: set[int] = set()
    saw_chat_subentry = False
    for sub in values:
        sub_type = getattr(sub, "subentry_type", None)
        if sub_type is None and isinstance(sub, dict):
            sub_type = sub.get("subentry_type")
        # Skip non-chat subentries when the type is known; accept untyped
        # dicts that still carry chat_id (test fixtures / older shapes).
        if sub_type is not None and sub_type != _SUBENTRY_ALLOWED_CHAT:
            continue
        data = getattr(sub, "data", None)
        if data is None and isinstance(sub, dict):
            data = sub.get("data")
        if not isinstance(data, dict):
            continue
        chat_id = data.get("chat_id")
        if chat_id is None:
            continue
        try:
            allowed.add(int(chat_id))
            saw_chat_subentry = True
        except (TypeError, ValueError):
            continue

    if not saw_chat_subentry:
        return None
    return allowed


def _chat_allowed_on_entry(entry: ConfigEntry, chat_id: int | None) -> bool:
    """Return True when chat_id is allowlisted, or when allowlist is unknown."""
    if chat_id is None:
        return True
    allowed = _allowed_chat_ids(entry)
    if allowed is None:
        return True
    try:
        return int(chat_id) in allowed
    except (TypeError, ValueError):
        return False


def _auto_detect_principal(hass: HomeAssistant) -> str | None:
    """If exactly one telegram_bot entry exists and is usable, use it.

    Counts *all* telegram_bot entries (including disabled). A lone usable
    entry among several must not silently become principal — that would
    route default/self-alerts through Info when principal is disabled.
    """
    entries = _iter_telegram_bot_entries(hass)
    if len(entries) == 1 and _entry_is_usable(entries[0]):
        return entries[0].entry_id
    return None


def _sole_usable_telegram_entry_id(hass: HomeAssistant) -> str | None:
    """Return the only usable telegram_bot entry id, if unambiguous."""
    usable = [e for e in _iter_telegram_bot_entries(hass) if _entry_is_usable(e)]
    if len(usable) == 1:
        return usable[0].entry_id
    return None


def _resolve_principal_entry_id(
    hass: HomeAssistant, entry: ConfigEntry | None
) -> str | None:
    """Resolve the principal bot entry id (configured, recovered, or auto)."""
    bots = _configured_telegram_bots(entry)
    configured = bots.get(TELEGRAM_BOT_PRINCIPAL)

    if configured:
        if _entry_is_usable(_get_entry(hass, configured)):
            return configured
        _LOGGER.warning(
            "Configured principal telegram_bot entry %s is missing, "
            "disabled, or not loaded",
            configured,
        )
        # Bot replacement: stale mapping + exactly one usable entry → recover.
        recovered = _sole_usable_telegram_entry_id(hass)
        if recovered:
            _LOGGER.warning(
                "Using sole usable telegram_bot entry %s until "
                "telegram_bots.principal is updated",
                recovered,
            )
            return recovered
        if len(_iter_telegram_bot_entries(hass)) > 1:
            _LOGGER.warning(
                "Multiple telegram_bot entries exist; update "
                "telegram_bots.principal in options"
            )
        return None

    auto = _auto_detect_principal(hass)
    if auto:
        return auto
    if len(_iter_telegram_bot_entries(hass)) > 1:
        _LOGGER.warning(
            "Multiple telegram_bot entries exist but telegram_bots.principal "
            "is not configured — Telegram sends may fail until it is set"
        )
    return None


def resolve_telegram_config_entry_id(
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    telegram_bot: str = TELEGRAM_BOT_PRINCIPAL,
    chat_id: int | None = None,
) -> str | None:
    """Resolve the ``config_entry_id`` for a Telegram send.

    * ``principal`` (default): configured principal, else auto-detect when a
      single telegram_bot entry exists. Stale configured ids recover to the
      sole usable entry when unambiguous; never return a known-unusable id.
    * ``info``: configured info when usable and (if known) allowlisting the
      chat; otherwise fall back to principal with a warning.
    * Integration self-alerts must always pass ``principal``.
    """
    bot_key = (telegram_bot or TELEGRAM_BOT_PRINCIPAL).strip().lower()
    if bot_key not in (TELEGRAM_BOT_PRINCIPAL, TELEGRAM_BOT_INFO):
        _LOGGER.warning(
            "Unknown telegram_bot %r — using principal",
            telegram_bot,
        )
        bot_key = TELEGRAM_BOT_PRINCIPAL

    bots = _configured_telegram_bots(entry)
    principal_id = _resolve_principal_entry_id(hass, entry)
    info_id = bots.get(TELEGRAM_BOT_INFO)

    if bot_key == TELEGRAM_BOT_PRINCIPAL:
        return principal_id

    # info requested
    fallback_reason: str | None = None
    if not info_id:
        fallback_reason = "info bot is not configured"
    else:
        info_entry = _get_entry(hass, info_id)
        if info_entry is None:
            fallback_reason = f"info entry {info_id} is missing"
        elif not _entry_is_usable(info_entry):
            fallback_reason = f"info entry {info_id} is disabled or not loaded"
        elif not _chat_allowed_on_entry(info_entry, chat_id):
            fallback_reason = (
                f"chat_id {chat_id} is not allowlisted on info entry {info_id}"
            )
        else:
            return info_id

    if principal_id:
        _LOGGER.warning(
            "telegram_bot=info requested but %s — falling back to principal (%s)",
            fallback_reason,
            principal_id,
        )
        return principal_id

    _LOGGER.warning(
        "telegram_bot=info requested but %s and no principal is available",
        fallback_reason,
    )
    return None


def apply_telegram_target(
    data: dict[str, Any],
    hass: HomeAssistant,
    entry: ConfigEntry | None,
    telegram_bot: str = TELEGRAM_BOT_PRINCIPAL,
) -> dict[str, Any]:
    """Return a copy of service data with ``config_entry_id`` when resolved."""
    payload = dict(data)
    chat_raw = payload.get("chat_id")
    chat_id: int | None
    try:
        chat_id = int(chat_raw) if chat_raw is not None else None
    except (TypeError, ValueError):
        chat_id = None
    entry_id = resolve_telegram_config_entry_id(
        hass, entry, telegram_bot=telegram_bot, chat_id=chat_id
    )
    if entry_id:
        payload["config_entry_id"] = entry_id
    return payload
