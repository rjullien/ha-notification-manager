"""Tests for admin guard, background-task error logging, runtime config and unload."""
import asyncio
import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager.__init__ as nm
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

        await nm._async_require_admin(hass, call)  # must not raise

        hass.auth.async_get_user.assert_not_called()

    async def test_admin_user_allowed(self):
        hass = MagicMock()
        user = MagicMock(is_admin=True)
        hass.auth.async_get_user = AsyncMock(return_value=user)
        call = MagicMock()
        call.context.user_id = "user-1"

        await nm._async_require_admin(hass, call)  # must not raise

    async def test_non_admin_user_rejected(self):
        hass = MagicMock()
        user = MagicMock(is_admin=False)
        hass.auth.async_get_user = AsyncMock(return_value=user)
        call = MagicMock()
        call.context.user_id = "user-2"

        with pytest.raises(Unauthorized):
            await nm._async_require_admin(hass, call)

    async def test_unknown_user_rejected(self):
        """Stale/unknown user_id → rejected, never allowed by default."""
        hass = MagicMock()
        hass.auth.async_get_user = AsyncMock(return_value=None)
        call = MagicMock()
        call.context.user_id = "ghost"

        with pytest.raises(Unauthorized):
            await nm._async_require_admin(hass, call)


class TestRunLogged:
    """Background tasks must log failures instead of raising."""

    async def test_exception_swallowed_and_logged(self, caplog):
        async def boom():
            raise ValueError("kaboom")

        with caplog.at_level(logging.ERROR):
            await nm._run_logged(boom(), "Test channel")  # must not raise

        assert "Test channel failed" in caplog.text
        assert "kaboom" in caplog.text

    async def test_success_no_log(self, caplog):
        async def fine():
            return 42

        with caplog.at_level(logging.ERROR):
            await nm._run_logged(fine(), "Test channel")

        assert "failed" not in caplog.text


class TestRuntimeConfig:
    """entry.data → runtime config precedence, incl. valid zero values."""

    def test_zero_volume_and_delay_preserved(self):
        """0.0 / 0 are valid saved values — must not fall back to defaults."""
        entry = MagicMock()
        entry.data = {"alexa_tts_volume": 0.0, "alexa_post_tts_delay": 0}

        cfg = nm._get_runtime_config(entry)

        assert cfg["alexa_tts_volume"] == 0.0
        assert cfg["alexa_post_tts_delay"] == 0

    def test_missing_values_fall_back_to_const(self):
        entry = MagicMock()
        entry.data = {}

        cfg = nm._get_runtime_config(entry)

        assert cfg["alexa_tts_volume"] == nm._CONST_ALEXA_TTS_VOLUME
        assert cfg["alexa_post_tts_delay"] == nm._CONST_ALEXA_POST_TTS_DELAY

    def test_entry_data_overrides_const(self):
        entry = MagicMock()
        entry.data = {"phone_targets": {"x": {"mobile": "notify.x"}}}

        cfg = nm._get_runtime_config(entry)

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
        })
        entry = MagicMock()
        entry.entry_id = "entry1"

        with patch.object(nm, "async_close_bridge_sessions", new=AsyncMock()) as close:
            result = await nm.async_unload_entry(hass, entry)

        assert result is True
        coordinator.async_shutdown.assert_awaited_once()
        removed = {c.args[1] for c in hass.services.async_remove.call_args_list}
        assert removed == {"notify", "whatsapp_bridge_logs", "whatsapp_bridge_restart"}
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
