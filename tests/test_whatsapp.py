"""Tests for WhatsApp delivery — retries, parallel recipients, aggregated alert."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager.__init__ as nm
    from notification_manager.const import BRIDGE_RETRIES


def _response_cm(status: int, body: str = "err"):
    """Build an async-context-manager mock mimicking an aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.text = AsyncMock(return_value=body)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


class TestSendToJid:
    """Per-recipient retry logic."""

    async def test_success_first_attempt(self):
        session = MagicMock()
        session.post = MagicMock(return_value=_response_cm(200))

        ok = await nm._async_send_whatsapp_to_jid(
            session, "http://b/send", {}, "33600000001@s.whatsapp.net", "msg"
        )

        assert ok is True
        assert session.post.call_count == 1

    async def test_retry_then_success(self):
        """HTTP 500 then 200 → success after 2 attempts, with backoff sleep."""
        session = MagicMock()
        session.post = MagicMock(side_effect=[_response_cm(500), _response_cm(200)])

        with patch.object(nm.asyncio, "sleep", new=AsyncMock()) as sleep:
            ok = await nm._async_send_whatsapp_to_jid(
                session, "http://b/send", {}, "j@s.whatsapp.net", "msg"
            )

        assert ok is True
        assert session.post.call_count == 2
        sleep.assert_awaited_once_with(2)  # exponential backoff: 2**1

    async def test_all_attempts_fail(self):
        """Connection errors on every attempt → False after BRIDGE_RETRIES."""
        session = MagicMock()
        session.post = MagicMock(side_effect=RuntimeError("bridge down"))

        with patch.object(nm.asyncio, "sleep", new=AsyncMock()):
            ok = await nm._async_send_whatsapp_to_jid(
                session, "http://b/send", {}, "j@s.whatsapp.net", "msg"
            )

        assert ok is False
        assert session.post.call_count == BRIDGE_RETRIES

    async def test_payload_contains_jid_and_text(self):
        session = MagicMock()
        session.post = MagicMock(return_value=_response_cm(200))

        await nm._async_send_whatsapp_to_jid(
            session, "http://b/send", {"Authorization": "Bearer t"}, "j@s", "coucou"
        )

        kwargs = session.post.call_args.kwargs
        assert kwargs["json"] == {"phone": "j@s", "message": "coucou"}
        assert kwargs["headers"]["Authorization"] == "Bearer t"


class TestSendWhatsapp:
    """Orchestration: parallel recipients + single aggregated alert."""

    def _entry(self):
        entry = MagicMock()
        entry.data = {
            "whatsapp_contacts": {
                "alice": "33600000001@s.whatsapp.net",
                "bob": "33600000002@s.whatsapp.net",
            },
            "bridge_alert_chat_ids": [999],
        }
        return entry

    async def test_partial_failure_sends_single_aggregated_alert(self):
        hass = MagicMock()
        entry = self._entry()

        with patch.object(nm, "async_get_bridge_session", return_value=MagicMock()), \
             patch.object(nm, "_async_send_whatsapp_to_jid",
                          new=AsyncMock(side_effect=[False, True])) as send, \
             patch.object(nm, "_async_send_bridge_alert", new=AsyncMock()) as alert:
            await nm._async_send_whatsapp(
                hass, entry, "msg", "alice bob", "http://b", "tok", True
            )

        assert send.await_count == 2
        alert.assert_awaited_once()
        assert "1/2" in alert.call_args.args[-1]

    async def test_all_success_no_alert(self):
        hass = MagicMock()
        entry = self._entry()

        with patch.object(nm, "async_get_bridge_session", return_value=MagicMock()), \
             patch.object(nm, "_async_send_whatsapp_to_jid",
                          new=AsyncMock(return_value=True)), \
             patch.object(nm, "_async_send_bridge_alert", new=AsyncMock()) as alert:
            await nm._async_send_whatsapp(
                hass, entry, "msg", "alice bob", "http://b", "tok", True
            )

        alert.assert_not_awaited()

    async def test_recipients_sent_in_parallel(self):
        """Both sends must be in flight at the same time (gather, not sequential)."""
        hass = MagicMock()
        entry = self._entry()

        in_flight = 0
        max_in_flight = 0

        async def tracked_send(*args, **kwargs):
            nonlocal in_flight, max_in_flight
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1
            return True

        with patch.object(nm, "async_get_bridge_session", return_value=MagicMock()), \
             patch.object(nm, "_async_send_whatsapp_to_jid", new=tracked_send):
            await nm._async_send_whatsapp(
                hass, entry, "msg", "alice bob", "http://b", "tok", True
            )

        assert max_in_flight == 2

    async def test_no_bridge_url_logs_error_no_session(self):
        hass = MagicMock()
        entry = self._entry()

        with patch.object(nm, "async_get_bridge_session") as get_session:
            await nm._async_send_whatsapp(hass, entry, "msg", "alice", "", "tok", True)

        get_session.assert_not_called()

    async def test_verify_ssl_forwarded_to_session(self):
        hass = MagicMock()
        entry = self._entry()

        with patch.object(nm, "async_get_bridge_session",
                          return_value=MagicMock()) as get_session, \
             patch.object(nm, "_async_send_whatsapp_to_jid",
                          new=AsyncMock(return_value=True)):
            await nm._async_send_whatsapp(
                hass, entry, "msg", "alice", "http://b", "tok", False
            )

        get_session.assert_called_once_with(hass, False)


class TestBridgeAlert:
    """Telegram alert fan-out to configured chat IDs."""

    async def test_alert_sent_to_each_chat_id(self):
        log: list = []
        hass = MagicMock()

        async def record(domain, service, data, blocking=False):
            log.append((data["chat_id"], blocking))

        hass.services.async_call = AsyncMock(side_effect=record)
        entry = MagicMock()
        entry.data = {"bridge_alert_chat_ids": [111, 222]}

        await nm._async_send_bridge_alert(hass, entry, "bridge down")

        assert log == [(111, True), (222, True)]

    async def test_one_failing_chat_does_not_stop_others(self):
        sent: list = []
        hass = MagicMock()

        async def record(domain, service, data, blocking=False):
            if data["chat_id"] == 111:
                raise RuntimeError("blocked")
            sent.append(data["chat_id"])

        hass.services.async_call = AsyncMock(side_effect=record)
        entry = MagicMock()
        entry.data = {"bridge_alert_chat_ids": [111, 222]}

        await nm._async_send_bridge_alert(hass, entry, "bridge down")

        assert sent == [222]
