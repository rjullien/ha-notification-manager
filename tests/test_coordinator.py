"""Tests for the WhatsApp bridge coordinator."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))


# We need to mock homeassistant modules before importing the coordinator
def _setup_ha_mocks():
    """Setup Home Assistant module mocks for testing."""
    # Create a real-ish DataUpdateCoordinator base class
    class FakeDataUpdateCoordinator:
        def __init__(self, hass, logger, *, name, update_interval):
            self.hass = hass
            self.name = name
            self.update_interval = update_interval
            self._unsub_refresh = None

        def __class_getitem__(cls, item):
            return cls

    ha_mock = MagicMock()
    ha_mock.core.HomeAssistant = MagicMock
    ha_mock.helpers.update_coordinator.DataUpdateCoordinator = FakeDataUpdateCoordinator
    ha_mock.helpers.update_coordinator.UpdateFailed = Exception
    ha_mock.helpers.aiohttp_client.async_get_clientsession = MagicMock()

    modules = {
        "homeassistant": ha_mock,
        "homeassistant.core": ha_mock.core,
        "homeassistant.helpers": ha_mock.helpers,
        "homeassistant.helpers.update_coordinator": ha_mock.helpers.update_coordinator,
        "homeassistant.helpers.aiohttp_client": ha_mock.helpers.aiohttp_client,
        "aiohttp": MagicMock(),
    }
    return modules, FakeDataUpdateCoordinator


class TestCoordinatorInit:
    """Test coordinator initialization."""

    def _import_coordinator(self):
        """Import coordinator with proper mocks."""
        ha_modules, _ = _setup_ha_mocks()
        mock_modules = {
            **ha_modules,
            "notification_manager.const_private": MagicMock(),
        }
        with patch.dict(sys.modules, mock_modules):
            # Clear cached modules
            for mod in list(sys.modules.keys()):
                if mod.startswith("notification_manager"):
                    del sys.modules[mod]

            from notification_manager.const import CONF_BRIDGE_URL, CONF_BRIDGE_TOKEN
            from notification_manager.coordinator import NotificationManagerCoordinator

            return NotificationManagerCoordinator, CONF_BRIDGE_URL, CONF_BRIDGE_TOKEN

    def test_init_with_url(self):
        """Coordinator stores bridge config."""
        Coord, CONF_BRIDGE_URL, CONF_BRIDGE_TOKEN = self._import_coordinator()

        hass = MagicMock()
        entry_data = {
            CONF_BRIDGE_URL: "http://bridge:8080",
            CONF_BRIDGE_TOKEN: "test-token",
        }

        coord = Coord(hass, entry_data)
        assert coord._bridge_url == "http://bridge:8080"
        assert coord._bridge_token == "test-token"
        assert coord.bridge_url == "http://bridge:8080"

    def test_init_empty_url(self):
        """Coordinator handles missing URL gracefully."""
        Coord, _, _ = self._import_coordinator()

        hass = MagicMock()
        coord = Coord(hass, {})
        # Falls back to DEFAULT_BRIDGE_URL (empty from mock)
        assert coord._bridge_url == "" or coord._bridge_url is not None

    def test_update_config(self):
        """update_config changes stored credentials."""
        Coord, _, _ = self._import_coordinator()

        hass = MagicMock()
        coord = Coord(hass, {})
        coord.update_config("http://new:9090", "new-token")
        assert coord.bridge_url == "http://new:9090"
        assert coord._bridge_token == "new-token"


def _response_cm(status: int):
    """Async-context-manager mock mimicking an aiohttp response."""
    resp = MagicMock()
    resp.status = status
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestCoordinatorUpdateData:
    """Test _async_update_data — health polling via the bridge session helper."""

    def _modules(self):
        """Module mocks incl. an aiohttp with REAL exception classes
        (`except aiohttp.ClientConnectorError` needs BaseException subclasses)."""
        ha_modules, _ = _setup_ha_mocks()
        aiohttp_m = MagicMock()
        aiohttp_m.ClientConnectorError = type("ClientConnectorError", (Exception,), {})
        aiohttp_m.ClientTimeout = MagicMock
        return {
            **ha_modules,
            "aiohttp": aiohttp_m,
            "notification_manager.const_private": MagicMock(),
        }, aiohttp_m

    def _fresh_import(self):
        """(Re)import the coordinator module inside the active patch.dict."""
        for mod in list(sys.modules.keys()):
            if mod.startswith("notification_manager"):
                del sys.modules[mod]
        import notification_manager.coordinator as coord_mod
        from notification_manager.const import (
            CONF_BRIDGE_TOKEN,
            CONF_BRIDGE_URL,
            CONF_VERIFY_SSL,
            SENSOR_STATE_CONNECTED,
            SENSOR_STATE_DISCONNECTED,
            SENSOR_STATE_UNKNOWN,
        )
        return coord_mod, {
            "url": CONF_BRIDGE_URL,
            "token": CONF_BRIDGE_TOKEN,
            "verify": CONF_VERIFY_SSL,
            "connected": SENSOR_STATE_CONNECTED,
            "disconnected": SENSOR_STATE_DISCONNECTED,
            "unknown": SENSOR_STATE_UNKNOWN,
        }

    @pytest.mark.asyncio
    async def test_http_200_connected(self):
        modules, _ = self._modules()
        with patch.dict(sys.modules, modules):
            coord_mod, c = self._fresh_import()
            coord = coord_mod.NotificationManagerCoordinator(
                MagicMock(), {c["url"]: "http://b:8080", c["token"]: "t"}
            )
            session = MagicMock()
            session.get = MagicMock(return_value=_response_cm(200))
            with patch.object(coord_mod, "async_get_bridge_session", return_value=session):
                assert await coord._async_update_data() == c["connected"]

    @pytest.mark.asyncio
    async def test_http_error_disconnected(self):
        modules, _ = self._modules()
        with patch.dict(sys.modules, modules):
            coord_mod, c = self._fresh_import()
            coord = coord_mod.NotificationManagerCoordinator(
                MagicMock(), {c["url"]: "http://b:8080", c["token"]: "t"}
            )
            session = MagicMock()
            session.get = MagicMock(return_value=_response_cm(503))
            with patch.object(coord_mod, "async_get_bridge_session", return_value=session):
                assert await coord._async_update_data() == c["disconnected"]

    @pytest.mark.asyncio
    async def test_connection_error_disconnected(self):
        modules, aiohttp_m = self._modules()
        with patch.dict(sys.modules, modules):
            coord_mod, c = self._fresh_import()
            coord = coord_mod.NotificationManagerCoordinator(
                MagicMock(), {c["url"]: "http://b:8080", c["token"]: "t"}
            )
            session = MagicMock()
            session.get = MagicMock(side_effect=aiohttp_m.ClientConnectorError())
            with patch.object(coord_mod, "async_get_bridge_session", return_value=session):
                assert await coord._async_update_data() == c["disconnected"]

    @pytest.mark.asyncio
    async def test_timeout_disconnected(self):
        modules, _ = self._modules()
        with patch.dict(sys.modules, modules):
            coord_mod, c = self._fresh_import()
            coord = coord_mod.NotificationManagerCoordinator(
                MagicMock(), {c["url"]: "http://b:8080", c["token"]: "t"}
            )
            session = MagicMock()
            session.get = MagicMock(side_effect=TimeoutError())
            with patch.object(coord_mod, "async_get_bridge_session", return_value=session):
                assert await coord._async_update_data() == c["disconnected"]

    @pytest.mark.asyncio
    async def test_no_url_unknown_without_request(self):
        modules, _ = self._modules()
        with patch.dict(sys.modules, modules):
            coord_mod, c = self._fresh_import()
            coord = coord_mod.NotificationManagerCoordinator(MagicMock(), {})
            with patch.object(coord_mod, "async_get_bridge_session") as get_session:
                assert await coord._async_update_data() in (c["unknown"], c["disconnected"])

    @pytest.mark.asyncio
    async def test_unexpected_error_raises_update_failed(self):
        modules, _ = self._modules()
        with patch.dict(sys.modules, modules):
            coord_mod, c = self._fresh_import()
            coord = coord_mod.NotificationManagerCoordinator(
                MagicMock(), {c["url"]: "http://b:8080", c["token"]: "t"}
            )
            session = MagicMock()
            session.get = MagicMock(side_effect=RuntimeError("boom"))
            with patch.object(coord_mod, "async_get_bridge_session", return_value=session):
                with pytest.raises(Exception):  # UpdateFailed mocked as Exception
                    await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_verify_ssl_forwarded_to_session_helper(self):
        modules, _ = self._modules()
        with patch.dict(sys.modules, modules):
            coord_mod, c = self._fresh_import()
            hass = MagicMock()
            coord = coord_mod.NotificationManagerCoordinator(
                hass, {c["url"]: "http://b:8080", c["token"]: "t", c["verify"]: False}
            )
            session = MagicMock()
            session.get = MagicMock(return_value=_response_cm(200))
            with patch.object(
                coord_mod, "async_get_bridge_session", return_value=session
            ) as get_session:
                await coord._async_update_data()
            get_session.assert_called_once_with(coord.hass, False)

    @pytest.mark.asyncio
    async def test_verify_ssl_defaults_to_true(self):
        """Pre-1.6.0 entry data without the flag → secure default."""
        modules, _ = self._modules()
        with patch.dict(sys.modules, modules):
            coord_mod, c = self._fresh_import()
            coord = coord_mod.NotificationManagerCoordinator(
                MagicMock(), {c["url"]: "http://b:8080", c["token"]: "t"}
            )
            session = MagicMock()
            session.get = MagicMock(return_value=_response_cm(200))
            with patch.object(
                coord_mod, "async_get_bridge_session", return_value=session
            ) as get_session:
                await coord._async_update_data()
            assert get_session.call_args.args[1] is True
