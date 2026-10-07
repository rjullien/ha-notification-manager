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
    import notification_manager.alexa as _alexa
    from notification_manager.messaging import (
        _resolve_phone_targets,
        _resolve_whatsapp_targets,
    )
    from notification_manager.alexa import (
        _resolve_alexa_targets,
    )
    # Patch surface for Alexa keyword constants (looked up in alexa module).
    _nm = _alexa

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
        # patch.object on the kept module reference — string-target patching
        # would re-import a fresh module copy and patch the wrong object.
        with patch.object(_nm, "ALEXA_DEFAULT_KEYWORD", "show"):
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

    def test_keyword_excludes_from_broad_show(self):
        """Bedroom Show stays in players but is dropped from bare show."""
        bedroom = "media_player.your_bedroom_echo_show_11_rene"
        players = [
            "media_player.your_kitchen_rene_echo_show",
            "media_player.your_living_echo_show",
            bedroom,
            "media_player.your_office_echo_show",
        ]
        excludes = {"show": [bedroom]}
        with patch.object(_nm, "ALEXA_DEFAULT_KEYWORD", "show"), patch.object(
            _nm, "ALEXA_KEYWORD_EXCLUDES", excludes
        ):
            empty = _resolve_alexa_targets("", players)
            show = _resolve_alexa_targets("show", players)
        for result in (empty, show):
            assert bedroom not in result
            assert "media_player.your_kitchen_rene_echo_show" in result
            assert "media_player.your_living_echo_show" in result
            assert "media_player.your_office_echo_show" in result

    def test_show_11_compound_still_targets_excluded_show(self):
        """Compound show_11 still reaches the bedroom device alone."""
        bedroom = "media_player.your_bedroom_echo_show_11_rene"
        players = [
            "media_player.your_kitchen_rene_echo_show",
            "media_player.your_living_echo_show",
            bedroom,
            "media_player.your_office_echo_show",
        ]
        excludes = {"show": [bedroom]}
        with patch.object(_nm, "ALEXA_KEYWORD_EXCLUDES", excludes):
            result = _resolve_alexa_targets("show_11", players)
        assert result == [bedroom]

    def test_show_2_alias_exclude_uses_post_alias_rene_show(self):
        """show_2 aliases to rene_show; exclude key must be post-alias."""
        bedroom = "media_player.your_bedroom_echo_show_11_rene"
        kitchen = "media_player.your_kitchen_rene_echo_show"
        players = [
            kitchen,
            "media_player.your_living_echo_show",
            bedroom,
            "media_player.your_office_echo_show",
        ]
        # Exclude keyed on post-alias "rene_show" (not "show_2").
        excludes = {"rene_show": [bedroom]}
        with patch.object(_nm, "ALEXA_KEYWORD_ALIASES", {"show_2": "rene_show"}), patch.object(
            _nm, "ALEXA_KEYWORD_EXCLUDES", excludes
        ):
            via_alias = _resolve_alexa_targets("show_2", players)
            via_direct = _resolve_alexa_targets("rene_show", players)
            # show_11 is a different keyword — bedroom still reachable.
            via_show_11 = _resolve_alexa_targets("show_11", players)

        for result in (via_alias, via_direct):
            assert bedroom not in result
            assert kitchen in result
            # living/office lack "rene" → not matched by compound rene_show
            assert "media_player.your_living_echo_show" not in result
            assert "media_player.your_office_echo_show" not in result
        assert via_show_11 == [bedroom]

    def test_show_2_exclude_under_show_2_key_is_ignored(self):
        """Excludes keyed on the pre-alias token do not apply after aliasing."""
        bedroom = "media_player.your_bedroom_echo_show_11_rene"
        kitchen = "media_player.your_kitchen_rene_echo_show"
        players = [kitchen, bedroom]
        excludes = {"show_2": [bedroom]}  # wrong key — lookup is post-alias
        with patch.object(_nm, "ALEXA_KEYWORD_ALIASES", {"show_2": "rene_show"}), patch.object(
            _nm, "ALEXA_KEYWORD_EXCLUDES", excludes
        ):
            result = _resolve_alexa_targets("show_2", players)
        assert bedroom in result
        assert kitchen in result


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
