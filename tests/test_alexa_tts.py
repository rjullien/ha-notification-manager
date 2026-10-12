"""Tests for the Alexa TTS flow — lock serialisation and volume save/restore."""
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    from notification_manager.alexa import (
        DATA_ALEXA_LAST_GOOD_VOLUMES,
        DATA_ALEXA_GENERATIONS,
        DATA_ALEXA_LOCK,
        DATA_ALEXA_RERESTORE_UNSUBS,
        _async_rerestore,
        _async_restore_volumes,
        _async_send_alexa,
        _async_send_alexa_en,
        _make_alexa_resolver,
        _resolve_restore_volume,
        _volumes_close,
    )
    from notification_manager.const import (
        ALEXA_DEFAULT_VOLUME,
        ALEXA_MIN_POST_TTS_DELAY,
        ALEXA_RERESTORE_DELAY,
        ALEXA_RESTORE_RETRY_DELAY,
        DOMAIN,
    )
    import notification_manager.alexa as alexa


PLAYER = "media_player.echo_show_bedroom"


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


def _make_hass_with_alexa_state_lag(
    call_log: list,
    initial_volume: float = 0.4,
    tts_volume: float = 0.7,
    *,
    fail_restore_once: bool = False,
):
    """Hass mock whose ``volume_level`` lags like Alexa Media after restore.

    Raising to TTS updates the attribute immediately. A restore ``volume_set``
    is recorded but does *not* refresh the attribute — matching the real
    integration lag that left speakers stuck loud across back-to-back cycles.
    """
    hass = MagicMock()
    hass.data = {}
    attrs = {"volume_level": initial_volume}
    state = MagicMock()
    state.state = "on"
    state.attributes = attrs
    restore_failures_left = 1 if fail_restore_once else 0

    async def record(domain, service, data, blocking=False):
        nonlocal restore_failures_left
        if domain == "media_player" and service == "volume_set":
            vol = float(data["volume_level"])
            if (
                restore_failures_left
                and not _volumes_close(vol, tts_volume)
            ):
                restore_failures_left -= 1
                raise RuntimeError("alexa volume restore timeout")
            call_log.append((domain, service, dict(data), blocking))
            if _volumes_close(vol, tts_volume):
                attrs["volume_level"] = vol
            # Restore: leave attrs at TTS level (stale HA state).
            return
        call_log.append((domain, service, dict(data), blocking))

    hass.services.async_call = AsyncMock(side_effect=record)
    hass.states.get = MagicMock(return_value=state)
    return hass, attrs


