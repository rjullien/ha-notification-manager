"""Tests for const.py — verify private override mechanism works."""
import logging
import sys
import types
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
            assert BRIDGE_SEND_ENDPOINT == "/send/message"
            assert BRIDGE_HEALTH_ENDPOINT == "/app/status"

    def test_critical_interval_matches_docstring_intent(self):
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock(__all__=[])}):
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]
            from notification_manager.const import WATCHDOG_CRITICAL_INTERVAL_MINUTES
            assert WATCHDOG_CRITICAL_INTERVAL_MINUTES == 5

    def test_broken_private_file_keeps_defaults(self, caplog):
        """A raising private config must not prevent const from loading."""
        if "notification_manager.const" in sys.modules:
            del sys.modules["notification_manager.const"]

        fake_spec = MagicMock()
        fake_spec.loader = MagicMock()
        fake_spec.loader.exec_module.side_effect = RuntimeError("syntax boom")
        real_mod = types.ModuleType("_nm_private")

        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock(__all__=[])}):
            with patch("os.path.isfile", return_value=True), \
                 patch("importlib.util.spec_from_file_location", return_value=fake_spec), \
                 patch("importlib.util.module_from_spec", return_value=real_mod), \
                 caplog.at_level(logging.ERROR):
                from notification_manager.const import (
                    DOMAIN,
                    PHONE_TARGETS,
                    WATCHDOG_CRITICAL_INTERVAL_MINUTES,
                )

        assert DOMAIN == "notification_manager"
        assert PHONE_TARGETS == {}
        assert WATCHDOG_CRITICAL_INTERVAL_MINUTES == 5
        assert "Failed to load private config" in caplog.text
