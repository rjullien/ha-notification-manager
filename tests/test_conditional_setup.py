"""Regression tests for configuration-dependent channel setup."""
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager as nm
    import notification_manager.alexa as alexa
    import notification_manager.bridge_services as bridge_services
    import notification_manager.notify as notify
    import notification_manager.runtime as runtime
    import notification_manager.watchdog as watchdog
    from notification_manager.const import (
        DEFAULT_BRIDGE_URL,
        DOMAIN,
        SENSOR_BRIDGE_STATUS_KEY,
    )

HomeAssistantError = sys.modules["homeassistant.exceptions"].HomeAssistantError


def _make_entry(entry_id="entry1", bridge_url="http://bridge:8080", **data):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.data = {"bridge_url": bridge_url, **data}
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=MagicMock())
    return entry


def _make_hass(*, is_running=True):
    hass = MagicMock()
    hass.data = {}
    hass.is_running = is_running
    registered = {}

    def has_service(domain, service):
        return (domain, service) in registered

    def register(domain, service, handler, schema=None, supports_response=None):
        registered[(domain, service)] = handler

    hass.services.has_service = MagicMock(side_effect=has_service)
    hass.services.async_register = MagicMock(side_effect=register)
    hass.services.async_remove = MagicMock()
    hass.services.async_call = AsyncMock()
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    hass.config_entries.async_get_entry = MagicMock()
    hass.bus.async_listen_once = MagicMock(return_value=MagicMock())
    hass.auth.async_get_user = AsyncMock()
    return hass, registered


class TestBridgeConfiguration:
    @pytest.mark.parametrize(
        ("token", "expected"),
        [
            ("", {}),
            ("   ", {}),
            ("secret", {"Authorization": "Bearer secret"}),
        ],
    )
    def test_bridge_headers_only_include_nonempty_token(self, token, expected):
        assert runtime._bridge_headers(token) == expected

    @pytest.mark.parametrize("bridge_url", ["", DEFAULT_BRIDGE_URL])
    async def test_unconfigured_bridge_skips_platform_and_removes_stale_sensor(
        self, bridge_url, caplog
    ):
        hass, _registered = _make_hass()
        entry = _make_entry(bridge_url=bridge_url)
        registry = MagicMock()
        registry.async_get_entity_id.return_value = "sensor.notification_manager_whatsapp_status"

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", []),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(nm.er, "async_get", return_value=registry),
            patch.object(nm, "Store") as store,
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
            caplog.at_level(logging.INFO),
        ):
            assert await nm.async_setup_entry(hass, entry) is True

            assert hass.data[DOMAIN][entry.entry_id]["platforms"] == []
            assert hass.data[DOMAIN][entry.entry_id]["bridge_url"] == ""
            hass.config_entries.async_forward_entry_setups.assert_not_awaited()
            store.assert_not_called()
            hass.bus.async_listen_once.assert_not_called()
            registry.async_get_entity_id.assert_called_once_with(
                "sensor", DOMAIN, f"{entry.entry_id}_{SENSOR_BRIDGE_STATUS_KEY}"
            )
            registry.async_remove.assert_called_once_with(
                "sensor.notification_manager_whatsapp_status"
            )
            assert caplog.text.count("Removed stale WhatsApp bridge status sensor") == 1

            assert await nm.async_unload_entry(hass, entry) is True

        hass.config_entries.async_unload_platforms.assert_not_awaited()

    async def test_configured_bridge_forwards_and_unloads_sensor_platform(self):
        hass, _registered = _make_hass()
        entry = _make_entry(bridge_url=" https://bridge.example ")
        registry = MagicMock()

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", []),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(nm.er, "async_get", return_value=registry),
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
        ):
            assert await nm.async_setup_entry(hass, entry) is True
            assert hass.data[DOMAIN][entry.entry_id]["platforms"] == ["sensor"]
            assert hass.data[DOMAIN][entry.entry_id]["bridge_url"] == "https://bridge.example"
            hass.config_entries.async_forward_entry_setups.assert_awaited_once_with(
                entry, ["sensor"]
            )
            registry.async_remove.assert_not_called()

            assert await nm.async_unload_entry(hass, entry) is True

        hass.config_entries.async_unload_platforms.assert_awaited_once_with(
            entry, ["sensor"]
        )

    @pytest.mark.parametrize(
        ("bridge_url", "expected"),
        [
            ("", ""),
            ("  ", ""),
            (DEFAULT_BRIDGE_URL, ""),
            (f" {DEFAULT_BRIDGE_URL} ", ""),
            (" https://bridge.example ", "https://bridge.example"),
        ],
    )
    def test_bridge_url_normalization(self, bridge_url, expected):
        entry = _make_entry(bridge_url=bridge_url)

        assert runtime._entry_bridge_url(entry) == expected


