"""Coordinator for Notification Manager — polls the WhatsApp bridge health."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from urllib.parse import urlparse

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

# Tailscale DNS override: hostname → IP
# HA OS containers can't resolve MagicDNS names
_TAILSCALE_DNS_OVERRIDES: dict[str, str] = {}

try:
    from .const import TAILSCALE_DNS_OVERRIDES
    _TAILSCALE_DNS_OVERRIDES = TAILSCALE_DNS_OVERRIDES
except (ImportError, AttributeError):
    pass

_LOGGER = logging.getLogger(__name__)


class NotificationManagerCoordinator(DataUpdateCoordinator[str]):
    """Polls the WhatsApp bridge /health endpoint every 5 minutes."""

    def __init__(self, hass: HomeAssistant, entry_data: dict) -> None:
        """Initialise the coordinator."""
        from .const import DEFAULT_BRIDGE_URL
        self._bridge_url: str = entry_data.get(CONF_BRIDGE_URL, "") or DEFAULT_BRIDGE_URL
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
        headers = {"Authorization": f"Bearer {self._bridge_token}"}

        # Check if we need DNS override for Tailscale hostnames
        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        override_ip = _TAILSCALE_DNS_OVERRIDES.get(hostname)

        try:
            if override_ip:
                # HA OS containers can't resolve Tailscale MagicDNS.
                # Write to /etc/hosts at runtime (idempotent) so the
                # default resolver works with the correct hostname for SNI.
                import os
                hosts_line = f"{override_ip} {hostname}"
                try:
                    with open("/etc/hosts", "r") as f:
                        hosts_content = f.read()
                    if hostname not in hosts_content:
                        with open("/etc/hosts", "a") as f:
                            f.write(f"\n{hosts_line}\n")
                        _LOGGER.info("Added %s to /etc/hosts", hosts_line)
                except (PermissionError, OSError) as err:
                    _LOGGER.debug("Cannot write /etc/hosts: %s", err)

                # Now use the normal HA session (hostname resolves via /etc/hosts)
                session = async_get_clientsession(self.hass, verify_ssl=False)
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
            else:
                session = async_get_clientsession(self.hass, verify_ssl=False)
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
