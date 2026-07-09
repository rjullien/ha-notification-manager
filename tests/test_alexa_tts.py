"""Tests for the Alexa TTS flow — lock serialisation and volume save/restore."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    from notification_manager.__init__ import (
        DATA_ALEXA_LOCK,
        _async_send_alexa,
        _async_send_alexa_en,
    )
    from notification_manager.const import ALEXA_DEFAULT_VOLUME, DOMAIN


def _make_hass(call_log: list, volume: float = 0.4):
    """Build a hass mock that records service calls in order."""
    hass = MagicMock()
    hass.data = {}

    async def record(domain, service, data, blocking=False):
        call_log.append((domain, service, dict(data), blocking))

    hass.services.async_call = AsyncMock(side_effect=record)

    state = MagicMock()
    state.state = "on"
    state.attributes = {"volume_level": volume}
    hass.states.get = MagicMock(return_value=state)
    return hass


def _make_entry(players=None, tts_volume=0.7, delay=0):
    entry = MagicMock()
    entry.data = {
        "alexa_players": players or ["media_player.echo_show_salon"],
        "alexa_tts_volume": tts_volume,
        "alexa_post_tts_delay": delay,
    }
    return entry


class TestAlexaVolumeCycle:
    """Single-call behaviour: save → set → TTS → restore."""

    async def test_volume_set_before_tts_then_restored(self):
        """Volume is raised to TTS level BEFORE speech, then restored after."""
        log: list = []
        hass = _make_hass(log, volume=0.4)
        entry = _make_entry()

        await _async_send_alexa(hass, entry, "Bonjour", "show")

        assert [(d, s) for d, s, *_ in log] == [
            ("media_player", "volume_set"),
            ("notify", "alexa_media"),
            ("media_player", "volume_set"),
        ]
        # TTS level first, original restored last
        assert log[0][2]["volume_level"] == 0.7
        assert log[2][2]["volume_level"] == 0.4
        # Every call is blocking so failures surface and ordering is guaranteed
        assert all(blocking for *_, blocking in log)

    async def test_unavailable_player_uses_default_volume(self):
        """Unavailable player → TTS skipped entirely (no volume to restore)."""
        log: list = []
        hass = _make_hass(log)
        hass.states.get = MagicMock(return_value=None)
        entry = _make_entry()

        await _async_send_alexa(hass, entry, "Bonjour", "show")

        # All targets unavailable → no TTS sent, no volume cycle
        assert log == []

    async def test_invalid_volume_attribute_uses_default(self):
        """Non-numeric volume_level attribute → default volume."""
        log: list = []
        hass = _make_hass(log)
        state = MagicMock()
        state.state = "on"
        state.attributes = {"volume_level": "garbage"}
        hass.states.get = MagicMock(return_value=state)
        entry = _make_entry()

        await _async_send_alexa(hass, entry, "Bonjour", "show")

        assert log[-1][2]["volume_level"] == ALEXA_DEFAULT_VOLUME

    async def test_no_targets_resolved_no_calls(self):
        """Keyword matching nothing → no service call at all."""
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry()

        await _async_send_alexa(hass, entry, "Bonjour", "inexistant")

        assert log == []

    async def test_tts_failure_still_restores_volume(self):
        """If the TTS call raises, the original volume is still restored."""
        log: list = []
        hass = MagicMock()
        hass.data = {}

        async def record(domain, service, data, blocking=False):
            if domain == "notify":
                raise RuntimeError("alexa_media down")
            log.append((domain, service, dict(data)))

        hass.services.async_call = AsyncMock(side_effect=record)
        state = MagicMock()
        state.state = "on"
        state.attributes = {"volume_level": 0.4}
        hass.states.get = MagicMock(return_value=state)
        entry = _make_entry()

        await _async_send_alexa(hass, entry, "Bonjour", "show")

        # set + restore both happened despite the TTS failure
        volumes = [d["volume_level"] for _, s, d in log if s == "volume_set"]
        assert volumes == [0.7, 0.4]


class TestAlexaLockSerialisation:
    """Overlapping notify calls must not capture the TTS volume as original."""

    async def test_concurrent_cycles_are_serialised(self):
        """Two concurrent sends run as full back-to-back cycles, never interleaved."""
        log: list = []
        hass = _make_hass(log, volume=0.4)
        entry = _make_entry(delay=0)

        await asyncio.gather(
            _async_send_alexa(hass, entry, "msg1", "show"),
            _async_send_alexa(hass, entry, "msg2", "show"),
        )

        services = [(d, s) for d, s, *_ in log]
        assert services == [
            ("media_player", "volume_set"),  # cycle 1: set TTS
            ("notify", "alexa_media"),       # cycle 1: TTS
            ("media_player", "volume_set"),  # cycle 1: restore
            ("media_player", "volume_set"),  # cycle 2: set TTS
            ("notify", "alexa_media"),       # cycle 2: TTS
            ("media_player", "volume_set"),  # cycle 2: restore
        ]
        # Both restores use the true original (0.4), never the TTS level (0.7)
        restores = [log[2][2]["volume_level"], log[5][2]["volume_level"]]
        assert restores == [0.4, 0.4]

    async def test_lock_is_shared_via_hass_data(self):
        """The lock is created once under hass.data[DOMAIN]."""
        log: list = []
        hass = _make_hass(log)
        entry = _make_entry()

        await _async_send_alexa(hass, entry, "msg", "show")
        lock1 = hass.data[DOMAIN][DATA_ALEXA_LOCK]
        await _async_send_alexa(hass, entry, "msg", "show")
        lock2 = hass.data[DOMAIN][DATA_ALEXA_LOCK]

        assert lock1 is lock2
        assert isinstance(lock1, asyncio.Lock)


class TestAlexaEnglish:
    """English Echo target handling."""

    async def test_missing_en_target_skips_with_warning(self, caplog):
        """No configured English target → warning, no service call."""
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        entry = MagicMock()
        entry.data = {"alexa_en_target": ""}

        await _async_send_alexa_en(hass, entry, "Good night")

        hass.services.async_call.assert_not_called()

    async def test_en_target_sends_blocking_tts(self):
        """Configured target → blocking TTS to that entity only."""
        hass = MagicMock()
        hass.services.async_call = AsyncMock()
        entry = MagicMock()
        entry.data = {"alexa_en_target": "media_player.english_echo"}

        await _async_send_alexa_en(hass, entry, "Good night")

        hass.services.async_call.assert_awaited_once()
        args, kwargs = hass.services.async_call.call_args
        assert args[0] == "notify"
        assert args[2]["target"] == ["media_player.english_echo"]
        assert kwargs.get("blocking") is True
