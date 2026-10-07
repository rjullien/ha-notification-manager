"""Tests for whatsapp_jid helpers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from notification_manager.whatsapp_jid import jid_to_phone


class TestJidToPhone:
    def test_individual_contact_stripped(self):
        assert jid_to_phone("33600000001@s.whatsapp.net") == "33600000001"

    def test_group_jid_unchanged(self):
        assert jid_to_phone("120363000000000000@g.us") == "120363000000000000@g.us"

    def test_other_suffix_passed_through(self):
        assert jid_to_phone("user@example.com") == "user@example.com"