class TestAlexaConditionalSetup:
    async def test_no_players_skips_store_and_started_listener(self):
        hass, _registered = _make_hass(is_running=False)
        entry = _make_entry(alexa_players=[])

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", []),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(nm, "Store") as store,
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
        ):
            assert await nm.async_setup_entry(hass, entry) is True

        store.assert_not_called()
        hass.bus.async_listen_once.assert_not_called()
        assert nm.DATA_ALEXA_STORE not in hass.data[DOMAIN]
        assert nm.DATA_ALEXA_LOCK in hass.data[DOMAIN]
        assert nm.DATA_ALEXA_EMISSIONS in hass.data[DOMAIN]

    async def test_players_load_store_and_register_started_listener(self):
        hass, _registered = _make_hass(is_running=False)
        entry = _make_entry(alexa_players=["media_player.echo"])
        store = MagicMock()
        store.async_load = AsyncMock(return_value=None)

        with (
            patch.object(nm, "Store", return_value=store) as store_factory,
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
        ):
            assert await nm.async_setup_entry(hass, entry) is True

        store_factory.assert_called_once_with(
            hass, 1, "notification_manager.alexa_volumes"
        )
        store.async_load.assert_awaited_once_with()
        assert hass.data[DOMAIN][nm.DATA_ALEXA_STORE] is store
        hass.bus.async_listen_once.assert_called_once()

    async def test_reload_after_adding_players_creates_store(self):
        hass, _registered = _make_hass(is_running=False)
        entry = _make_entry(alexa_players=[])

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", []),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(nm, "Store") as store_factory,
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
        ):
            assert await nm.async_setup_entry(hass, entry) is True
            store_factory.assert_not_called()
            hass.bus.async_listen_once.assert_not_called()

            assert await nm.async_unload_entry(hass, entry) is True
            entry.data["alexa_players"] = ["media_player.echo"]
            store = MagicMock()
            store.async_load = AsyncMock(return_value=None)
            store_factory.return_value = store

            assert await nm.async_setup_entry(hass, entry) is True

        store_factory.assert_called_once_with(
            hass, 1, "notification_manager.alexa_volumes"
        )
        store.async_load.assert_awaited_once_with()
        hass.bus.async_listen_once.assert_called_once()

    async def test_no_store_or_players_makes_restore_service_a_noop(self):
        hass, registered = _make_hass()
        entry = _make_entry(alexa_players=[])

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", []),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(nm, "Store") as store_factory,
            patch.object(
                nm, "_async_restore_saved_volumes", new=AsyncMock()
            ) as restore,
            patch.object(alexa, "_async_set_volume", new=AsyncMock()) as set_volume,
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
        ):
            assert await nm.async_setup_entry(hass, entry) is True
            call = MagicMock()
            call.context.user_id = None
            await registered[(DOMAIN, "restore_alexa_volumes")](call)

        store_factory.assert_not_called()
        restore.assert_not_awaited()
        set_volume.assert_not_awaited()
        hass.services.async_call.assert_not_awaited()

    async def test_no_players_skips_stale_cached_volume_restore(self):
        hass, registered = _make_hass()
        entry = _make_entry(alexa_players=[])
        hass.config_entries.async_get_entry.return_value = entry

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", []),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(nm, "Store") as store_factory,
            patch.object(
                nm, "_async_restore_saved_volumes", new=AsyncMock()
            ) as restore,
            patch.object(alexa, "_async_set_volume", new=AsyncMock()) as set_volume,
            patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()),
        ):
            assert await nm.async_setup_entry(hass, entry) is True
            hass.data[DOMAIN][nm.DATA_ALEXA_STORE] = MagicMock()
            hass.data[DOMAIN][nm.DATA_ALEXA_LAST_GOOD_VOLUMES] = {
                "media_player.stale": 0.4
            }
            call = MagicMock()
            call.context.user_id = None
            await registered[(DOMAIN, "restore_alexa_volumes")](call)

        store_factory.assert_not_called()
        restore.assert_not_awaited()
        set_volume.assert_not_awaited()

