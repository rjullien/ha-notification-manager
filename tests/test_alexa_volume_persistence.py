"""Tests for persistent Alexa resting volumes and post-start recovery."""
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from homeassistant.config_entries import ConfigEntryState  # noqa: E402

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager as nm
    import notification_manager.alexa as alexa
    from notification_manager.alexa import (
        DATA_ALEXA_LAST_GOOD_VOLUMES,
        DATA_ALEXA_LOCK,
        DATA_ALEXA_STORE,
        _async_boot_recovery,
        _async_send_alexa,
    )
    from notification_manager.const import (
        ALEXA_BOOT_RECOVERY_DELAY,
        ALEXA_VOLUME_SAVE_DELAY,
        DOMAIN,
        SERVICE_RESTORE_ALEXA_VOLUMES,
    )


PLAYER = "media_player.echo_show_bedroom"
SECOND_PLAYER = "media_player.echo_show_kitchen"
STRAY_PLAYER = "media_player.echo_dot_office"
Unauthorized = sys.modules["homeassistant.exceptions"].Unauthorized


class FakeStore:
    """Small stand-in for HA Store used by setup and unload tests."""

    def __init__(self, data=None):
        self.async_load = AsyncMock(return_value=data)
        self.async_save = AsyncMock()
        self.delayed_saves = []

    def async_delay_save(self, data_func, delay):
        self.delayed_saves.append((data_func, delay))


def _make_entry(
    entry_id="entry1",
    players=None,
    tts_volume=0.7,
    post_tts_delay=0,
):
    entry = MagicMock()
    entry.state = ConfigEntryState.LOADED
    entry.entry_id = entry_id
    entry.data = {
        "alexa_players": players if players is not None else [PLAYER],
        "alexa_tts_volume": tts_volume,
        "alexa_post_tts_delay": post_tts_delay,
    }
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    return entry


def _make_setup_hass(*, is_running=True):
    hass = MagicMock()
    hass.data = {}
    hass.is_running = is_running
    registered = {}

    def has_service(domain, service):
        return (domain, service) in registered

    def async_register(domain, service, handler, schema=None, supports_response=None):
        registered[(domain, service)] = {
            "handler": handler,
            "schema": schema,
        }

    hass.services.has_service = MagicMock(side_effect=has_service)
    hass.services.async_register = MagicMock(side_effect=async_register)
    hass.services.async_remove = MagicMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
    return hass, registered


class TestAlexaVolumeStore:
    async def test_setup_merges_valid_cache_once_and_purges_invalid_players(self):
        """Setup loads valid configured values once, preserving existing memory."""
        existing = {PLAYER: 0.25}
        store = FakeStore({
            "last_good": {
                PLAYER: 0.4,
                SECOND_PLAYER: 0.6,
                STRAY_PLAYER: 0.8,
                "media_player.echo_bad": "0.5",
                "media_player.echo_bool": True,
                "media_player.echo_nan": float("nan"),
                "media_player.echo_out_of_range": 1.2,
            }
        })
        hass, _registered = _make_setup_hass()
        hass.data[DOMAIN] = {DATA_ALEXA_LAST_GOOD_VOLUMES: existing}
        entry = _make_entry(players=[PLAYER, SECOND_PLAYER])

        with (
            patch.object(nm, "Store", return_value=store) as store_factory,
            patch.object(nm, "async_setup_watchdog", return_value=MagicMock()),
        ):
            assert await nm.async_setup_entry(hass, entry) is True
            assert existing == {PLAYER: 0.25, SECOND_PLAYER: 0.6}
            existing[PLAYER] = 0.2
            assert await nm.async_setup_entry(hass, entry) is True

        store_factory.assert_called_once_with(
            hass, 1, "notification_manager.alexa_volumes"
        )
        store.async_load.assert_awaited_once_with()
        assert hass.data[DOMAIN][DATA_ALEXA_STORE] is store
        assert existing == {PLAYER: 0.2, SECOND_PLAYER: 0.6}

    async def test_alexa_cycle_schedules_delayed_snapshot(self):
        """A TTS cycle schedules the current cache using the configured delay."""
        store = FakeStore()
        hass = MagicMock()
        hass.data = {DOMAIN: {DATA_ALEXA_STORE: store}}
        hass.services.has_service = MagicMock(return_value=True)
        hass.services.async_call = AsyncMock()
        state = MagicMock()
        state.state = "on"
        state.attributes = {"volume_level": 0.4}
        hass.states.get = MagicMock(return_value=state)
        entry = _make_entry()

        with (
            patch.object(alexa.asyncio, "sleep", new=AsyncMock()),
            patch.object(alexa, "async_call_later", return_value=MagicMock()),
        ):
            await _async_send_alexa(hass, entry, "Bonjour", "show")

        assert len(store.delayed_saves) == 1
        snapshot, delay = store.delayed_saves[0]
        assert delay == ALEXA_VOLUME_SAVE_DELAY == 10
        assert snapshot() == {"last_good": {PLAYER: 0.4}}


