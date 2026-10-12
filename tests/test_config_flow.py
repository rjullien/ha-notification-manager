"""Tests for config_flow validation logic."""
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))


class _TestFlowBase:
    """Minimal class base for invoking config-flow steps with mocked HA."""

    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)


class TestBridgeValidation:
    """Test _async_validate_bridge function."""

    @pytest.mark.asyncio
    async def test_empty_url_returns_error(self):
        """Empty bridge URL should return error."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.config_flow" in sys.modules:
                del sys.modules["notification_manager.config_flow"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.config_flow import _async_validate_bridge

            hass = MagicMock()
            result = await _async_validate_bridge(hass, "", "token")
            assert result == "bridge_url_required"

    @pytest.mark.asyncio
    async def test_successful_connection(self):
        """HTTP 200 → None (success)."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.config_flow" in sys.modules:
                del sys.modules["notification_manager.config_flow"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.config_flow import _async_validate_bridge

            # Mock the aiohttp session
            mock_resp = AsyncMock()
            mock_resp.status = 200
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_resp)

            hass = MagicMock()
            with patch(
                "notification_manager.config_flow.async_get_clientsession",
                return_value=mock_session,
            ):
                result = await _async_validate_bridge(
                    hass, "http://bridge:8080", "token"
                )
                assert result is None

    @pytest.mark.asyncio
    async def test_auth_failure(self):
        """HTTP 401 → invalid_auth."""
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.config_flow" in sys.modules:
                del sys.modules["notification_manager.config_flow"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]

            from notification_manager.config_flow import _async_validate_bridge

            mock_resp = AsyncMock()
            mock_resp.status = 401
            mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
            mock_resp.__aexit__ = AsyncMock(return_value=None)

            mock_session = MagicMock()
            mock_session.get = MagicMock(return_value=mock_resp)

            hass = MagicMock()
            with patch(
                "notification_manager.config_flow.async_get_clientsession",
                return_value=mock_session,
            ):
                result = await _async_validate_bridge(
                    hass, "http://bridge:8080", "bad-token"
                )
                assert result == "invalid_auth"


class TestBridgeValidationVerifySsl:
    """verify_ssl propagation and connection errors in bridge validation."""

    def _import(self):
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            if "notification_manager.config_flow" in sys.modules:
                del sys.modules["notification_manager.config_flow"]
            if "notification_manager.const" in sys.modules:
                del sys.modules["notification_manager.const"]
            import notification_manager.config_flow as cf
        return cf

    def _ok_session(self):
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_resp.__aexit__ = AsyncMock(return_value=None)
        session = MagicMock()
        session.get = MagicMock(return_value=mock_resp)
        return session

    @pytest.mark.asyncio
    async def test_verify_ssl_false_forwarded(self):
        cf = self._import()
        session = self._ok_session()
        hass = MagicMock()

        with patch.object(cf, "async_get_clientsession", return_value=session) as gs:
            result = await cf._async_validate_bridge(
                hass, "http://bridge:8080", "tok", verify_ssl=False
            )

        assert result is None
        gs.assert_called_once_with(hass, verify_ssl=False)

    @pytest.mark.asyncio
    async def test_verify_ssl_defaults_to_true(self):
        """Without an explicit flag, validation verifies TLS (secure default)."""
        cf = self._import()
        session = self._ok_session()
        hass = MagicMock()

        with patch.object(cf, "async_get_clientsession", return_value=session) as gs:
            await cf._async_validate_bridge(hass, "http://bridge:8080", "tok")

        gs.assert_called_once_with(hass, verify_ssl=True)

    @pytest.mark.asyncio
    async def test_connection_error_returns_cannot_connect(self):
        cf = self._import()
        import aiohttp as aiohttp_mod  # conftest mock with real exception classes
        session = MagicMock()
        session.get = MagicMock(side_effect=aiohttp_mod.ClientConnectorError())
        hass = MagicMock()

        with patch.object(cf, "async_get_clientsession", return_value=session):
            result = await cf._async_validate_bridge(hass, "http://bridge:8080", "tok")

        assert result == "cannot_connect"

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_unknown(self):
        cf = self._import()
        session = MagicMock()
        session.get = MagicMock(side_effect=RuntimeError("boom"))
        hass = MagicMock()

        with patch.object(cf, "async_get_clientsession", return_value=session):
            result = await cf._async_validate_bridge(hass, "http://bridge:8080", "tok")

        assert result == "unknown"


