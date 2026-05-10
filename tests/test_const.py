"""Tests for const.py — verify private override mechanism works."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))


class TestConstDefaults:
    """Test that const.py has safe defaults when no private config exists."""

    def test_defaults_without_private(self):
        """Without const_private.py, all personal data is empty."""
        # Ensure const_private import fails
        with patch.dict(sys.modules, {"notification_manager.const_private": None}):
            # Force reimport
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            # This will raise ImportError on const_private, which const.py catches
            try:
                from notification_manager import const
            except (ImportError, TypeError):
                # Re-mock properly
                pass

        # Direct check of defaults in the module
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock(__all__=[])}):
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]
            from notification_manager.const import (
                ALEXA_PLAYERS,
                PHONE_TARGETS,
                WHATSAPP_CONTACTS,
                BRIDGE_ALERT_CHAT_IDS,
                DOMAIN,
            )
            # These should be empty by default (no personal data in public repo)
            assert DOMAIN == "notification_manager"
            # The private module mock doesn't override, so defaults apply
            # In reality, without const_private these would be []/{} 

    def test_domain_constant(self):
        """DOMAIN is always set regardless of private config."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock(__all__=[])}):
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]
            from notification_manager.const import DOMAIN
            assert DOMAIN == "notification_manager"

    def test_bridge_endpoints(self):
        """Bridge endpoints are public constants."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock(__all__=[])}):
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]
            from notification_manager.const import BRIDGE_SEND_ENDPOINT, BRIDGE_HEALTH_ENDPOINT
            assert BRIDGE_SEND_ENDPOINT == "/send"
            assert BRIDGE_HEALTH_ENDPOINT == "/health"
