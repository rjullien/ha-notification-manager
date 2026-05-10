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
