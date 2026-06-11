"""Tests for bridge_http.py — DNS override resolver and session management."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    from notification_manager import bridge_http
    from notification_manager.bridge_http import (
        DATA_BRIDGE_SESSIONS,
        _OverrideResolver,
        async_close_bridge_sessions,
        async_get_bridge_session,
    )
    from notification_manager.const import DOMAIN


class TestOverrideResolver:
    """Test the in-process Tailscale DNS override resolver."""

    async def test_override_returns_mapped_ip(self):
        """An overridden hostname resolves to the configured IP."""
        resolver = _OverrideResolver({"bridge.tailnet.ts.net": "100.64.0.10"})
        result = await resolver.resolve("bridge.tailnet.ts.net", 8080)

        assert len(result) == 1
        assert result[0]["host"] == "100.64.0.10"
        # Hostname is preserved → SNI / Host header stay correct
        assert result[0]["hostname"] == "bridge.tailnet.ts.net"
        assert result[0]["port"] == 8080

    async def test_non_overridden_host_uses_fallback(self):
        """Hostnames not in the override map go to the default resolver."""
        resolver = _OverrideResolver({"bridge.tailnet.ts.net": "100.64.0.10"})
        resolver._fallback = AsyncMock()
        resolver._fallback.resolve = AsyncMock(
            return_value=[{"host": "93.184.216.34", "hostname": "example.com"}]
        )

        result = await resolver.resolve("example.com", 443)

        resolver._fallback.resolve.assert_awaited_once()
        assert result[0]["host"] == "93.184.216.34"

    async def test_close_closes_fallback(self):
        """close() propagates to the fallback resolver."""
        resolver = _OverrideResolver({})
        resolver._fallback = AsyncMock()
        await resolver.close()
        resolver._fallback.close.assert_awaited_once()


class TestBridgeSession:
    """Test session selection, caching and cleanup."""

    def test_no_overrides_uses_shared_session(self):
        """Without DNS overrides, HA's shared session is used (verify_ssl forwarded)."""
        hass = MagicMock()
        shared = MagicMock()
        with patch.object(
            bridge_http._const, "TAILSCALE_DNS_OVERRIDES", {}
        ), patch.object(
            bridge_http, "async_get_clientsession", return_value=shared
        ) as get_session:
            result = async_get_bridge_session(hass, True)

        assert result is shared
        get_session.assert_called_once_with(hass, verify_ssl=True)

    def test_no_overrides_verify_ssl_false_forwarded(self):
        """verify_ssl=False is forwarded to the shared session factory."""
        hass = MagicMock()
        with patch.object(
            bridge_http._const, "TAILSCALE_DNS_OVERRIDES", {}
        ), patch.object(bridge_http, "async_get_clientsession") as get_session:
            async_get_bridge_session(hass, False)

        get_session.assert_called_once_with(hass, verify_ssl=False)

    def test_overrides_create_dedicated_cached_session(self):
        """With overrides, a dedicated session is created once and cached."""
        hass = MagicMock()
        hass.data = {}
        with patch.object(
            bridge_http._const,
            "TAILSCALE_DNS_OVERRIDES",
            {"bridge.tailnet.ts.net": "100.64.0.10"},
        ), patch.object(bridge_http, "aiohttp") as aio:
            aio.ClientSession.return_value = MagicMock(closed=False)

            first = async_get_bridge_session(hass, True)
            second = async_get_bridge_session(hass, True)

            assert first is second
            assert aio.ClientSession.call_count == 1
            # ssl=True → default certificate verification
            assert aio.TCPConnector.call_args.kwargs["ssl"] is True
            resolver = aio.TCPConnector.call_args.kwargs["resolver"]
            assert isinstance(resolver, _OverrideResolver)

    def test_overrides_sessions_keyed_by_verify_ssl(self):
        """verify_ssl True and False get separate dedicated sessions."""
        hass = MagicMock()
        hass.data = {}
        with patch.object(
            bridge_http._const,
            "TAILSCALE_DNS_OVERRIDES",
            {"bridge.tailnet.ts.net": "100.64.0.10"},
        ), patch.object(bridge_http, "aiohttp") as aio:
            aio.ClientSession.side_effect = lambda **kw: MagicMock(closed=False)

            s_verify = async_get_bridge_session(hass, True)
            s_noverify = async_get_bridge_session(hass, False)

            assert s_verify is not s_noverify
            assert aio.ClientSession.call_count == 2
            ssl_args = [c.kwargs["ssl"] for c in aio.TCPConnector.call_args_list]
            assert ssl_args == [True, False]

    async def test_close_bridge_sessions(self):
        """async_close_bridge_sessions closes and removes cached sessions."""
        session = MagicMock(closed=False)
        session.close = AsyncMock()
        hass = MagicMock()
        hass.data = {DOMAIN: {DATA_BRIDGE_SESSIONS: {"override_verify_True": session}}}

        await async_close_bridge_sessions(hass)

        session.close.assert_awaited_once()
        assert DATA_BRIDGE_SESSIONS not in hass.data[DOMAIN]

    async def test_close_bridge_sessions_noop_when_empty(self):
        """No cached sessions → no error."""
        hass = MagicMock()
        hass.data = {}
        await async_close_bridge_sessions(hass)  # must not raise
