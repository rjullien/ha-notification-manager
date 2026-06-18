"""Entity Unavailable Watchdog for Notification Manager.

Periodically checks a list of monitored entities. If any entity remains
'unavailable' for longer than the configured threshold, sends a Telegram
notification to the admin.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    BRIDGE_ALERT_CHAT_IDS,
    DOMAIN,
    WATCHDOG_CHECK_INTERVAL_MINUTES,
    WATCHDOG_ENTITIES,
    WATCHDOG_THRESHOLD_MINUTES,
    WATCHDOG_COOLDOWN_HOURS,
    WATCHDOG_TELEGRAM_CHAT_IDS,
)

_LOGGER = logging.getLogger(__name__)

DATA_WATCHDOG = "entity_watchdog"


class EntityWatchdog:
    """Monitors entities and alerts when they stay unavailable too long."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialise the watchdog."""
        self._hass = hass
        self._entry = entry
        # Track when each entity first became unavailable
        self._unavailable_since: dict[str, datetime] = {}
        # Track last alert time per entity (cooldown)
        self._last_alerted: dict[str, datetime] = {}
        self._unsub: Any = None

    def start(self) -> None:
        """Start periodic checking."""
        self._unsub = async_track_time_interval(
            self._hass,
            self._async_check,
            timedelta(minutes=WATCHDOG_CHECK_INTERVAL_MINUTES),
        )
        _LOGGER.debug(
            "Entity watchdog started — monitoring %d entities, "
            "threshold %d min, interval %d min",
            len(WATCHDOG_ENTITIES),
            WATCHDOG_THRESHOLD_MINUTES,
            WATCHDOG_CHECK_INTERVAL_MINUTES,
        )

    def stop(self) -> None:
        """Stop periodic checking."""
        if self._unsub:
            self._unsub()
            self._unsub = None
            _LOGGER.debug("Entity watchdog stopped")

    @callback
    async def _async_check(self, _now: datetime | None = None) -> None:
        """Check all monitored entities."""
        if not WATCHDOG_ENTITIES:
            return

        now = datetime.now(timezone.utc)
        threshold = timedelta(minutes=WATCHDOG_THRESHOLD_MINUTES)
        cooldown = timedelta(hours=WATCHDOG_COOLDOWN_HOURS)
        alerts: list[str] = []

        for entity_id in WATCHDOG_ENTITIES:
            state = self._hass.states.get(entity_id)

            if state is None or state.state in ("unavailable", "unknown"):
                # Entity is currently unavailable
                if entity_id not in self._unavailable_since:
                    self._unavailable_since[entity_id] = now
                    _LOGGER.debug(
                        "Watchdog: %s became unavailable at %s", entity_id, now
                    )

                elapsed = now - self._unavailable_since[entity_id]
                if elapsed >= threshold:
                    # Check cooldown
                    last_alert = self._last_alerted.get(entity_id)
                    if last_alert is None or (now - last_alert) >= cooldown:
                        friendly = (
                            state.attributes.get("friendly_name", entity_id)
                            if state
                            else entity_id
                        )
                        minutes = int(elapsed.total_seconds() // 60)
                        alerts.append(
                            f"• {friendly} (`{entity_id}`) — unavailable depuis {minutes} min"
                        )
                        self._last_alerted[entity_id] = now
            else:
                # Entity is back — clear tracking
                if entity_id in self._unavailable_since:
                    _LOGGER.debug("Watchdog: %s recovered", entity_id)
                    del self._unavailable_since[entity_id]
                    # Reset cooldown on recovery so next unavailable is alerted promptly
                    self._last_alerted.pop(entity_id, None)

        if alerts:
            message = (
                "⚠️ *Entity Watchdog — Entités unavailable*\n\n"
                + "\n".join(alerts)
                + "\n\n_Vérifier l'intégration ou l'appareil._"
            )
            await self._async_send_alert(message)

    async def _async_send_alert(self, message: str) -> None:
        """Send alert via Telegram."""
        chat_ids = WATCHDOG_TELEGRAM_CHAT_IDS or BRIDGE_ALERT_CHAT_IDS
        for chat_id in chat_ids:
            try:
                await self._hass.services.async_call(
                    "telegram_bot",
                    "send_message",
                    {
                        "chat_id": chat_id,
                        "message": message,
                        "parse_mode": "markdown",
                    },
                    blocking=True,
                )
                _LOGGER.debug("Watchdog alert sent to chat_id %s", chat_id)
            except Exception as exc:  # noqa: BLE001
                _LOGGER.error(
                    "Failed to send watchdog alert to chat_id %s: %s", chat_id, exc
                )


def async_setup_watchdog(hass: HomeAssistant, entry: ConfigEntry) -> EntityWatchdog:
    """Create and start the entity watchdog."""
    watchdog = EntityWatchdog(hass, entry)
    watchdog.start()
    return watchdog
