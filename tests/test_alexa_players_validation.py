"""Tests for Alexa player list validation."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from notification_manager.alexa_players_validation import validate_alexa_players


class TestValidateAlexaPlayers:
    """Malformed configs must be rejected before saving."""

    def test_valid_list(self):
        players = ["media_player.echo_show", "media_player.echo_dot"]
        assert validate_alexa_players(players) == players

    def test_deduplicates(self):
        assert validate_alexa_players(
            ["media_player.a", "media_player.a", "media_player.b"]
        ) == ["media_player.a", "media_player.b"]

    def test_not_a_list(self):
        assert validate_alexa_players({"media_player.a": {}}) is None

    def test_non_string_element(self):
        assert validate_alexa_players(["media_player.a", 123]) is None

    def test_empty_string_element(self):
        assert validate_alexa_players(["media_player.a", ""]) is None

    def test_strips_whitespace(self):
        assert validate_alexa_players(["  media_player.a  "]) == ["media_player.a"]
