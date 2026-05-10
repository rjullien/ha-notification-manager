"""Coordinator for Notification Manager — polls the WhatsApp bridge health."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    BRIDGE_HEALTH_ENDPOINT,
    BRIDGE_TIMEOUT,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    DOMAIN,
    SENSOR_POLL_INTERVAL_MINUTES,
    SENSOR_STATE_CONNECTED,
    SENSOR_STATE_DISCONNECTED,
    SENSOR_STATE_UNKNOWN,
)

_LOGGER = logging.getLogger(__name__)


class NotificationManagerCoordinator(DataUpdateCoordinator[str]):
    """Polls the WhatsApp bridge /health endpoint every 5 minutes."""

    def __init__(self, hass: HomeAssistant, entry_data: dict) -> None:
        """Initialise the coordinator."""
        self._bridge_url: str = entry_data.get(CONF_BRIDGE_URL, "")
        self._bridge_token: str = entry_data.get(CONF_BRIDGE_TOKEN, "")

        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_whatsapp_status",
            update_interval=timedelta(minutes=SENSOR_POLL_INTERVAL_MINUTES),
        )

    @property
    def bridge_url(self) -> str:
        """Return the bridge URL (public accessor)."""
        return self._bridge_url

    async def _async_update_data(self) -> str:
        """Fetch bridge health status."""
        if not self._bridge_url:
            return SENSOR_STATE_UNKNOWN

        url = self._bridge_url.rstrip("/") + BRIDGE_HEALTH_ENDPOINT
        session = async_get_clientsession(self.hass, verify_ssl=False)
        headers = {"Authorization": f"Bearer {self._bridge_token}"}

        try:
            async with session.get(
                url,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=BRIDGE_TIMEOUT),
            ) as resp:
                if resp.status == 200:
                    return SENSOR_STATE_CONNECTED
                _LOGGER.debug(
                    "Bridge health returned HTTP %d → disconnected", resp.status
                )
                return SENSOR_STATE_DISCONNECTED
        except aiohttp.ClientConnectorError:
            _LOGGER.debug("Bridge unreachable → disconnected")
            return SENSOR_STATE_DISCONNECTED
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"Unexpected error polling bridge health: {exc}") from exc

    def update_config(self, bridge_url: str, bridge_token: str) -> None:
        """Update bridge credentials (called when options change)."""
        self._bridge_url = bridge_url
        self._bridge_token = bridge_token

    async def async_shutdown(self) -> None:
        """Cancel polling on unload."""
        if self._unsub_refresh:
            self._unsub_refresh()
            self._unsub_refresh = None