class TestRestoreAlexaVolumesService:
    async def _register_service(self, hass, entry, store):
        registered = {}

        def has_service(domain, service):
            return (domain, service) in registered

        def async_register(domain, service, handler, schema=None, supports_response=None):
            registered[(domain, service)] = {
                "handler": handler,
                "schema": schema,
            }

        hass.services.has_service = MagicMock(side_effect=has_service)
        hass.services.async_register = MagicMock(side_effect=async_register)
        hass.services.async_remove = MagicMock()
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        hass.config_entries.async_get_entry = MagicMock(return_value=entry)
        with (
            patch.object(nm, "Store", return_value=store),
            patch.object(nm, "async_setup_watchdog", return_value=MagicMock()),
        ):
            await nm.async_setup_entry(hass, entry)
        return registered

    async def test_service_restores_only_available_players_under_shared_lock(self):
        """The admin service restores available players while holding the lock."""
        hass, _registered = _make_setup_hass()
        entry = _make_entry(players=[PLAYER, SECOND_PLAYER])
        registered = await self._register_service(hass, entry, FakeStore())
        lock = hass.data[DOMAIN][DATA_ALEXA_LOCK]
        hass.data[DOMAIN][DATA_ALEXA_LAST_GOOD_VOLUMES] = {
            PLAYER: 0.3,
            SECOND_PLAYER: 0.5,
            STRAY_PLAYER: 0.6,
            "media_player.echo_missing": 0.8,
        }
        states = {}
        available = MagicMock()
        available.state = "on"
        available.attributes = {"volume_level": 0.7}
        unknown = MagicMock()
        unknown.state = "unknown"
        unknown.attributes = {"volume_level": 0.7}
        unavailable = MagicMock()
        unavailable.state = "unavailable"
        unavailable.attributes = {"volume_level": 0.7}
        states[PLAYER] = available
        states[SECOND_PLAYER] = unknown
        states[STRAY_PLAYER] = unavailable
        hass.states.get = MagicMock(side_effect=lambda entity_id: states.get(entity_id))
        call = MagicMock()
        call.context.user_id = None
        lock_observations = []

        async def record_set_volume(current_hass, entity_id, volume):
            lock_observations.append(lock.locked())
            return True

        with patch.object(
            alexa,
            "_async_set_volume",
            new=AsyncMock(side_effect=record_set_volume),
        ) as set_volume:
            await registered[(DOMAIN, SERVICE_RESTORE_ALEXA_VOLUMES)]["handler"](call)

        set_volume.assert_awaited_once_with(hass, PLAYER, 0.3)
        assert lock_observations == [True]

    async def test_service_rejects_non_admin_user(self):
        """The restore service applies the shared administrator guard."""
        hass, _registered = _make_setup_hass()
        entry = _make_entry()
        registered = await self._register_service(hass, entry, FakeStore())
        hass.auth.async_get_user = AsyncMock(return_value=MagicMock(is_admin=False))
        call = MagicMock()
        call.context.user_id = "non-admin"

        with pytest.raises(Unauthorized):
            await registered[(DOMAIN, SERVICE_RESTORE_ALEXA_VOLUMES)]["handler"](
                call
            )