class TestReconfigurePreservesEntryData:
    """Reconfigure must start from dict(entry.data) so keys are never dropped."""

    def _import(self):
        config_entries = sys.modules["homeassistant.config_entries"]
        original_config_flow = config_entries.ConfigFlow
        original_options_flow = config_entries.OptionsFlow
        config_entries.ConfigFlow = _TestFlowBase
        config_entries.OptionsFlow = _TestFlowBase
        with patch.dict(sys.modules, {"notification_manager.const_private": MagicMock()}):
            try:
                for name in (
                    "notification_manager.config_flow",
                    "notification_manager.const",
                ):
                    if name in sys.modules:
                        del sys.modules[name]
                import notification_manager.config_flow as cf
            finally:
                config_entries.ConfigFlow = original_config_flow
                config_entries.OptionsFlow = original_options_flow
        return cf

    def test_seed_starts_from_full_entry_data_then_overwrites(self):
        cf = self._import()
        entry_data = {
            "bridge_url": "http://old",
            "bridge_token": "old-tok",
            "verify_ssl": True,
            "phone_targets": {"alice": {"mobile": "notify.alice"}},
            "telegram_groups": {"family": -100},
            "custom_future_key": "keep-me",
        }

        seeded = cf._seed_reconfigure_data(
            entry_data,
            bridge_url="http://new",
            bridge_token="new-tok",
            verify_ssl=False,
        )

        assert seeded["bridge_url"] == "http://new"
        assert seeded["bridge_token"] == "new-tok"
        assert seeded["verify_ssl"] is False
        assert seeded["phone_targets"] == {"alice": {"mobile": "notify.alice"}}
        assert seeded["telegram_groups"] == {"family": -100}
        assert seeded["custom_future_key"] == "keep-me"
        # Original entry dict must not be mutated
        assert entry_data["bridge_url"] == "http://old"


