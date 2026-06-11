"""Tests for phone push + Telegram delivery (DM and groups)."""
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager.__init__ as nm


def _make_hass(call_log: list):
    hass = MagicMock()
    hass.data = {}

    async def record(domain, service, data, blocking=False):
        call_log.append((domain, service, dict(data), blocking))

    hass.services.async_call = AsyncMock(side_effect=record)
    return hass


def _make_entry(**data):
    entry = MagicMock()
    entry.data = data
    return entry


class TestSendPhone:
    """Mobile push + Telegram DM per target."""

    async def test_push_and_telegram_sent_blocking(self):
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry(
            phone_targets={
                "alice": {"mobile": "notify.mobile_app_alice", "telegram_chat_id": 111},
            },
            phone_default_targets=["alice"],
        )

        await nm._async_send_phone(hass, entry, "hello", "all")

        assert ("notify", "mobile_app_alice", {"message": "hello"}, True) in log
        assert ("telegram_bot", "send_message",
                {"chat_id": 111, "message": "hello"}, True) in log

    async def test_no_telegram_chat_id_push_only(self):
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry(
            phone_targets={
                "bob": {"mobile": "notify.mobile_app_bob", "telegram_chat_id": None},
            },
            phone_default_targets=["bob"],
        )

        await nm._async_send_phone(hass, entry, "hello", "bob")

        assert len(log) == 1
        assert log[0][0] == "notify"

    async def test_unknown_target_warns_and_skips(self, caplog):
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry(phone_targets={}, phone_default_targets=[])

        with caplog.at_level(logging.WARNING):
            await nm._async_send_phone(hass, entry, "hello", "ghost")

        assert log == []
        assert "Unknown phone target" in caplog.text

    async def test_multiple_targets_all_sent(self):
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry(
            phone_targets={
                "alice": {"mobile": "notify.mobile_app_alice", "telegram_chat_id": 1},
                "bob": {"mobile": "notify.mobile_app_bob", "telegram_chat_id": 2},
            },
            phone_default_targets=["alice", "bob"],
        )

        await nm._async_send_phone(hass, entry, "hello", "all")

        pushes = sorted(s for d, s, *_ in log if d == "notify")
        chats = sorted(d2["chat_id"] for d, _, d2, _ in log if d == "telegram_bot")
        assert pushes == ["mobile_app_alice", "mobile_app_bob"]
        assert chats == [1, 2]

    async def test_push_failure_does_not_block_telegram(self):
        """A failing mobile push must not prevent the Telegram send."""
        log: list = []
        hass = MagicMock()
        hass.data = {}

        async def record(domain, service, data, blocking=False):
            if domain == "notify":
                raise RuntimeError("push gateway down")
            log.append((domain, service, dict(data)))

        hass.services.async_call = AsyncMock(side_effect=record)
        entry = _make_entry(
            phone_targets={
                "alice": {"mobile": "notify.mobile_app_alice", "telegram_chat_id": 111},
            },
            phone_default_targets=["alice"],
        )

        await nm._async_send_phone(hass, entry, "hello", "alice")

        assert log == [("telegram_bot", "send_message", {"chat_id": 111, "message": "hello"})]


class TestCallTelegram:
    """Photo / parse_mode handling in the shared Telegram helper."""

    async def test_photo_path_sends_photo_with_caption(self):
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(
            hass, 111, "regarde", photo_path="/config/www/x.jpg", parse_mode="html"
        )

        domain, service, data, blocking = log[0]
        assert (domain, service) == ("telegram_bot", "send_photo")
        assert data == {"chat_id": 111, "file": "/config/www/x.jpg",
                        "caption": "regarde", "parse_mode": "html"}
        assert blocking is True

    async def test_photo_url_used_when_no_path(self):
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(hass, 111, "", photo_url="https://x/y.jpg")

        _, service, data, _ = log[0]
        assert service == "send_photo"
        assert data == {"chat_id": 111, "url": "https://x/y.jpg"}

    async def test_text_message_with_parse_mode(self):
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(hass, 111, "<b>hi</b>", parse_mode="html")

        _, service, data, _ = log[0]
        assert service == "send_message"
        assert data == {"chat_id": 111, "message": "<b>hi</b>", "parse_mode": "html"}


class TestTelegramGroup:
    """Group name → chat_id resolution and validation."""

    async def test_valid_group_string_chat_id_cast_to_int(self):
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry(telegram_groups={"family": "-1001234"})

        await nm._async_send_telegram_group(hass, entry, "hello", "Family")

        _, service, data, _ = log[0]
        assert service == "send_message"
        assert data["chat_id"] == -1001234  # int, from string config

    async def test_invalid_chat_id_logged_not_sent(self, caplog):
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry(telegram_groups={"family": "not-a-number"})

        with caplog.at_level(logging.ERROR):
            await nm._async_send_telegram_group(hass, entry, "hello", "family")

        assert log == []
        assert "Invalid chat_id" in caplog.text

    async def test_unknown_group_warns(self, caplog):
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry(telegram_groups={})

        with caplog.at_level(logging.WARNING):
            await nm._async_send_telegram_group(hass, entry, "hello", "ghost")

        assert log == []
        assert "Unknown Telegram group" in caplog.text
