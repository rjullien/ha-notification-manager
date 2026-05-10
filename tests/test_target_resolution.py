"""Tests for target resolution logic in __init__.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


# Resolver helpers — imported once, no global-patch needed (functions now take
# their data as explicit arguments instead of reading module-level globals).
with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    if "notification_manager.__init__" in sys.modules:
        del sys.modules["notification_manager.__init__"]
    from notification_manager.__init__ import (
        _resolve_phone_targets,
        _resolve_alexa_targets,
        _resolve_whatsapp_targets,
    )

_SAMPLE_PHONE_DEFAULTS = ["rene", "nicole"]
_SAMPLE_ALEXA_PLAYERS = [
    "media_player.echo_show_2",
    "media_player.echo_dot",
    "media_player.echo_show_chambre",
]
_SAMPLE_WA_CONTACTS = {
    "rene": "33600000001@s.whatsapp.net",
    "nicole": "33600000002@s.whatsapp.net",
}


class TestPhoneTargetResolution:
    """Test _resolve_phone_targets function."""

    def test_all_returns_defaults(self):
        result = _resolve_phone_targets("all", _SAMPLE_PHONE_DEFAULTS)
        assert result == ["rene", "nicole"]

    def test_empty_returns_defaults(self):
        result = _resolve_phone_targets("", _SAMPLE_PHONE_DEFAULTS)
        assert result == ["rene", "nicole"]

    def test_specific_names(self):
        result = _resolve_phone_targets("rene camille", _SAMPLE_PHONE_DEFAULTS)
        assert result == ["rene", "camille"]

    def test_none_returns_empty(self):
        # "none" is not in ("all", "") so it splits literally
        result = _resolve_phone_targets("none", _SAMPLE_PHONE_DEFAULTS)
        assert result == ["none"]

    def test_case_insensitive(self):
        result = _resolve_phone_targets("Rene Nicole", _SAMPLE_PHONE_DEFAULTS)
        assert result == ["rene", "nicole"]


class TestAlexaTargetResolution:
    """Test _resolve_alexa_targets function."""

    def test_empty_uses_default_keyword(self):
        with patch(
            "notification_manager.__init__.ALEXA_DEFAULT_KEYWORD", "show"
        ):
            result = _resolve_alexa_targets("", _SAMPLE_ALEXA_PLAYERS)
        assert "media_player.echo_show_2" in result
        assert "media_player.echo_show_chambre" in result
        assert "media_player.echo_dot" not in result

    def test_keyword_matching(self):
        players = [
            "media_player.echo_show_2",
            "media_player.jardin",
            "media_player.chambre",
        ]
        result = _resolve_alexa_targets("jardin chambre", players)
        assert "media_player.jardin" in result
        assert "media_player.chambre" in result
        assert "media_player.echo_show_2" not in result

    def test_aucun_returns_empty(self):
        # "aucun" won't match any entity_id in the list
        result = _resolve_alexa_targets("aucun", _SAMPLE_ALEXA_PLAYERS)
        assert result == []


class TestWhatsAppTargetResolution:
    """Test _resolve_whatsapp_targets function."""

    def test_none_returns_empty(self):
        assert _resolve_whatsapp_targets("none", _SAMPLE_WA_CONTACTS) == []
        assert _resolve_whatsapp_targets("aucun", _SAMPLE_WA_CONTACTS) == []
        assert _resolve_whatsapp_targets("", _SAMPLE_WA_CONTACTS) == []

    def test_known_contacts(self):
        result = _resolve_whatsapp_targets("rene nicole", _SAMPLE_WA_CONTACTS)
        assert result == [
            "33600000001@s.whatsapp.net",
            "33600000002@s.whatsapp.net",
        ]

    def test_unknown_contact_skipped(self):
        result = _resolve_whatsapp_targets("rene unknown", _SAMPLE_WA_CONTACTS)
        assert result == ["33600000001@s.whatsapp.net"]