class TestReconfigureStructureValidation:
    """Reconfigure JSON fields must match the structures consumed at runtime."""

    def _make_flow(self):
        cf = TestReconfigurePreservesEntryData()._import()
        flow = cf.NotificationManagerConfigFlow()
        entry = MagicMock()
        entry.data = {
            "phone_targets": {"alice": {"mobile": "notify.mobile_app_alice"}},
            "phone_default_targets": ["alice"],
            "whatsapp_contacts": {"alice": "33600000000@s.whatsapp.net"},
            "alexa_players": ["media_player.echo"],
            "alexa_tts_volume": 0.7,
            "alexa_post_tts_delay": 5,
        }
        flow.hass = MagicMock()
        flow.hass.config_entries.async_get_entry.return_value = entry
        flow.context = {"entry_id": "entry1"}
        flow._reconfigure_data = {}
        flow.async_show_form = MagicMock(
            side_effect=lambda **kwargs: {"type": "form", **kwargs}
        )
        return cf, flow

    @staticmethod
    def _stub_next_step(flow, step):
        next_step = {
            "alexa": "async_step_reconfigure_alerts",
            "phone": "async_step_reconfigure_whatsapp",
            "whatsapp": "async_step_reconfigure_alexa",
        }[step]
        next_step_mock = AsyncMock(
            return_value=next_step.removeprefix("async_step_reconfigure_")
        )
        setattr(flow, next_step, next_step_mock)
        return next_step_mock

    @pytest.mark.parametrize(
        ("step", "user_input", "field"),
        [
            (
                "alexa",
                {"alexa_players_json": '{"not": "a list"}', "alexa_tts_volume": "0.7"},
                "alexa_players_json",
            ),
            (
                "alexa",
                {"alexa_players_json": '["media_player.echo", "  "]'},
                "alexa_players_json",
            ),
            (
                "phone",
                {"phone_targets_json": '[{"mobile": "notify.alice"}]'},
                "phone_targets_json",
            ),
            (
                "phone",
                {"phone_targets_json": '{"alice": {"mobile": "nodot"}}'},
                "phone_targets_json",
            ),
            (
                "phone",
                {"phone_targets_json": '{"alice": {"mobile": ".notify"}}'},
                "phone_targets_json",
            ),
            (
                "phone",
                {"phone_targets_json": '{"alice": {"mobile": "notify."}}'},
                "phone_targets_json",
            ),
            (
                "phone",
                {"phone_targets_json": '{"alice": {"mobile": "notify.phone.extra"}}'},
                "phone_targets_json",
            ),
            (
                "phone",
                {"phone_targets_json": '{"alice": {"mobile": 123}}'},
                "phone_targets_json",
            ),
            (
                "phone",
                {"phone_targets_json": '{"alice": {"telegram_chat_id": "--1"}}'},
                "phone_targets_json",
            ),
            (
                "phone",
                {"phone_targets_json": '{"alice": {"telegram_chat_id": true}}'},
                "phone_targets_json",
            ),
            (
                "phone",
                {"phone_targets_json": '{"alice": {}}'},
                "phone_targets_json",
            ),
            (
                "phone",
                {
                    "phone_targets_json": '{"alice": {"mobile": "notify.alice"}}',
                    "phone_default_targets": "unknown",
                },
                "phone_default_targets",
            ),
            (
                "whatsapp",
                {"whatsapp_contacts_json": '{"alice": 123}'},
                "whatsapp_contacts_json",
            ),
            (
                "whatsapp",
                {"whatsapp_contacts_json": '{"alice": "   "}'},
                "whatsapp_contacts_json",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_invalid_structure_returns_field_error(
        self, step, user_input, field
    ):
        """Each malformed JSON shape is reported against its own config field."""
        _cf, flow = self._make_flow()
        next_step_mock = self._stub_next_step(flow, step)
        step_methods = {
            "alexa": flow.async_step_reconfigure_alexa,
            "phone": flow.async_step_reconfigure_phone,
            "whatsapp": flow.async_step_reconfigure_whatsapp,
        }

        result = await step_methods[step](user_input)

        assert result["errors"][field] == "invalid_structure"
        next_step_mock.assert_not_awaited()

    @pytest.mark.parametrize(
        ("step", "user_input", "next_step"),
        [
            (
                "alexa",
                {
                    "alexa_players_json": '["media_player.echo"]',
                    "alexa_tts_volume": "0.7",
                    "alexa_post_tts_delay": "5",
                },
                "alerts",
            ),
            (
                "phone",
                {
                    "phone_targets_json": (
                        '{"alice": {"mobile": "notify.alice", '
                        '"telegram_chat_id": "-100"}, "bob": '
                        '{"telegram_chat_id": 123}, "carol": '
                        '{"mobile": "notify.carol"}}'
                    ),
                    "phone_default_targets": "alice, bob",
                },
                "whatsapp",
            ),
            (
                "whatsapp",
                {
                    "whatsapp_contacts_json": (
                        '{"alice": "33600000000@s.whatsapp.net"}'
                    )
                },
                "alexa",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_valid_structures_advance_to_next_step(
        self, step, user_input, next_step
    ):
        """Valid field structures continue the reconfigure flow."""
        _cf, flow = self._make_flow()
        next_step_mock = self._stub_next_step(flow, step)
        step_methods = {
            "alexa": flow.async_step_reconfigure_alexa,
            "phone": flow.async_step_reconfigure_phone,
            "whatsapp": flow.async_step_reconfigure_whatsapp,
        }

        result = await step_methods[step](user_input)

        assert result == next_step
        next_step_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_zero_tts_volume_uses_existing_volume_error(self):
        """A silent TTS volume is rejected while positive volume remains valid."""
        _cf, flow = self._make_flow()
        flow.async_step_reconfigure_alerts = AsyncMock(return_value="alerts")

        result = await flow.async_step_reconfigure_alexa(
            {
                "alexa_players_json": '["media_player.echo"]',
                "alexa_tts_volume": "0",
                "alexa_post_tts_delay": "5",
            }
        )

        assert result["errors"] == {"alexa_tts_volume": "invalid_volume"}
        flow.async_step_reconfigure_alerts.assert_not_awaited()
