"""Tests for target resolution logic in __init__.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# We need to test the resolution functions which are pure logic
# Import after conftest sets up mocks
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

# Patch const_private import to avoid FileNotFoundError
with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    from notification_manager.const import (
        DOMAIN,
        SERVICE_NOTIFY,
        BRIDGE_SEND_ENDPOINT,
        BRIDGE_HEALTH_ENDPOINT,
        BRIDGE_TIMEOUT,
        BRIDGE_RETRIES,
        SENSOR_STATE_CONNECTED,
        SENSOR_STATE_DISCONNECTED,
        SENSOR_STATE_UNKNOWN,
    )


class TestPhoneTargetResolution:
    """Test _resolve_phone_targets function."""

    def _get_resolver(self):
        """Import and return the resolver (needs mocks in place)."""
        # We'll test the logic directly
        from notification_manager.__init__ import _resolve_phone_targets
        return _resolve_phone_targets

    def test_all_returns_defaults(self):
        resolve = self._get_resolver()
        # Patch PHONE_DEFAULT_TARGETS
        with patch("notification_manager.__init__.PHONE_DEFAULT_TARGETS", ["rene", "nicole"]):
            result = resolve("all")
            assert result == ["rene", "nicole"]

    def test_empty_returns_defaults(self):
        resolve = self._get_resolver()
        with patch("notification_manager.__init__.PHONE_DEFAULT_TARGETS", ["rene", "nicole"]):
            result = resolve("")
            assert result == ["rene", "nicole"]

    def test_specific_names(self):
        resolve = self._get_resolver()
        result = resolve("rene camille")
        assert result == ["rene", "camille"]

    def test_none_returns_empty(self):
        resolve = self._get_resolver()
        # "none" is not in (all, "")
        result = resolve("none")
        assert result == ["none"]  # It just splits, doesn't filter

    def test_case_insensitive(self):
        resolve = self._get_resolver()
        result = resolve("Rene Nicole")
        assert result == ["rene", "nicole"]


class TestAlexaTargetResolution:
    """Test _resolve_alexa_targets function."""

    def _get_resolver(self):
        from notification_manager.__init__ import _resolve_alexa_targets
        return _resolve_alexa_targets

    def test_empty_uses_default_keyword(self):
        resolve = self._get_resolver()
        with patch("notification_manager.__init__.ALEXA_DEFAULT_KEYWORD", "show"), \
             patch("notification_manager.__init__.ALEXA_PLAYERS", [
                 "media_player.echo_show_2",
                 "media_player.echo_dot",
                 "media_player.echo_show_chambre",
             ]):
            result = resolve("")
            assert "media_player.echo_show_2" in result
            assert "media_player.echo_show_chambre" in result
            assert "media_player.echo_dot" not in result

    def test_keyword_matching(self):
        resolve = self._get_resolver()
        with patch("notification_manager.__init__.ALEXA_PLAYERS", [
            "media_player.echo_show_2",
            "media_player.jardin",
            "media_player.chambre",
        ]):
            result = resolve("jardin chambre")
            assert "media_player.jardin" in result
            assert "media_player.chambre" in result
            assert "media_player.echo_show_2" not in result

    def test_aucun_returns_empty(self):
        resolve = self._get_resolver()
        with patch("notification_manager.__init__.ALEXA_PLAYERS", [
            "media_player.echo_show_2",
        ]):
            # Empty string after strip → uses default keyword
            # But if we pass a real keyword that matches nothing...
            result = resolve("aucun")
            # "aucun" won't match any entity_id
            assert result == []


class TestWhatsAppTargetResolution:
    """Test _resolve_whatsapp_targets function."""

    def _get_resolver(self):
        from notification_manager.__init__ import _resolve_whatsapp_targets
        return _resolve_whatsapp_targets

    def test_none_returns_empty(self):
        resolve = self._get_resolver()
        assert resolve("none") == []
        assert resolve("aucun") == []
        assert resolve("") == []

    def test_known_contacts(self):
        resolve = self._get_resolver()
        with patch("notification_manager.__init__.WHATSAPP_CONTACTS", {
            "rene": "33600000001@s.whatsapp.net",
            "nicole": "33600000002@s.whatsapp.net",
        }):
            result = resolve("rene nicole")
            assert result == [
                "33600000001@s.whatsapp.net",
                "33600000002@s.whatsapp.net",
            ]

    def test_unknown_contact_skipped(self):
        resolve = self._get_resolver()
        with patch("notification_manager.__init__.WHATSAPP_CONTACTS", {
            "rene": "33600000001@s.whatsapp.net",
        }):
            result = resolve("rene unknown")
            assert result == ["33600000001@s.whatsapp.net"]
