"""Coordinator for Notification Manager — polls the WhatsApp bridge health."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .bridge_http import async_get_bridge_session
from .const import (
    BRIDGE_HEALTH_ENDPOINT,
    BRIDGE_TIMEOUT,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_VERIFY_SSL,
    DEFAULT_BRIDGE_URL,
    DEFAULT_VERIFY_SSL,
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
        self._bridge_url: str = entry_data.get(CONF_BRIDGE_URL, "") or DEFAULT_BRIDGE_URL
        self._bridge_token: str = entry_data.get(CONF_BRIDGE_TOKEN, "")
        self._verify_ssl: bool = entry_data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

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
        headers = {"Authorization": f"Bearer {self._bridge_token}"}

        # Tailscale MagicDNS hostnames are handled by the session's custom
        # resolver (bridge_http.py) — no /etc/hosts manipulation needed.
        session = async_get_bridge_session(self.hass, self._verify_ssl)

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
        except (aiohttp.ClientConnectorError, TimeoutError):
            _LOGGER.debug("Bridge unreachable → disconnected")
            return SENSOR_STATE_DISCONNECTED
        except Exception as exc:  # noqa: BLE001
            raise UpdateFailed(f"Unexpected error polling bridge health: {exc}") from exc

    def update_config(
        self, bridge_url: str, bridge_token: str, verify_ssl: bool | None = None
    ) -> None:
        """Update bridge credentials (called when options change)."""
        self._bridge_url = bridge_url
        self._bridge_token = bridge_token
        if verify_ssl is not None:
            self._verify_ssl = verify_ssl

    async def async_shutdown(self) -> None:
        """Cancel polling on unload."""
        if hasattr(super(), "async_shutdown"):
            await super().async_shutdown()
        elif self._unsub_refresh:  # pragma: no cover — very old HA fallback
            self._unsub_refresh()
            self._unsub_refresh = None
