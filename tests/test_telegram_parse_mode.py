"""Regression tests: Telegram messages must never be parsed as markdown by accident.

Background
----------
Home Assistant's telegram_bot platform defaults its parser to markdown
(``vol.Optional(ATTR_PARSER, default=PARSER_MD)``) and only overrides it when
the service data explicitly contains a ``parse_mode`` key. Omitting the key
therefore means *markdown*, so a message with an unbalanced "_" or "*" — an
entity_id, a snake_case label, a file name — is rejected by the Telegram API
with "Can't parse entities" and the notification is silently lost.

These tests pin the fix: parse_mode is always sent, empty means "plain_text",
and the messages we do format on purpose escape their dynamic parts.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager.__init__ as nm
    from notification_manager import watchdog as wd
    from notification_manager.telegram_text import (
        PARSE_MODE_HTML,
        PARSE_MODE_PLAIN,
        escape_html,
    )


def _make_hass(call_log: list):
    hass = MagicMock()
    hass.data = {}

    async def record(domain, service, data, blocking=False):
        call_log.append((domain, service, dict(data), blocking))

    hass.services.async_call = AsyncMock(side_effect=record)
    return hass


# A real message from controle_roquefort_manager's "On part !" checklist: nine
# underscores, i.e. an odd number, which is what made Telegram reject it.
CHECKLIST_MESSAGE = (
    "Départ vacances activé !\n"
    "✅ CE pausé\n"
    "❌ Volet manager_volet_chambre_active encore actif !\n"
    "✅ Volet manager_volet_sam_active auto OFF\n"
    "✅ Volet manager_volet_cuisine_active auto OFF"
)


class TestParseModeAlwaysExplicit:
    """_async_call_telegram must never let telegram_bot pick its own default."""

    async def test_empty_parse_mode_sends_plain_text_sentinel(self):
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(hass, 111, "hello")

        _, service, data, _ = log[0]
        assert service == "send_message"
        assert data["parse_mode"] == PARSE_MODE_PLAIN

    async def test_parse_mode_key_is_present_even_when_not_requested(self):
        """The bug was a *missing key*, not a wrong value — assert presence."""
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(hass, 111, "hello")

        assert "parse_mode" in log[0][2]

    async def test_underscore_heavy_message_sent_plain_and_unaltered(self):
        """Odd number of "_": must go out as plain text, byte-for-byte intact."""
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(hass, 111, CHECKLIST_MESSAGE)

        _, _, data, _ = log[0]
        assert data["parse_mode"] == PARSE_MODE_PLAIN
        assert data["message"] == CHECKLIST_MESSAGE
        assert CHECKLIST_MESSAGE.count("_") % 2 == 1  # guards the fixture itself

    @pytest.mark.parametrize("requested", ["html", "markdown", "markdownv2"])
    async def test_explicit_parse_mode_is_preserved(self, requested):
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(hass, 111, "<b>hi</b>", parse_mode=requested)

        assert log[0][2]["parse_mode"] == requested

    async def test_photo_without_parse_mode_also_plain(self):
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(
            hass, 111, "légende_avec_underscores", photo_path="/config/www/x.jpg"
        )

        _, service, data, _ = log[0]
        assert service == "send_photo"
        assert data["parse_mode"] == PARSE_MODE_PLAIN
        assert data["caption"] == "légende_avec_underscores"

    async def test_photo_with_explicit_parse_mode_is_preserved(self):
        log: list = []
        hass = _make_hass(log)

        await nm._async_call_telegram(
            hass, 111, "<i>x</i>", photo_url="https://x/y.jpg", parse_mode="html"
        )

        assert log[0][2]["parse_mode"] == "html"

    async def test_telegram_group_inherits_plain_default(self):
        log: list = []
        hass = _make_hass(log)
        entry = MagicMock()
        entry.data = {"telegram_groups": {"family": "-1001234"}}

        await nm._async_send_telegram_group(hass, entry, CHECKLIST_MESSAGE, "family")

        _, service, data, _ = log[0]
        assert service == "send_message"
        assert data["parse_mode"] == PARSE_MODE_PLAIN


class TestEscapeHtml:
    """escape_html covers exactly Telegram's HTML markup characters."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("switch.volets_manager_volet_chambre_active",
             "switch.volets_manager_volet_chambre_active"),  # "_" inert in HTML
            ("a & b", "a &amp; b"),
            ("<b>", "&lt;b&gt;"),
            ("Salon <Rez> & Cuisine", "Salon &lt;Rez&gt; &amp; Cuisine"),
            ("", ""),
            (None, ""),
            (42, "42"),
        ],
    )
    def test_escaping(self, raw, expected):
        assert escape_html(raw) == expected

    def test_quotes_left_readable(self):
        """Quotes are inert outside tag attributes — keep names legible."""
        assert escape_html('Volet "Chambre"') == 'Volet "Chambre"'

    def test_ampersand_escaped_once(self):
        """No double-escaping of an already-escaped sequence's output."""
        assert escape_html("&amp;") == "&amp;amp;"


class TestWatchdogAlertFormatting:
    """The watchdog formats on purpose, so its interpolations must be escaped."""

    @staticmethod
    def _watchdog_with_unavailable(entity_id: str, friendly: str):
        hass = MagicMock()
        state = MagicMock()
        state.state = "unavailable"
        state.attributes = {"friendly_name": friendly}
        hass.states.get = MagicMock(return_value=state)

        entry = MagicMock()
        entry.data = {}
        watchdog = wd.EntityWatchdog(hass, entry)
        # Entity already seen as unavailable long enough to breach the threshold.
        watchdog._unavailable_since = {
            entity_id: datetime.now(timezone.utc) - timedelta(hours=2)
        }
        watchdog._last_alerted = {}
        watchdog._async_send_alert = AsyncMock()
        return watchdog

    async def test_alert_uses_html_and_escapes_friendly_name(self):
        entity_id = "switch.arrosage_vanne_1"
        watchdog = self._watchdog_with_unavailable(entity_id, "Vanne <Jardin> & Potager")

        await watchdog._async_check_entities(
            [entity_id], timedelta(minutes=5), "critical"
        )

        watchdog._async_send_alert.assert_awaited_once()
        message = watchdog._async_send_alert.await_args.args[0]
        assert "&lt;Jardin&gt; &amp; Potager" in message
        assert f"<code>{entity_id}</code>" in message
        assert "<b>" in message and "*" not in message

    async def test_alert_sent_with_html_parse_mode(self):
        hass = MagicMock()
        log: list = []

        async def record(domain, service, data, blocking=False):
            log.append((domain, service, dict(data)))

        hass.services.async_call = AsyncMock(side_effect=record)
        entry = MagicMock()
        entry.data = {}
        watchdog = wd.EntityWatchdog(hass, entry)

        with patch.object(wd, "WATCHDOG_TELEGRAM_CHAT_IDS", [999]):
            await watchdog._async_send_alert("<b>alerte</b>")

        assert log, "no telegram_bot call recorded"
        assert log[0][2]["parse_mode"] == PARSE_MODE_HTML