class TestNotifyWithoutConfiguredChannels:
    async def test_no_alexa_entities_does_not_create_tts_task(self):
        hass, _registered = _make_hass()
        entry = _make_entry(alexa_players=[], alexa_local_players=[])
        entry.async_create_task = MagicMock()
        entry.async_create_background_task = MagicMock()
        call = MagicMock()
        call.context.id = "context"
        call.data = {
            "message_alexa": "bonjour",
            "notification_alexa": "all",
        }

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", []),
            patch.object(runtime, "_CONST_ALEXA_LOCAL_PLAYERS", []),
            patch.object(alexa, "_async_send_alexa", new=AsyncMock()) as send_alexa,
        ):
            await notify._async_handle_notify(hass, entry, call)

        entry.async_create_task.assert_not_called()
        entry.async_create_background_task.assert_not_called()
        send_alexa.assert_not_awaited()
        assert nm.DATA_ALEXA_LOCK not in hass.data.get(DOMAIN, {})
        hass.services.async_call.assert_not_awaited()

    async def test_local_alexa_players_preserve_task_scheduling(self):
        hass, _registered = _make_hass()
        entry = _make_entry(alexa_players=[], alexa_local_players=["media_player.echo"])
        tasks = []

        def create_task(_hass, coro, _name=None, **_kwargs):
            task = asyncio.create_task(coro)
            tasks.append(task)
            return task

        entry.async_create_task = MagicMock(side_effect=create_task)
        entry.async_create_background_task = MagicMock(side_effect=create_task)
        call = MagicMock()
        call.data = {
            "message_alexa": "bonjour",
            "notification_alexa": "all",
        }

        with (
            patch.object(runtime, "_CONST_ALEXA_PLAYERS", []),
            patch.object(
                runtime, "_CONST_ALEXA_LOCAL_PLAYERS", ["media_player.echo"]
            ),
            patch.object(alexa, "_async_send_alexa", new=AsyncMock()) as send_alexa,
        ):
            await notify._async_handle_notify(hass, entry, call)
            await asyncio.gather(*tasks)

        send_alexa.assert_awaited_once()

    async def test_empty_english_target_does_not_create_delayed_task(self):
        hass, _registered = _make_hass()
        entry = _make_entry(alexa_en_target="")
        entry.async_create_task = MagicMock()
        entry.async_create_background_task = MagicMock()
        call = MagicMock()
        call.data = {"message_alexa_en": "good night"}

        with (
            patch.object(runtime, "_CONST_ALEXA_EN_TARGET", ""),
            patch.object(alexa, "_async_send_alexa_en_delayed", new=AsyncMock()) as send_en,
        ):
            await notify._async_handle_notify(hass, entry, call)

        entry.async_create_task.assert_not_called()
        entry.async_create_background_task.assert_not_called()
        send_en.assert_not_awaited()

    async def test_whatsapp_without_bridge_skips_http_and_warns(self, caplog):
        hass, _registered = _make_hass()
        entry = _make_entry(bridge_url="")
        entry.async_create_task = MagicMock()
        entry.async_create_background_task = MagicMock()
        call = MagicMock()
        call.data = {
            "message_tel": "hello",
            "notification_tel": "none",
            "notification_whatsapp": "alice",
        }

        with (
            patch.object(notify.messaging_mod, "async_get_bridge_session") as get_session,
            patch.object(
                notify.messaging_mod, "_async_send_whatsapp", new=AsyncMock()
            ) as send_whatsapp,
            caplog.at_level(logging.WARNING),
        ):
            await notify._async_handle_notify(hass, entry, call)

        entry.async_create_task.assert_not_called()
        send_whatsapp.assert_not_awaited()
        get_session.assert_not_called()
        assert (
            "WhatsApp requested (alice) but no bridge_url configured — skipped"
            in caplog.text
        )


class TestBridgeServicesWithoutBridge:
    @pytest.mark.parametrize("service_name", ["whatsapp_bridge_logs", "whatsapp_bridge_restart"])
    async def test_bridge_service_raises_home_assistant_error(self, service_name):
        hass, registered = _make_hass()
        entry = _make_entry(bridge_url="")
        hass.data[DOMAIN] = {entry.entry_id: {}}
        hass.config_entries.async_get_entry.return_value = entry
        bridge_services.async_register_bridge_services(hass, entry)
        call = MagicMock()
        call.context.user_id = None
        call.data = {}

        with pytest.raises(HomeAssistantError, match="WhatsApp bridge not configured"):
            await registered[(DOMAIN, service_name)](call)

    async def test_bridge_logs_omits_authorization_for_empty_token(self):
        hass, registered = _make_hass()
        entry = _make_entry(bridge_token="")
        hass.data[DOMAIN] = {entry.entry_id: {}}
        hass.config_entries.async_get_entry.return_value = entry
        bridge_services.async_register_bridge_services(hass, entry)

        response = MagicMock(status=200)
        response.json = AsyncMock(return_value={"logs": []})
        request = MagicMock()
        request.__aenter__ = AsyncMock(return_value=response)
        request.__aexit__ = AsyncMock(return_value=False)
        session = MagicMock()
        session.get = MagicMock(return_value=request)
        call = MagicMock()
        call.context.user_id = None
        call.data = {}

        with patch.object(
            bridge_services, "async_get_bridge_session", return_value=session
        ):
            await registered[(DOMAIN, "whatsapp_bridge_logs")](call)

        assert session.get.call_args.kwargs["headers"] == {}


class TestWatchdogConditionalSetup:
    def test_empty_watchdog_lists_allocate_no_object(self):
        with (
            patch.object(watchdog, "WATCHDOG_ENTITIES", []),
            patch.object(watchdog, "WATCHDOG_CRITICAL_ENTITIES", []),
            patch.object(watchdog, "async_track_time_interval") as track_interval,
        ):
            assert watchdog.async_setup_watchdog(MagicMock(), MagicMock()) is None

        track_interval.assert_not_called()

    def test_nonempty_watchdog_list_starts_watchdog(self):
        with (
            patch.object(watchdog, "WATCHDOG_ENTITIES", ["sensor.example"]),
            patch.object(watchdog, "WATCHDOG_CRITICAL_ENTITIES", []),
            patch.object(watchdog, "async_track_time_interval") as track_interval,
        ):
            result = watchdog.async_setup_watchdog(MagicMock(), MagicMock())

        assert isinstance(result, watchdog.EntityWatchdog)
        track_interval.assert_called_once()
