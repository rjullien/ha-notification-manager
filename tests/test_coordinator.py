"""Tests for the WhatsApp bridge coordinator."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))


class TestCoordinatorInit:
    """Test coordinator initialization."""

    def test_init_with_url(self):
        """Coordinator stores bridge config."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            # Re-import to get fresh module
            if "notification_manager.coordinator" in sys.modules:
                del sys.modules["notification_manager.coordinator"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.const import CONF_BRIDGE_URL, CONF_BRIDGE_TOKEN
            from notification_manager.coordinator import NotificationManagerCoordinator

            hass = MagicMock()
            entry_data = {
                CONF_BRIDGE_URL: "http://bridge:8080",
                CONF_BRIDGE_TOKEN: "test-token",
            }

            coord = NotificationManagerCoordinator(hass, entry_data)
            assert coord._bridge_url == "http://bridge:8080"
            assert coord._bridge_token == "test-token"
            assert coord.bridge_url == "http://bridge:8080"

    def test_init_empty_url(self):
        """Coordinator handles missing URL gracefully."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.coordinator" in sys.modules:
                del sys.modules["notification_manager.coordinator"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.coordinator import NotificationManagerCoordinator

            hass = MagicMock()
            coord = NotificationManagerCoordinator(hass, {})
            assert coord._bridge_url == ""

    def test_update_config(self):
        """update_config changes stored credentials."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.coordinator" in sys.modules:
                del sys.modules["notification_manager.coordinator"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.coordinator import NotificationManagerCoordinator

            hass = MagicMock()
            coord = NotificationManagerCoordinator(hass, {})
            coord.update_config("http://new:9090", "new-token")
            assert coord.bridge_url == "http://new:9090"
            assert coord._bridge_token == "new-token"
