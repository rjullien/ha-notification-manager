"""Tests for config_flow validation logic."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))


class TestBridgeValidation:
    """Test _async_validate_bridge function."""

    @pytest.mark.asyncio
    async def test_empty_url_returns_error(self):
        """Empty bridge URL should return error."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.config_flow" in sys.modules:
                del sys.modules["notification_manager.config_flow"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.config_flow import _async_validate_bridge

            hass = MagicMock()
            result = await _async_validate_bridge(hass, "", "token")
            assert result == "bridge_url_required"

    @pytest.mark.asyncio
    async def test_successful_connection(self):
        """HTTP 200 → None (success)."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.config_flow" in sys.modules:
                del sys.modules["notification_manager.config_flow"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.config_flow import _async_validate_bridge

            # Mock the aiohttp session
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_resp)

            hass = MagicMock()
            with patch(
                "notification_manager.config_flow.async_get_clientsession",
                return_value=mock_session,
            ):
                result = await _async_validate_bridge(
                    hass, "http://bridge:8080", "token"
                )
                assert result is None

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        """HTTP 401 → invalid_auth."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.config_flow" in sys.modules:
                del sys.modules["notification_manager.config_flow"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.config_flow import _async_validate_bridge

            mock_resp = AsyncMock()
            mock_resp.status = 401
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_resp)

            hass = MagicMock()
            with patch(
                "notification_manager.config_flow.async_get_clientsession",
                return_value=mock_session,
            ):
                result = await _async_validate_bridge(
                    hass, "http://bridge:8080", "bad-token"
                )
                assert result == "invalid_auth"


class TestBridgeValidationVerifySsl:
    """verify_ssl propagation and connection errors in bridge validation."""

    def _import(self):
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.config_flow" in sys.modules:
                del sys.modules["notification_manager.config_flow"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]
            import notification_manager.config_flow as cf
        return cf

    def _ok_session(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)
        return session

    @pytest.mark.asyncio
    async def test_verify_ssl_false_forwarded(self):
        cf = self._import()
        session = self._ok_session()
        hass = MagicMock()

        with patch.object(cf, "async_get_clientsession", return_value=session) as gs:
            result = await cf._async_validate_bridge(
                hass, "http://bridge:8080", "tok", verify_ssl=False
            )

        assert result is None
        gs.assert_called_once_with(hass, verify_ssl=False)

    @pytest.mark.asyncio
    async def test_verify_ssl_defaults_to_true(self):
        """Without an explicit flag, validation verifies TLS (secure default)."""
        cf = self._import()
        session = self._ok_session()
        hass = MagicMock()

        with patch.object(cf, "async_get_clientsession", return_value=session) as gs:
            await cf._async_validate_bridge(hass, "http://bridge:8080", "tok")

        gs.assert_called_once_with(hass, verify_ssl=True)

    @pytest.mark.asyncio
    async def test_connection_error_returns_cannot_connect(self):
        cf = self._import()
        import aiohttp as aiohttp_mod  # conftest mock with real exception classes
        session = MagicMock()
        session.get = MagicMock(side_effect=aiohttp_mod.ClientConnectorError())
        hass = MagicMock()

        with patch.object(cf, "async_get_clientsession", return_value=session):
            result = await cf._async_validate_bridge(hass, "http://bridge:8080", "tok")

        assert result == "cannot_connect"

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_unknown(self):
        cf = self._import()
        session = MagicMock()
        session.get = MagicMock(side_effect=RuntimeError("boom"))
        hass = MagicMock()

        with patch.object(cf, "async_get_clientsession", return_value=session):
            result = await cf._async_validate_bridge(hass, "http://bridge:8080", "tok")

        assert result == "unknown"