class TestAlexaBootRecovery:
    @pytest.mark.parametrize(
        ("player_state", "reading", "resting", "should_restore"),
        [
            ("on", 0.7, 0.4, True),
            ("on", 0.6, 0.4, False),
            ("on", 0.7, 0.7, False),
            ("unavailable", 0.7, 0.4, False),
            ("unknown", 0.7, 0.4, False),
            ("on", "invalid", 0.4, False),
        ],
    )
    async def test_recovery_only_corrects_players_still_at_tts(
        self,
        player_state,
        reading,
        resting,
        should_restore,
    ):
        """Recovery only corrects available players still at TTS volume."""
        state = MagicMock()
        state.state = player_state
        state.attributes = {"volume_level": reading}
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                DATA_ALEXA_LOCK: asyncio.Lock(),
                DATA_ALEXA_LAST_GOOD_VOLUMES: {PLAYER: resting},
            }
        }
        hass.states.get = MagicMock(return_value=state)
        entry = _make_entry(tts_volume=0.7)

        with patch.object(
            alexa,
            "_async_set_volume",
            new=AsyncMock(return_value=True),
        ) as set_volume:
            await _async_boot_recovery(hass, entry)

        if should_restore:
            set_volume.assert_awaited_once_with(hass, PLAYER, resting)
        else:
            set_volume.assert_not_awaited()

    async def test_recovery_swallows_state_lookup_errors(self, caplog):
        """A recovery failure is logged without escaping the startup callback."""
        hass = MagicMock()
        hass.data = {
            DOMAIN: {
                DATA_ALEXA_LOCK: asyncio.Lock(),
                DATA_ALEXA_LAST_GOOD_VOLUMES: {PLAYER: 0.4},
            }
        }
        hass.states.get = MagicMock(side_effect=RuntimeError("state lookup failed"))

        with caplog.at_level(logging.WARNING):
            await _async_boot_recovery(hass, _make_entry())

        assert "Alexa boot volume recovery failed" in caplog.text
        assert "state lookup failed" in caplog.text

    @pytest.mark.parametrize("is_running", [True, False])
    async def test_started_listener_and_timer_follow_entry_lifecycle(self, is_running):
        """A boot listener is first-setup-only and its listener/timer are unloadable."""
        hass, _registered = _make_setup_hass(is_running=is_running)
        entry = _make_entry()
        store = FakeStore()
        timer_cancel = MagicMock()

        with (
            patch.object(nm, "Store", return_value=store),
            patch.object(nm, "async_setup_watchdog", return_value=MagicMock()),
        ):
            assert await nm.async_setup_entry(hass, entry) is True

        if is_running:
            hass.bus.async_listen_once.assert_not_called()
            return

        hass.bus.async_listen_once.assert_called_once()
        event, callback = hass.bus.async_listen_once.call_args.args
        assert event == "homeassistant_started"
        entry.async_on_unload.assert_any_call(hass.bus.async_listen_once.return_value)

        with patch.object(nm, "async_call_later", return_value=timer_cancel) as later:
            callback(MagicMock())

        later.assert_called_once()
        scheduled_hass, delay, recovery = later.call_args.args
        assert scheduled_hass is hass
        assert delay == ALEXA_BOOT_RECOVERY_DELAY == 60
        assert recovery.func is _async_boot_recovery
        assert recovery.args == (hass, entry)
        entry.async_on_unload.assert_any_call(timer_cancel)
