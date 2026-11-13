"""Tests for multi telegram_bot config_entry_id routing (issue #34)."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
    import notification_manager.messaging as nm
    import notification_manager.notify as notify
    from notification_manager.const import (
        CONF_TELEGRAM_BOTS,
        TELEGRAM_BOT_INFO,
        TELEGRAM_BOT_PRINCIPAL,
    )
    from notification_manager.telegram_target import (
        TelegramTargetUnresolvedError,
        apply_telegram_target,
        resolve_telegram_config_entry_id,
    )


PRINCIPAL_ID = "01TESTPRINCIPAL00000000000"
INFO_ID = "01TESTINFOBOT0000000000000"


def _tg_entry(
    entry_id: str,
    *,
    disabled_by=None,
    state=ConfigEntryState.LOADED,
    allowed_chats: list[int] | None = None,
    title: str = "bot",
):
    entry = MagicMock()
    entry.entry_id = entry_id
    entry.domain = "telegram_bot"
    entry.disabled_by = disabled_by
    entry.state = state
    entry.title = title
    if allowed_chats is None:
        entry.subentries = {}
    else:
        entry.subentries = {
            f"sub_{chat}": MagicMock(
                subentry_type="allowed_chat_ids",
                data={"chat_id": chat},
            )
            for chat in allowed_chats
        }
    return entry


def _make_hass(call_log: list | None = None, entries: list | None = None):
    hass = MagicMock()
    hass.data = {}
    by_id = {e.entry_id: e for e in (entries or [])}
    hass.config_entries.async_entries = MagicMock(
        side_effect=lambda domain: [
            e for e in by_id.values() if e.domain == domain
        ]
        if domain == "telegram_bot"
        else []
    )
    hass.config_entries.async_get_entry = MagicMock(
        side_effect=lambda entry_id: by_id.get(entry_id)
    )

    if call_log is not None:

        async def record(domain, service, data, blocking=False):
            call_log.append((domain, service, dict(data), blocking))

        hass.services.async_call = AsyncMock(side_effect=record)
    return hass


def _nm_entry(**data):
    entry = MagicMock()
    entry.data = data
    return entry


class TestResolveTelegramConfigEntryId:
    """Unit tests for resolve_telegram_config_entry_id."""

    def test_single_entry_auto_detect_as_principal(self):
        principal = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        hass = _make_hass(entries=[principal])
        entry = _nm_entry()

        assert (
            resolve_telegram_config_entry_id(hass, entry, TELEGRAM_BOT_PRINCIPAL, 111)
            == PRINCIPAL_ID
        )

    def test_configured_principal_preferred_over_auto_detect(self):
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[1])
        b = _tg_entry(INFO_ID, allowed_chats=[2])
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry(
            **{CONF_TELEGRAM_BOTS: {TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID}}
        )

        assert (
            resolve_telegram_config_entry_id(hass, entry, TELEGRAM_BOT_PRINCIPAL)
            == PRINCIPAL_ID
        )

    def test_info_routes_to_info_entry(self):
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111, 222])
        b = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        assert (
            resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_INFO, chat_id=111
            )
            == INFO_ID
        )

    def test_info_missing_falls_back_to_principal(self, caplog):
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        hass = _make_hass(entries=[a])
        entry = _nm_entry(
            **{CONF_TELEGRAM_BOTS: {TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID}}
        )

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_INFO, chat_id=111
            )

        assert result == PRINCIPAL_ID
        assert "falling back to principal" in caplog.text

    def test_info_disabled_falls_back(self, caplog):
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        b = _tg_entry(INFO_ID, disabled_by="user", allowed_chats=[111])
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_INFO, chat_id=111
            )

        assert result == PRINCIPAL_ID
        assert "disabled or not loaded" in caplog.text

    def test_info_chat_not_allowed_falls_back(self, caplog):
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111, 222])
        b = _tg_entry(INFO_ID, allowed_chats=[111])  # 222 not allowed
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_INFO, chat_id=222
            )

        assert result == PRINCIPAL_ID
        assert "not allowlisted" in caplog.text

    def test_default_is_principal(self):
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[1])
        b = _tg_entry(INFO_ID, allowed_chats=[1])
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        assert resolve_telegram_config_entry_id(hass, entry) == PRINCIPAL_ID

    def test_two_entries_without_config_warns(self, caplog):
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[1])
        b = _tg_entry(INFO_ID, allowed_chats=[1])
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry()

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_PRINCIPAL
            )

        assert result is None
        assert "telegram_bots.principal" in caplog.text

    def test_disabled_principal_among_two_does_not_auto_pick_info(self, caplog):
        """Auto-detect must not treat the sole *usable* of several as principal."""
        a = _tg_entry(PRINCIPAL_ID, disabled_by="user", allowed_chats=[1])
        b = _tg_entry(INFO_ID, allowed_chats=[1])
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry()

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_PRINCIPAL
            )

        assert result is None
        assert "telegram_bots.principal" in caplog.text

    def test_stale_principal_recovers_to_sole_usable(self, caplog):
        """Deleted/replaced principal mapping → sole remaining usable entry."""
        stale = "01STALEPRINCIPAL000000000000"
        new = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        hass = _make_hass(entries=[new])
        entry = _nm_entry(
            **{CONF_TELEGRAM_BOTS: {TELEGRAM_BOT_PRINCIPAL: stale}}
        )

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_PRINCIPAL, chat_id=111
            )

        assert result == PRINCIPAL_ID
        assert "sole usable" in caplog.text

    def test_stale_principal_with_two_usable_returns_none(self, caplog):
        stale = "01STALEPRINCIPAL000000000000"
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[1])
        b = _tg_entry(INFO_ID, allowed_chats=[1])
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry(
            **{CONF_TELEGRAM_BOTS: {TELEGRAM_BOT_PRINCIPAL: stale}}
        )

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_PRINCIPAL
            )

        assert result is None
        assert "missing, disabled, or not loaded" in caplog.text

    def test_disabled_principal_does_not_resolve_to_info(self, caplog):
        """Principal still present but unusable → never substitute Info."""
        a = _tg_entry(PRINCIPAL_ID, disabled_by="user", allowed_chats=[111])
        b = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        with caplog.at_level(logging.ERROR):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_PRINCIPAL, chat_id=111
            )

        assert result is None
        assert "not substituting another bot" in caplog.text

    def test_deleted_principal_does_not_recover_to_configured_info(self, caplog):
        """Sole remaining usable entry is Info → still fail closed for principal."""
        stale = "01STALEPRINCIPAL000000000000"
        info = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(entries=[info])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: stale,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_PRINCIPAL, chat_id=111
            )

        assert result is None

    def test_mapping_proxy_allowlist_triggers_info_fallback(self, caplog):
        """HA stores subentry.data as MappingProxyType — must still parse chat_id."""
        from types import MappingProxyType

        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111, 222])
        b = _tg_entry(INFO_ID, allowed_chats=None)
        # Real HA shape: MappingProxyType for subentry.data
        b.subentries = {
            "sub_111": MagicMock(
                subentry_type="allowed_chat_ids",
                data=MappingProxyType({"chat_id": 111}),
            )
        }
        hass = _make_hass(entries=[a, b])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        with caplog.at_level(logging.WARNING):
            result = resolve_telegram_config_entry_id(
                hass, entry, TELEGRAM_BOT_INFO, chat_id=222
            )

        assert result == PRINCIPAL_ID
        assert "not allowlisted" in caplog.text


class TestCallTelegramInjectsConfigEntryId:
    """_async_call_telegram always passes config_entry_id when resolvable."""

    async def test_single_entry_injects_config_entry_id(self):
        log: list = []
        principal = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        hass = _make_hass(log, entries=[principal])
        entry = _nm_entry()

        await nm._async_call_telegram(
            hass, 111, "hello", entry=entry, telegram_bot="principal"
        )

        assert log[0][2]["config_entry_id"] == PRINCIPAL_ID
        assert log[0][2]["chat_id"] == 111

    async def test_info_bot_uses_info_entry_id(self):
        log: list = []
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        b = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(log, entries=[a, b])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        await nm._async_call_telegram(
            hass, 111, "hello", entry=entry, telegram_bot="info"
        )

        assert log[0][2]["config_entry_id"] == INFO_ID

    async def test_default_omitted_telegram_bot_uses_principal(self):
        log: list = []
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        b = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(log, entries=[a, b])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        await nm._async_call_telegram(hass, 111, "hello", entry=entry)

        assert log[0][2]["config_entry_id"] == PRINCIPAL_ID

    async def test_send_phone_passes_telegram_bot(self):
        log: list = []
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        b = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(log, entries=[a, b])
        entry = _nm_entry(
            phone_targets={
                "alice": {"mobile": "notify.mobile_app_alice", "telegram_chat_id": 111},
            },
            phone_default_targets=["alice"],
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            },
        )

        await nm._async_send_phone(
            hass, entry, "hello", "alice", telegram_bot="info"
        )

        tg = [item for item in log if item[0] == "telegram_bot"]
        assert tg[0][2]["config_entry_id"] == INFO_ID

    async def test_bridge_alert_always_principal(self):
        log: list = []
        a = _tg_entry(PRINCIPAL_ID, allowed_chats=[111])
        b = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(log, entries=[a, b])
        entry = _nm_entry(
            bridge_alert_chat_ids=[111],
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            },
        )

        await nm._async_send_bridge_alert(hass, entry, "bridge down")

        assert log[0][2]["config_entry_id"] == PRINCIPAL_ID

    async def test_no_entries_omits_config_entry_id(self):
        """Backward compatible: single-bot HA without mapping still sends chat_id."""
        log: list = []
        hass = _make_hass(log, entries=[])
        entry = _nm_entry()

        await nm._async_call_telegram(hass, 111, "hello", entry=entry)

        assert "config_entry_id" not in log[0][2]
        assert log[0][2]["chat_id"] == 111


class TestNotifyServiceTelegramBotField:
    """notify handler forwards telegram_bot to messaging."""

    async def test_notify_forwards_info(self):
        created: list = []
        hass = MagicMock()
        hass.data = {}
        hass._created_tasks = created

        def create_task(hass_, coro, name=None, eager_start=True):
            import asyncio

            task = asyncio.ensure_future(coro)
            created.append(task)
            return task

        entry = MagicMock()
        entry.data = {
            "bridge_url": "",
            "alexa_players": [],
            "phone_targets": {
                "alice": {"telegram_chat_id": 111},
            },
            "phone_default_targets": ["alice"],
            CONF_TELEGRAM_BOTS: {
                TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID,
                TELEGRAM_BOT_INFO: INFO_ID,
            },
        }
        entry.async_create_task = MagicMock(side_effect=create_task)
        entry.async_create_background_task = MagicMock(side_effect=create_task)

        call = MagicMock()
        call.data = {
            "message_tel": "hi",
            "message_alexa": "",
            "message_alexa_en": "",
            "notification_tel": "alice",
            "notification_whatsapp": "none",
            "notification_alexa": "none",
            "telegram_group": "",
            "photo_path": "",
            "photo_url": "",
            "parse_mode": "",
            "telegram_bot": "info",
        }
        call.context = MagicMock(id="ctx")

        with patch.object(
            notify.messaging_mod, "_async_send_phone", new=AsyncMock()
        ) as phone:
            await notify._async_handle_notify(hass, entry, call)
            if created:
                import asyncio

                await asyncio.gather(*created)

        phone.assert_awaited()
        assert phone.await_args.kwargs.get("telegram_bot") == "info"


class TestApplyTelegramTarget:
    def test_apply_adds_key(self):
        principal = _tg_entry(PRINCIPAL_ID, allowed_chats=[5])
        hass = _make_hass(entries=[principal])
        entry = _nm_entry(
            **{CONF_TELEGRAM_BOTS: {TELEGRAM_BOT_PRINCIPAL: PRINCIPAL_ID}}
        )
        out = apply_telegram_target(
            {"chat_id": 5, "message": "x"}, hass, entry, "principal"
        )
        assert out["config_entry_id"] == PRINCIPAL_ID
        assert out["chat_id"] == 5

    def test_unresolved_explicit_mapping_aborts_instead_of_omitting_id(self, caplog):
        """Deleted principal + sole Info left must not send without config_entry_id."""
        stale = "01STALEPRINCIPAL000000000000"
        info = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(entries=[info])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: stale,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        with caplog.at_level(logging.ERROR), pytest.raises(
            TelegramTargetUnresolvedError
        ):
            apply_telegram_target(
                {"chat_id": 111, "message": "watchdog"},
                hass,
                entry,
                "principal",
            )

        assert "Aborting Telegram send" in caplog.text

    async def test_call_telegram_aborts_when_mapping_unresolved(self, caplog):
        log: list = []
        stale = "01STALEPRINCIPAL000000000000"
        info = _tg_entry(INFO_ID, allowed_chats=[111])
        hass = _make_hass(log, entries=[info])
        entry = _nm_entry(
            **{
                CONF_TELEGRAM_BOTS: {
                    TELEGRAM_BOT_PRINCIPAL: stale,
                    TELEGRAM_BOT_INFO: INFO_ID,
                }
            }
        )

        with caplog.at_level(logging.ERROR), pytest.raises(
            TelegramTargetUnresolvedError
        ):
            await nm._async_call_telegram(
                hass, 111, "alert", entry=entry, telegram_bot="principal"
            )

        assert log == []
        assert "Aborting Telegram send" in caplog.text


class TestRuntimeTelegramBotsEmptyOverride:
    """Cleared options {} must not resurrect private TELEGRAM_BOTS."""

    def test_empty_dict_in_entry_data_wins(self):
        from notification_manager.runtime import _get_runtime_config

        entry = MagicMock()
        entry.data = {CONF_TELEGRAM_BOTS: {}}
        with patch(
            "notification_manager.runtime._CONST_TELEGRAM_BOTS",
            {TELEGRAM_BOT_PRINCIPAL: "01PRIVATE"},
        ):
            cfg = _get_runtime_config(entry)
        assert cfg[CONF_TELEGRAM_BOTS] == {}

    def test_absent_key_falls_back_to_const(self):
        from notification_manager.runtime import _get_runtime_config

        entry = MagicMock()
        entry.data = {}
        with patch(
            "notification_manager.runtime._CONST_TELEGRAM_BOTS",
            {TELEGRAM_BOT_PRINCIPAL: "01PRIVATE"},
        ):
            cfg = _get_runtime_config(entry)
        assert cfg[CONF_TELEGRAM_BOTS] == {TELEGRAM_BOT_PRINCIPAL: "01PRIVATE"}
