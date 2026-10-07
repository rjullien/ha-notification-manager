"""Tests for admin guard, background-task error logging, runtime config and unload."""
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager as nm
    import notification_manager.bridge_services as bridge_services
    import notification_manager.notify as notify
    from notification_manager.alexa_emissions import AlexaEmissionLog
    from notification_manager.const import DOMAIN

Unauthorized = sys.modules["homeassistant.exceptions"].Unauthorized


class TestRequireAdmin:
    """Admin-only guard on the bridge diagnostic services."""

    async def test_no_user_context_allowed(self):
        """Automations / system calls (no user_id) pass through."""
        hass = MagicMock()
        hass.auth.async_get_user = AsyncMock()
        call = MagicMock()
        call.context.user_id = None

        await bridge_services._async_require_admin(hass, call)  # must not raise

        hass.auth.async_get_user.assert_not_called()

    async def test_admin_user_allowed(self):
        hass = MagicMock()
        user = MagicMock(is_admin=True)
        hass.auth.async_get_user = AsyncMock(return_value=user)
        call = MagicMock()
        call.context.user_id = "user-1"

        await bridge_services._async_require_admin(hass, call)  # must not raise

    async def test_non_admin_user_rejected(self):
        hass = MagicMock()
        user = MagicMock(is_admin=False)
        hass.auth.async_get_user = AsyncMock(return_value=user)
        call = MagicMock()
        call.context.user_id = "user-2"

        with pytest.raises(Unauthorized):
            await bridge_services._async_require_admin(hass, call)

    async def test_unknown_user_rejected(self):
        """Stale/unknown user_id → rejected, never allowed by default."""
        hass = MagicMock()
        hass.auth.async_get_user = AsyncMock(return_value=None)
        call = MagicMock()
        call.context.user_id = "ghost"

        with pytest.raises(Unauthorized):
            await bridge_services._async_require_admin(hass, call)


class TestRunLogged:
    """Background tasks must log failures instead of raising."""

    async def test_exception_swallowed_and_logged(self, caplog):
        async def boom():
            raise ValueError("kaboom")

        with caplog.at_level(logging.ERROR):
            await notify._run_logged(boom(), "Test channel")  # must not raise

        assert "Test channel failed" in caplog.text
        assert "kaboom" in caplog.text

    async def test_success_no_log(self, caplog):
        async def fine():
            return 42

        with caplog.at_level(logging.ERROR):
            await notify._run_logged(fine(), "Test channel")

        assert "failed" not in caplog.text


class TestRuntimeConfig:
    """entry.data → runtime config precedence, incl. valid zero values."""

    def test_zero_volume_and_delay_preserved(self):
        """0.0 / 0 are valid saved values — must not fall back to defaults."""
        entry = MagicMock()
        entry.data = {"alexa_tts_volume": 0.0, "alexa_post_tts_delay": 0}

        cfg = notify._get_runtime_config(entry)

        assert cfg["alexa_tts_volume"] == 0.0
        assert cfg["alexa_post_tts_delay"] == 0

    def test_missing_values_fall_back_to_const(self):
        entry = MagicMock()
        entry.data = {}

        cfg = notify._get_runtime_config(entry)

        assert cfg["alexa_tts_volume"] == notify._CONST_ALEXA_TTS_VOLUME
        assert cfg["alexa_post_tts_delay"] == notify._CONST_ALEXA_POST_TTS_DELAY

    def test_entry_data_overrides_const(self):
        entry = MagicMock()
        entry.data = {"phone_targets": {"x": {"mobile": "notify.x"}}}

        cfg = notify._get_runtime_config(entry)

        assert cfg["phone_targets"] == {"x": {"mobile": "notify.x"}}


