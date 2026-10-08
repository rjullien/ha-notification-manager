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
    _LOGGING_debug = _LOGGER  # placemarker to be replaced
    _logger = _logger
