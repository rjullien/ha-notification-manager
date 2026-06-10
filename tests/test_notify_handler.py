"""Tests for _async_handle_notify — routing, detachment, verify_ssl propagation.

Note: patches use patch.object on a kept module reference. String-target
patching ("notification_manager.__init__.X") silently re-imports a fresh copy
of the module after conftest's patch.dict restores sys.modules, and would
patch the wrong module object.
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager.__init__ as nm


def _make_call(**overrides):
    call = MagicMock()
    call.data = {
        "message_tel": "",
        "message_alexa": "",
        "message_alexa_en": "",
        "notification_tel": "all",
        "notification_whatsapp": "none",
        "notification_alexa": "",
        "telegram_group": "",
        "photo_path": "",
        "photo_url": "",
        "parse_mode": "",
        **overrides,
    }
    return call


def _make_hass(created: list):
    """hass mock whose async_create_task actually schedules coroutines."""
    hass = MagicMock()
    hass.data = {}

    def create_task(coro):
        task = asyncio.ensure_future(coro)
        created.append(task)
        return task

    hass.async_create_task = MagicMock(side_effect=create_task)
    return hass


def _make_entry(verify_ssl=True):
    entry = MagicMock()
    entry.data = {
        "bridge_url": "http://bridge:8080",
        "bridge_token": "tok",
        "verify_ssl": verify_ssl,
    }
    return entry


class TestChannelRouting:
    """Which channels are triggered for which inputs."""

    async def test_all_channels_triggered(self):
        created: list = []
        hass = _make_hass(created)
        entry = _make_entry()
        call = _make_call(
            message_tel="hello",
            message_alexa="bonjour",
            message_alexa_en="hi",
            notification_tel="alice",
            notification_whatsapp="alice",
            notification_alexa="show",
            telegram_group="family",
        )

        with patch.object(nm, "_async_send_phone", new=AsyncMock()) as phone, \
             patch.object(nm, "_async_send_whatsapp", new=AsyncMock()) as wa, \
             patch.object(nm, "_async_send_telegram_group", new=AsyncMock()) as group, \
             patch.object(nm, "_async_send_alexa", new=AsyncMock()) as alexa, \
             patch.object(nm, "_async_send_alexa_en_delayed", new=AsyncMock()) as alexa_en:
            await nm._async_handle_notify(hass, entry, call)
            await asyncio.gather(*created)

        phone.assert_awaited_once()
        wa.assert_awaited_once()
        group.assert_awaited_once()
        alexa.assert_awaited_once()
        alexa_en.assert_awaited_once()

    async def test_none_values_disable_channels(self):
        created: list = []
        hass = _make_hass(created)
        entry = _make_entry()
        call = _make_call(
            message_tel="hello",
            message_alexa="bonjour",
            notification_tel="none",
            notification_whatsapp="none",
            notification_alexa="aucun",
        )

        with patch.object(nm, "_async_send_phone", new=AsyncMock()) as phone, \
             patch.object(nm, "_async_send_whatsapp", new=AsyncMock()) as wa, \
             patch.object(nm, "_async_send_alexa", new=AsyncMock()) as alexa:
            await nm._async_handle_notify(hass, entry, call)
            await asyncio.gather(*created)

        phone.assert_not_awaited()
        wa.assert_not_awaited()
        alexa.assert_not_awaited()

    async def test_empty_notification_alexa_still_schedules(self):
        """Empty notification_alexa = default 'show' keyword → Alexa runs."""
        created: list = []
        hass = _make_hass(created)
        entry = _make_entry()
        call = _make_call(message_alexa="bonjour", notification_alexa="")

        with patch.object(nm, "_async_send_alexa", new=AsyncMock()) as alexa:
            await nm._async_handle_notify(hass, entry, call)
            await asyncio.gather(*created)

        alexa.assert_awaited_once()

    async def test_telegram_group_without_payload_skipped(self):
        """telegram_group with no message and no photo → skipped with warning."""
        created: list = []
        hass = _make_hass(created)
        entry = _make_entry()
        call = _make_call(telegram_group="family")  # no message_tel, no photo

        with patch.object(nm, "_async_send_telegram_group", new=AsyncMock()) as group:
            await nm._async_handle_notify(hass, entry, call)
            await asyncio.gather(*created)

        group.assert_not_awaited()

    async def test_telegram_group_with_photo_only_runs(self):
        """telegram_group with only a photo (no text) is valid."""
        created: list = []
        hass = _make_hass(created)
        entry = _make_entry()
        call = _make_call(telegram_group="family", photo_path="/config/www/x.jpg")

        with patch.object(nm, "_async_send_telegram_group", new=AsyncMock()) as group:
            await nm._async_handle_notify(hass, entry, call)
            await asyncio.gather(*created)

        group.assert_awaited_once()


class TestVerifySslPropagation:
    """The configured verify_ssl flag must reach the WhatsApp sender."""

    @pytest.mark.parametrize("flag", [True, False])
    async def test_verify_ssl_forwarded_to_whatsapp(self, flag):
        created: list = []
        hass = _make_hass(created)
        entry = _make_entry(verify_ssl=flag)
        call = _make_call(message_tel="hello", notification_whatsapp="alice",
                          notification_tel="none")

        with patch.object(nm, "_async_send_whatsapp", new=AsyncMock()) as wa:
            await nm._async_handle_notify(hass, entry, call)
            await asyncio.gather(*created)

        assert wa.call_args.args[-1] is flag

    async def test_missing_verify_ssl_defaults_to_true(self):
        """Pre-1.6.0 entries without the flag → secure default (True)."""
        created: list = []
        hass = _make_hass(created)
        entry = MagicMock()
        entry.data = {"bridge_url": "http://b", "bridge_token": "t"}  # no verify_ssl
        call = _make_call(message_tel="hello", notification_whatsapp="alice",
                          notification_tel="none")

        with patch.object(nm, "_async_send_whatsapp", new=AsyncMock()) as wa:
            await nm._async_handle_notify(hass, entry, call)
            await asyncio.gather(*created)

        assert wa.call_args.args[-1] is True


class TestAlexaDetachment:
    """The handler must return without waiting for the Alexa cycle."""

    async def test_handler_returns_while_alexa_still_running(self):
        created: list = []
        hass = _make_hass(created)
        entry = _make_entry()
        call = _make_call(message_alexa="bonjour", notification_alexa="show")

        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_alexa(*args, **kwargs):
            started.set()
            await release.wait()

        with patch.object(nm, "_async_send_alexa", new=slow_alexa):
            # Must complete promptly even though the Alexa task is blocked
            await asyncio.wait_for(
                nm._async_handle_notify(hass, entry, call), timeout=1
            )

            await asyncio.sleep(0)
            assert started.is_set()
            assert not release.is_set()  # handler returned before Alexa finished

            release.set()
            await asyncio.gather(*created)

    async def test_en_message_does_not_block_handler(self):
        """The EN task is scheduled, not awaited by the handler."""
        created: list = []
        hass = _make_hass(created)
        entry = _make_entry()
        call = _make_call(message_alexa_en="good night")

        release = asyncio.Event()

        async def slow_en(*args, **kwargs):
            await release.wait()

        with patch.object(nm, "_async_send_alexa_en_delayed", new=slow_en):
            await asyncio.wait_for(
                nm._async_handle_notify(hass, entry, call), timeout=1
            )
            assert len(created) == 1
            release.set()
            await asyncio.gather(*created)