class TestUnloadEntry:
    """Service removal and session cleanup on unload."""

    def _make_hass(self, entries: dict):
        hass = MagicMock()
        hass.data = {DOMAIN: dict(entries)}
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        hass.services.async_remove = MagicMock()
        return hass

    async def test_last_entry_removes_services_and_closes_sessions(self):
        coordinator = MagicMock()
        coordinator.async_shutdown = AsyncMock()
        hass = self._make_hass({
            "entry1": {"coordinator": coordinator},
            nm.DATA_ALEXA_LOCK: asyncio.Lock(),  # internal key must be ignored
            nm.DATA_ALEXA_LAST_GOOD_VOLUMES: {},  # idem
            nm.DATA_ALEXA_EMISSIONS: AlexaEmissionLog(),  # idem
        })
        entry = MagicMock()
        entry.entry_id = "entry1"

        with patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()) as close:
            result = await nm.async_unload_entry(hass, entry)

        assert result is True
        coordinator.async_shutdown.assert_awaited_once()
        removed = {c.args[1] for c in hass.services.async_remove.call_args_list}
        assert removed == {
            "notify",
            "recent_alexa_emissions",
            "whatsapp_bridge_logs",
            "whatsapp_bridge_restart",
        }
        close.assert_awaited_once()
        assert DOMAIN not in hass.data

    async def test_remaining_entry_keeps_services(self):
        hass = self._make_hass({
            "entry1": {},
            "entry2": {},
        })
        entry = MagicMock()
        entry.entry_id = "entry1"

        with patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()) as close:
            result = await nm.async_unload_entry(hass, entry)

        assert result is True
        hass.services.async_remove.assert_not_called()
        close.assert_not_awaited()
        assert "entry2" in hass.data[DOMAIN]

    async def test_failed_platform_unload_keeps_entry_data(self):
        hass = self._make_hass({"entry1": {}})
        hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
        entry = MagicMock()
        entry.entry_id = "entry1"

        result = await nm.async_unload_entry(hass, entry)

        assert result is False
        assert "entry1" in hass.data[DOMAIN]


class TestMultiEntrySetup:
    """Two config entries must load; domain services register once."""

    def _make_entry(self, entry_id: str):
        entry = MagicMock()
        entry.entry_id = entry_id
        entry.data = {
            "bridge_url": f"http://bridge-{entry_id}",
            "bridge_token": "tok",
            "verify_ssl": True,
        }
        entry.async_on_unload = MagicMock()
        entry.add_update_listener = MagicMock(return_value=lambda: None)
        return entry

    def _make_hass(self):
        hass = MagicMock()
        hass.data = {}
        registered: set[tuple[str, str]] = set()

        def has_service(domain, service):
            return (domain, service) in registered

        def async_register(domain, service, handler, schema=None, supports_response=None):
            key = (domain, service)
            assert key not in registered, f"duplicate register: {key}"
            registered.add(key)

        hass.services.has_service = MagicMock(side_effect=has_service)
        hass.services.async_register = MagicMock(side_effect=async_register)
        hass.config_entries.async_forward_entry_setups = AsyncMock()
        return hass, registered

    async def test_two_entries_load_services_once(self):
        hass, registered = self._make_hass()
        entry1 = self._make_entry("entry1")
        entry2 = self._make_entry("entry2")
        watchdog = MagicMock()

        with patch.object(nm, "async_setup_watchdog", return_value=watchdog):
            assert await nm.async_setup_entry(hass, entry1) is True
            assert await nm.async_setup_entry(hass, entry2) is True

        assert "entry1" in hass.data[DOMAIN]
        assert "entry2" in hass.data[DOMAIN]
        # Per-entry resolvers, not a single domain-level clobber key
        assert nm.DATA_ALEXA_RESOLVER in hass.data[DOMAIN]["entry1"]
        assert nm.DATA_ALEXA_RESOLVER in hass.data[DOMAIN]["entry2"]
        assert nm.DATA_ALEXA_RESOLVER not in hass.data[DOMAIN]
        # Shared house-level state still at domain root
        assert nm.DATA_ALEXA_LOCK in hass.data[DOMAIN]
        assert nm.DATA_ALEXA_EMISSIONS in hass.data[DOMAIN]

        assert registered == {
            (DOMAIN, "notify"),
            (DOMAIN, "recent_alexa_emissions"),
            (DOMAIN, "whatsapp_bridge_logs"),
            (DOMAIN, "whatsapp_bridge_restart"),
        }
