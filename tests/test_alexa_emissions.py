"""Tests for the in-memory Alexa emission log and its recording point."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from notification_manager.alexa_emissions import (  # noqa: E402
    AlexaEmissionLog,
    MESSAGE_SNIPPET_LEN,
)

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    from notification_manager.__init__ import (  # noqa: E402
        DATA_ALEXA_EMISSIONS,
        _async_send_alexa,
        _async_send_alexa_en,
        _get_emission_log,
    )
    from notification_manager.const import DOMAIN  # noqa: E402

LOCAL = ["media_player.kitchen_echo", "media_player.bedroom_echo"]
REMOTE = "media_player.other_house_echo"


def _make_hass(available=True):
    """hass mock whose speakers are available and whose calls are recorded."""
    hass = MagicMock()
    hass.data = {}
    hass.services.async_call = AsyncMock()
    hass.services.has_service = MagicMock(return_value=True)
    state = MagicMock()
    state.state = "on" if available else "unavailable"
    state.attributes = {"volume_level": 0.4}
    hass.states.get = MagicMock(return_value=state)
    return hass


def _make_entry(players, local_players=None, delay=8):
    entry = MagicMock()
    entry.data = {
        "alexa_players": players,
        "alexa_local_players": local_players or [],
        "alexa_tts_volume": 0.7,
        "alexa_post_tts_delay": delay,
        "alexa_en_target": "media_player.english_echo",
    }
    return entry


class TestEmissionLog:
    """Pure in-memory behaviour, no Home Assistant involved."""

    def test_record_returns_none_without_real_target(self):
        log = AlexaEmissionLog()
        assert log.record(targets=[], kind="tts_fr") is None
        assert log.record(targets=["", None], kind="tts_fr") is None
        assert len(log) == 0

    def test_speech_interval_spans_the_estimated_duration(self):
        log = AlexaEmissionLog()
        emission = log.record(
            targets=["media_player.kitchen_echo"],
            kind="tts_fr",
            speech_estimate=timedelta(seconds=8),
        )
        assert (emission.ends_at - emission.started_at) == timedelta(seconds=8)

    def test_window_matches_speech_still_running_inside_it(self):
        """Speech starting before the window but ongoing must be found.

        This is what makes correlation work for a sensor that reports an
        aggregate over a period rather than an instant value.
        """
        log = AlexaEmissionLog()
        start = datetime(2026, 8, 27, 4, 21, tzinfo=timezone.utc)
        log.record(
            targets=["media_player.kitchen_echo"],
            kind="tts_fr",
            started_at=start,
            speech_estimate=timedelta(seconds=10),
        )
        # Window opens 5s after speech began
        found = log.between(start + timedelta(seconds=5), start + timedelta(seconds=30))
        assert len(found) == 1

    def test_window_excludes_emission_fully_outside(self):
        log = AlexaEmissionLog()
        start = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)
        log.record(
            targets=["media_player.kitchen_echo"], kind="tts_fr", started_at=start
        )
        found = log.between(start + timedelta(minutes=10), start + timedelta(minutes=12))
        assert found == []

    def test_local_flag_true_only_for_local_speaker(self):
        log = AlexaEmissionLog()
        log.record(targets=[REMOTE], kind="tts_fr", local_players=LOCAL)
        log.record(targets=[LOCAL[0]], kind="tts_fr", local_players=LOCAL)

        now = datetime.now(timezone.utc)
        window = (now - timedelta(minutes=1), now + timedelta(minutes=1))
        assert len(log.between(*window)) == 2
        local_only = log.between(*window, local_only=True)
        assert len(local_only) == 1
        assert local_only[0].targets == (LOCAL[0],)

    def test_empty_local_list_flags_everything_local(self):
        """Cannot distinguish → conservative: treat as local sound."""
        log = AlexaEmissionLog()
        emission = log.record(targets=[REMOTE], kind="tts_fr", local_players=[])
        assert emission.local is True

    def test_context_ids_can_be_excluded(self):
        log = AlexaEmissionLog()
        now = datetime.now(timezone.utc)
        log.record(targets=[LOCAL[0]], kind="tts_fr", context_id="own-call")
        log.record(targets=[LOCAL[0]], kind="tts_fr", context_id="someone-else")

        window = (now - timedelta(minutes=1), now + timedelta(minutes=1))
        kept = log.between(*window, exclude_context_ids=["own-call"])
        assert [e.context_id for e in kept] == ["someone-else"]

    def test_newest_first_ordering(self):
        log = AlexaEmissionLog()
        base = datetime(2026, 8, 27, 4, 0, tzinfo=timezone.utc)
        for i in range(3):
            log.record(
                targets=[LOCAL[0]],
                kind="tts_fr",
                message=f"m{i}",
                started_at=base + timedelta(seconds=i * 10),
            )
        found = log.between(base - timedelta(minutes=1), base + timedelta(minutes=1))
        assert [e.message for e in found] == ["m2", "m1", "m0"]

    def test_message_is_truncated(self):
        log = AlexaEmissionLog()
        emission = log.record(targets=[LOCAL[0]], kind="tts_fr", message="x" * 500)
        assert len(emission.message) == MESSAGE_SNIPPET_LEN

    def test_entries_are_bounded_and_pruned(self):
        log = AlexaEmissionLog(max_entries=5, retention=timedelta(minutes=1))
        for i in range(20):
            log.record(targets=[LOCAL[0]], kind="tts_fr", message=str(i))
        assert len(log) <= 5

        old = datetime.now(timezone.utc) - timedelta(hours=2)
        log_old = AlexaEmissionLog(retention=timedelta(minutes=30))
        log_old.record(targets=[LOCAL[0]], kind="tts_fr", started_at=old)
        log_old.record(targets=[LOCAL[0]], kind="tts_fr")  # now → prunes the old one
        assert len(log_old) == 1

    def test_naive_datetimes_are_accepted(self):
        log = AlexaEmissionLog()
        naive = datetime.now()
        log.record(targets=[LOCAL[0]], kind="tts_fr", started_at=naive)
        found = log.between(naive - timedelta(minutes=1), naive + timedelta(minutes=1))
        assert len(found) == 1


class TestRecordingPoint:
    """Only real, delivered speech may be recorded."""

    async def test_fr_tts_records_resolved_targets(self):
        hass = _make_hass()
        entry = _make_entry(["media_player.kitchen_echo"], local_players=LOCAL)
        log = AlexaEmissionLog()
        hass.data[DOMAIN] = {DATA_ALEXA_EMISSIONS: log}

        await _async_send_alexa(hass, entry, "Bonjour", "kitchen", "ctx-1")

        assert len(log) == 1
        emission = log.recent(timedelta(minutes=1))[0]
        assert emission.targets == ("media_player.kitchen_echo",)
        assert emission.kind == "tts_fr"
        assert emission.context_id == "ctx-1"
        assert emission.local is True

    async def test_unresolved_target_records_nothing(self):
        """A keyword matching no speaker produces no sound and no entry."""
        hass = _make_hass()
        entry = _make_entry(["media_player.kitchen_echo"], local_players=LOCAL)
        log = AlexaEmissionLog()
        hass.data[DOMAIN] = {DATA_ALEXA_EMISSIONS: log}

        await _async_send_alexa(hass, entry, "Bonjour", "salon", "ctx-1")

        assert len(log) == 0
        hass.services.async_call.assert_not_called()

    async def test_unavailable_speaker_records_nothing(self):
        hass = _make_hass(available=False)
        entry = _make_entry(["media_player.kitchen_echo"], local_players=LOCAL)
        log = AlexaEmissionLog()
        hass.data[DOMAIN] = {DATA_ALEXA_EMISSIONS: log}

        await _async_send_alexa(hass, entry, "Bonjour", "kitchen", "ctx-1")

        assert len(log) == 0

    async def test_failed_tts_call_records_nothing(self):
        hass = _make_hass()
        hass.services.async_call = AsyncMock(side_effect=RuntimeError("boom"))
        entry = _make_entry(["media_player.kitchen_echo"], local_players=LOCAL)
        log = AlexaEmissionLog()
        hass.data[DOMAIN] = {DATA_ALEXA_EMISSIONS: log}

        await _async_send_alexa(hass, entry, "Bonjour", "kitchen", "ctx-1")

        assert len(log) == 0

    async def test_remote_only_emission_is_flagged_not_local(self):
        hass = _make_hass()
        entry = _make_entry([REMOTE], local_players=LOCAL)
        log = AlexaEmissionLog()
        hass.data[DOMAIN] = {DATA_ALEXA_EMISSIONS: log}

        await _async_send_alexa(hass, entry, "Bonjour", "other_house", "ctx-1")

        assert log.recent(timedelta(minutes=1))[0].local is False
        assert log.recent(timedelta(minutes=1), local_only=True) == []

    async def test_en_tts_is_recorded(self):
        hass = _make_hass()
        entry = _make_entry(["media_player.kitchen_echo"], local_players=LOCAL)
        log = AlexaEmissionLog()
        hass.data[DOMAIN] = {DATA_ALEXA_EMISSIONS: log}

        await _async_send_alexa_en(hass, entry, "Good night", "ctx-en")

        emission = log.recent(timedelta(minutes=1))[0]
        assert emission.kind == "tts_en"
        assert emission.targets == ("media_player.english_echo",)
        assert emission.context_id == "ctx-en"

    async def test_missing_log_does_not_break_tts(self):
        """A consumer-facing feature must never break a notification."""
        hass = _make_hass()
        entry = _make_entry(["media_player.kitchen_echo"])
        hass.data[DOMAIN] = {}

        await _async_send_alexa(hass, entry, "Bonjour", "kitchen", "ctx-1")

        assert _get_emission_log(hass) is None
        assert hass.services.async_call.await_count >= 1
