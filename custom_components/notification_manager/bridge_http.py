"""HTTP helpers for talking to the WhatsApp bridge.

Centralises client-session handling so that:
- TLS verification is configurable (CONF_VERIFY_SSL) instead of hardcoded off;
- Tailscale MagicDNS hostnames are resolved in-process via a custom aiohttp
  resolver — the system /etc/hosts is never modified (no blocking file I/O,
  no stale entries when the Tailscale IP changes).
"""
from __future__ import annotations

import socket
from typing import Any

import aiohttp
from aiohttp.resolver import DefaultResolver

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import const as _const
from .const import DOMAIN

DATA_BRIDGE_SESSIONS = "_bridge_sessions"


def _dns_overrides() -> dict[str, str]:
    """Return the configured hostname → IP overrides (may be empty)."""
    overrides = getattr(_const, "TAILSCALE_DNS_OVERRIDES", {}) or {}
    return overrides if isinstance(overrides, dict) else {}


class _OverrideResolver(aiohttp.abc.AbstractResolver):
    """Resolver that maps configured hostnames to static IPs.

    Falls back to the default resolver for any other hostname. Resolving
    the override in-process keeps the real hostname for SNI/Host headers,
    unlike rewriting the URL to an IP.
    """

    def __init__(self, overrides: dict[str, str]) -> None:
        self._overrides = overrides
        self._fallback = DefaultResolver()

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict[str, Any]]:
        ip = self._overrides.get(host)
        if ip:
            return [
                {
                    "hostname": host,
                    "host": ip,
                    "port": port,
                    "family": socket.AF_INET,
                    "proto": 0,
                    "flags": socket.AI_NUMERICHOST,
                }
            ]
        return await self._fallback.resolve(host, port, family)

    async def close(self) -> None:
        await self._fallback.close()


@callback
def async_get_bridge_session(
    hass: HomeAssistant, verify_ssl: bool
) -> aiohttp.ClientSession:
    """Return the client session to use for bridge requests.

    Without DNS overrides this is Home Assistant's shared session.
    With overrides, a dedicated cached session with the custom resolver
    is created (one per verify_ssl value) and closed on unload via
    async_close_bridge_sessions.
    """
    overrides = _dns_overrides()
    if not overrides:
        return async_get_clientsession(hass, verify_ssl=verify_ssl)

    sessions: dict[str, aiohttp.ClientSession] = hass.data.setdefault(
        DOMAIN, {}
    ).setdefault(DATA_BRIDGE_SESSIONS, {})

    key = f"override_verify_{verify_ssl}"
    session = sessions.get(key)
    if session is None or session.closed:
        connector = aiohttp.TCPConnector(
            resolver=_OverrideResolver(overrides),
            ssl=verify_ssl,  # True → default verification, False → disabled
        )
        session = aiohttp.ClientSession(connector=connector)
        sessions[key] = session
    return session


async def async_close_bridge_sessions(hass: HomeAssistant) -> None:
    """Close any dedicated bridge sessions (called on unload)."""
    sessions = hass.data.get(DOMAIN, {}).pop(DATA_BRIDGE_SESSIONS, None) or {}
    for session in sessions.values():
        if not session.closed:
            await session.close()