def _make_entry(players=None, tts_volume=0.7, delay=0):
    entry = MagicMock()
    entry.data = {
        "alexa_players": players or [PLAYER],
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
        """Non-numeric volume_level uses default and is not cached."""
        log: list = []
        hass = _make_hass(log)
        state = MagicMock()
        state.state = "on"
        state.attributes = {"volume_level": "garbage"}
        hass.states.get = MagicMock(return_value=state)
        entry = _make_entry()

        await _async_send_alexa(hass, entry, "Bonjour", "show")

        assert log[-1][2]["volume_level"] == ALEXA_DEFAULT_VOLUME
        assert hass.data[DOMAIN][DATA_ALEXA_LAST_GOOD_VOLUMES] == {}

    async def test_invalid_volume_attribute_does_not_replace_last_good(self):
        """An invalid live reading leaves the remembered resting value intact."""
        log: list = []
        hass = _make_hass(log)
        state = MagicMock()
        state.state = "on"
        state.attributes = {"volume_level": "garbage"}
        hass.states.get = MagicMock(return_value=state)
        remembered = {PLAYER: 0.3}
        hass.data[DOMAIN] = {DATA_ALEXA_LAST_GOOD_VOLUMES: remembered}

        await _async_send_alexa(hass, _make_entry(), "Bonjour", "show")

        assert remembered == {PLAYER: 0.3}
        assert log[-1][2]["volume_level"] == 0.3

    async def test_unknown_player_is_filtered_from_tts_targets(self):
        """Unknown entities are omitted while available targets still receive TTS."""
        log: list = []
        hass = _make_hass(log)
        players = [PLAYER, "media_player.echo_show_kitchen"]
        unknown = MagicMock(state="unknown", attributes={"volume_level": 0.4})
        available = MagicMock(state="on", attributes={"volume_level": 0.4})
        states = {players[0]: unknown, players[1]: available}
        hass.states.get = MagicMock(side_effect=states.get)

        await _async_send_alexa(
            hass, _make_entry(players=players), "Bonjour", "show"
        )

        tts_call = next(call for call in log if call[0] == "notify")
        assert tts_call[2]["target"] == [players[1]]

    async def test_all_unknown_players_skip_tts(self):
        """No TTS is sent when every matching player is unknown."""
        log: list = []
        hass = _make_hass(log)
        state = MagicMock(state="unknown", attributes={"volume_level": 0.4})
        hass.states.get = MagicMock(return_value=state)

        await _async_send_alexa(hass, _make_entry(), "Bonjour", "show")

        assert log == []

    async def test_zero_delay_uses_two_second_floor(self):
        """A saved zero delay cannot restore before speech has time to finish."""
        hass = _make_hass([])
        with patch.object(alexa.asyncio, "sleep", new=AsyncMock()) as sleep:
            await _async_send_alexa(hass, _make_entry(delay=0), "Bonjour", "show")

        sleep.assert_awaited_once_with(ALEXA_MIN_POST_TTS_DELAY)

    async def test_restore_retries_concurrently_after_one_second(self):
        """All targets start restore together and a failed one retries after a pause."""
        hass = _make_hass([])
        players = [PLAYER, "media_player.echo_show_kitchen"]
        attempts: dict[str, int] = {}
        call_order: list[tuple[str, int]] = []
        first_attempts_started = asyncio.Event()

        async def set_volume(_hass, entity_id, _volume):
            attempts[entity_id] = attempts.get(entity_id, 0) + 1
            attempt = attempts[entity_id]
            call_order.append((entity_id, attempt))
            if attempt == 1:
                if len(attempts) == len(players):
                    first_attempts_started.set()
                await first_attempts_started.wait()
                return entity_id != PLAYER
            return True

        with patch.object(
            alexa, "_async_set_volume", new=AsyncMock(side_effect=set_volume)
        ) as set_volume_mock, patch.object(
            alexa.asyncio, "sleep", new=AsyncMock()
        ) as sleep:
            await asyncio.wait_for(
                _async_restore_volumes(hass, {player: 0.4 for player in players}),
                timeout=1,
            )

        assert call_order == [(PLAYER, 1), (players[1], 1), (PLAYER, 2)]
        assert {call.args[1] for call in set_volume_mock.await_args_list} == set(players)
        sleep.assert_awaited_once_with(ALEXA_RESTORE_RETRY_DELAY)

    async def test_cancellation_during_sleep_restores_all_targets_without_rerestore(self):
        """Cancellation still restores each target and never schedules a retry."""
        log: list = []
        hass = _make_hass(log)
        players = [PLAYER, "media_player.echo_show_kitchen"]

        with patch.object(
            alexa.asyncio,
            "sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ), patch.object(alexa, "async_call_later") as call_later:
            with pytest.raises(asyncio.CancelledError):
                await _async_send_alexa(
                    hass, _make_entry(players=players), "Bonjour", "show"
                )

        restores = {
            data["entity_id"]: data["volume_level"]
            for domain, service, data, *_ in log
            if domain == "media_player" and service == "volume_set"
            and data["volume_level"] == 0.4
        }
        assert restores == {player: 0.4 for player in players}
        call_later.assert_not_called()

    async def test_rerestore_scheduled_per_target_and_cancelled_by_next_cycle(self):
        """A later cycle cancels each pending job and advances its generation."""
        log: list = []
        hass = _make_hass(log)
        players = [PLAYER, "media_player.echo_show_kitchen"]
        entry = _make_entry(players=players)
        scheduled_unsubs = [MagicMock() for _ in range(4)]

        with patch.object(alexa.asyncio, "sleep", new=AsyncMock()), patch.object(
            alexa,
            "async_call_later",
            side_effect=scheduled_unsubs,
        ) as call_later:
            await _async_send_alexa(hass, entry, "First", "show")
            assert call_later.call_count == len(players)
            assert all(
                call.args[1] == ALEXA_RERESTORE_DELAY
                for call in call_later.call_args_list
            )

            await _async_send_alexa(hass, entry, "Second", "show")

        for unsubscribe in scheduled_unsubs[:2]:
            unsubscribe.assert_called_once_with()
        assert call_later.call_count == 2 * len(players)
        assert hass.data[DOMAIN][DATA_ALEXA_GENERATIONS] == {
            player: 2 for player in players
        }
        assert all(
            hass.data[DOMAIN][DATA_ALEXA_RERESTORE_UNSUBS][player]
            is scheduled_unsubs[index + 2]
            for index, player in enumerate(players)
        )

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


class TestAlexaStaleStateRace:
    """Gap the lock alone missed: HA state still shows TTS after restore."""

    async def test_back_to_back_cycles_ignore_stale_tts_attribute(self):
        """Second cycle must not save TTS as original when state lags."""
        log: list = []
        hass, attrs = _make_hass_with_alexa_state_lag(log)
        entry = _make_entry(delay=0)

        await _async_send_alexa(hass, entry, "msg1", "show")
        # After cycle 1 restore, HA still reports TTS — the bug trigger.
        assert attrs["volume_level"] == 0.7

        await _async_send_alexa(hass, entry, "msg2", "show")

        restores = [
            d["volume_level"]
            for _, s, d, *_ in log
            if s == "volume_set" and d["volume_level"] != 0.7
        ]
        assert restores == [0.4, 0.4]
        assert hass.data[DOMAIN][DATA_ALEXA_LAST_GOOD_VOLUMES][PLAYER] == 0.4

    async def test_concurrent_cycles_with_stale_state_restore_true_original(self):
        """Overlapping calls + Alexa lag still restore to the resting volume."""
        log: list = []
        hass, _attrs = _make_hass_with_alexa_state_lag(log)
        entry = _make_entry(delay=0)

        await asyncio.gather(
            _async_send_alexa(hass, entry, "msg1", "show"),
            _async_send_alexa(hass, entry, "msg2", "show"),
        )

        restores = [
            d["volume_level"]
            for _, s, d, *_ in log
            if s == "volume_set" and not _volumes_close(d["volume_level"], 0.7)
        ]
        assert restores == [0.4, 0.4]

    async def test_failed_restore_still_uses_last_good_next_cycle(self):
        """A failed restore leaves the device loud; next cycle must heal it."""
        log: list = []
        hass, attrs = _make_hass_with_alexa_state_lag(log, fail_restore_once=True)
        entry = _make_entry(delay=0)

        await _async_send_alexa(hass, entry, "msg1", "show")
        # First restore attempt failed; attribute still at TTS.
        assert attrs["volume_level"] == 0.7

        await _async_send_alexa(hass, entry, "msg2", "show")

        restores = [
            d["volume_level"]
            for _, s, d, *_ in log
            if s == "volume_set" and not _volumes_close(d["volume_level"], 0.7)
        ]
        # Cycle 1: one successful retry restore (or the retry after fail).
        # Cycle 2: restore from last-known-good, not from stale TTS attribute.
        assert 0.4 in restores
        assert restores[-1] == 0.4
        assert all(v == 0.4 for v in restores)

    async def test_first_cycle_at_tts_volume_keeps_reported(self):
        """If the user already rests at TTS volume, do not invent another level."""
        log: list = []
        hass = _make_hass(log, volume=0.7)
        entry = _make_entry()

        await _async_send_alexa(hass, entry, "Bonjour", "show")

        volumes = [d["volume_level"] for _, s, d, *_ in log if s == "volume_set"]
        assert volumes == [0.7, 0.7]


class TestResolveRestoreVolume:
    """Unit tests for the last-known-good heuristic."""

    def test_trusts_live_volume_when_not_tts(self):
        last_good: dict[str, float] = {}
        assert _resolve_restore_volume(PLAYER, 0.35, 0.7, last_good) == 0.35
        assert last_good[PLAYER] == 0.35

    def test_prefers_last_good_when_live_equals_tts(self):
        last_good = {PLAYER: 0.4}
        assert _resolve_restore_volume(PLAYER, 0.7, 0.7, last_good) == 0.4

    def test_missing_live_uses_last_good_then_default(self):
        assert _resolve_restore_volume(PLAYER, None, 0.7, {PLAYER: 0.3}) == 0.3
        assert _resolve_restore_volume(PLAYER, None, 0.7, {}) == ALEXA_DEFAULT_VOLUME


class TestAlexaResolverAvailability:
    """The public Alexa resolver uses the same unknown-state filter as TTS."""

    def test_available_only_drops_unknown_player(self):
        hass = MagicMock()
        state = MagicMock(state="unknown")
        hass.states.get = MagicMock(return_value=state)
        entry = _make_entry()
        resolver = _make_alexa_resolver(hass, entry)

        assert resolver("show") == []
        assert resolver("show", available_only=False) == [PLAYER]


class TestAlexaRerestore:
    """Generation-guarded correction after Alexa Media's delayed state update."""

    @pytest.mark.parametrize(
        (
            "state_name",
            "reported",
            "stored_generation",
            "expected_restore",
            "expected_warning",
        ),
        [
            ("on", 0.7, 4, True, True),
            ("on", 0.4, 4, False, False),
            ("on", 0.7, 5, False, False),
            ("on", 0.55, 4, False, False),
            ("unavailable", 0.7, 4, False, False),
            ("unknown", 0.7, 4, False, False),
            ("on", "invalid", 4, False, False),
            (None, 0.7, 4, False, False),
        ],
    )
    async def test_rerestore_only_corrects_unchanged_tts_volume(
        self,
        state_name,
        reported,
        stored_generation,
        expected_restore,
        expected_warning,
        caplog,
    ):
        hass = MagicMock()
        pending_unsubscribe = MagicMock()
        state = None
        if state_name is not None:
            state = MagicMock(
                state=state_name,
                attributes={"volume_level": reported},
            )
        hass.states.get = MagicMock(return_value=state)
        hass.data = {
            DOMAIN: {
                DATA_ALEXA_LOCK: asyncio.Lock(),
                DATA_ALEXA_GENERATIONS: {PLAYER: stored_generation},
                DATA_ALEXA_RERESTORE_UNSUBS: {PLAYER: pending_unsubscribe},
            }
        }

        with patch.object(alexa, "_async_set_volume", new=AsyncMock()) as set_volume:
            with caplog.at_level(logging.WARNING):
                await _async_rerestore(
                    hass, PLAYER, 0.4, generation=4, tts_volume=0.7
                )

        if expected_restore:
            set_volume.assert_awaited_once_with(hass, PLAYER, 0.4)
        else:
            set_volume.assert_not_awaited()
        assert ("still at TTS volume" in caplog.text) is expected_warning
        if stored_generation == 4:
            assert PLAYER not in hass.data[DOMAIN][DATA_ALEXA_RERESTORE_UNSUBS]
        else:
            assert hass.data[DOMAIN][DATA_ALEXA_RERESTORE_UNSUBS][PLAYER] is pending_unsubscribe


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
