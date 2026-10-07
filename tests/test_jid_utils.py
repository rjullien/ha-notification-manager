"""Tests for WhatsApp JID-to-phone conversion."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from notification_manager.jid_utils import jid_to_phone


class TestJidToPhone:
    """Individual contacts are stripped; groups pass through."""

    def test_individual_contact_stripped(self):
        assert jid_to_phone("33600000001@s.whatsapp.net") == "33600000001"

    def test_group_jid_unchanged(self):
        group = "120363000000000000@g.us"
        assert jid_to_phone(group) == group

    def test_bare_number_unchanged(self):
        assert jid_to_phone("33600000001") == "33600000001"

    def test_empty_string(self):
        assert jid_to_phone("") == ""

    def test_substring_false_positive_not_stripped(self):
        """Only a real suffix is stripped — substring matches must not mutate."""
        weird = "user@s.whatsapp.net.example"
        assert jid_to_phone(weird) == weird
