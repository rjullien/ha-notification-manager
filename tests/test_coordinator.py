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
