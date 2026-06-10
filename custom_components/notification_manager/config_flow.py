"""Config flow for Notification Manager."""
from __future__ import annotations

import json
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ALEXA_EN_TARGET,
    ALEXA_PLAYERS,
    ALEXA_POST_TTS_DELAY,
    ALEXA_TTS_VOLUME,
    BRIDGE_ALERT_CHAT_IDS,
    BRIDGE_HEALTH_ENDPOINT,
    BRIDGE_TIMEOUT,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_VERIFY_SSL,
    DEFAULT_BRIDGE_TOKEN,
    DEFAULT_BRIDGE_URL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PHONE_DEFAULT_TARGETS,
    PHONE_TARGETS,
    WHATSAPP_CONTACTS,
)

_LOGGER = logging.getLogger(__name__)

# ── JSON helper ───────────────────────────────────────────────────────────────

def _to_json(value: Any) -> str:
    """Serialize a value to a pretty JSON string for display in a text field."""
    return json.dumps(value, ensure_ascii=False, indent=2)


def _parse_json(raw: str, field_name: str, errors: dict) -> Any | None:
    """Parse a JSON string; record an error and return None on failure."""
    try:
        return json.loads(raw.strip())
    except (json.JSONDecodeError, ValueError):
        errors[field_name] = "invalid_json"
        return None


def _bridge_schema(url: str, token: str, verify_ssl: bool) -> vol.Schema:
    """Build the bridge URL/token/TLS schema shared by all bridge steps."""
    return vol.Schema(
        {
            vol.Optional(CONF_BRIDGE_URL, default=url): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.URL)
            ),
            vol.Optional(CONF_BRIDGE_TOKEN, default=token): selector.TextSelector(
                selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
            ),
            vol.Optional(CONF_VERIFY_SSL, default=verify_ssl): selector.BooleanSelector(),
        }
    )


# ── Bridge validation ─────────────────────────────────────────────────────────

async def _async_validate_bridge(
    hass: HomeAssistant,
    bridge_url: str,
    bridge_token: str,
    verify_ssl: bool = DEFAULT_VERIFY_SSL,
) -> str | None:
    """Validate the whatsmeow bridge connection.

    Returns None on success, or an error key string on failure.
    """
    if not bridge_url:
        return "bridge_url_required"

    url = bridge_url.rstrip("/") + BRIDGE_HEALTH_ENDPOINT
    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    headers = {"Authorization": f"Bearer {bridge_token}"}

    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=BRIDGE_TIMEOUT),
        ) as resp:
            if resp.status == 200:
                return None
            if resp.status in (401, 403):
                return "invalid_auth"
            return "cannot_connect"
    except aiohttp.ClientConnectorError:
        return "cannot_connect"
    except aiohttp.InvalidURL:
        return "invalid_url"
    except Exception:  # noqa: BLE001
        return "unknown"


# ── Config flow ───────────────────────────────────────────────────────────────

class NotificationManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Notification Manager."""

    VERSION = 1
    supports_reconfigure = True

    # ── Initial setup (one step — bridge only) ────────────────────────────────

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            bridge_url: str = user_input[CONF_BRIDGE_URL].strip()
            bridge_token: str = user_input[CONF_BRIDGE_TOKEN].strip()
            verify_ssl: bool = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))

            # Validate bridge but don't block — warn and continue
            if bridge_url:
                error = await _async_validate_bridge(
                    self.hass, bridge_url, bridge_token, verify_ssl
                )
                if error:
                    _LOGGER.warning(
                        "Bridge validation failed (%s) — saving config anyway. "
                        "Bridge may not be reachable from this container (e.g. Tailscale DNS).",
                        error,
                    )

            return self.async_create_entry(
                title="Notification Manager",
                data={
                    CONF_BRIDGE_URL: bridge_url,
                    CONF_BRIDGE_TOKEN: bridge_token,
                    CONF_VERIFY_SSL: verify_ssl,
                },
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_bridge_schema(
                DEFAULT_BRIDGE_URL, DEFAULT_BRIDGE_TOKEN, DEFAULT_VERIFY_SSL
            ),
            errors=errors,
        )

    # ── Reconfigure flow (5 steps) ────────────────────────────────────────────

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Entry point for reconfigure — delegates to step 1."""
        return await self.async_step_reconfigure_bridge(user_input)

    async def async_step_reconfigure_bridge(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 1/5 — WhatsApp bridge URL & token."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}

        if user_input is not None:
            bridge_url: str = user_input[CONF_BRIDGE_URL].strip()
            bridge_token: str = user_input[CONF_BRIDGE_TOKEN].strip()
            verify_ssl: bool = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))

            # Validate bridge but don't block — warn and continue
            if bridge_url:
                error = await _async_validate_bridge(
                    self.hass, bridge_url, bridge_token, verify_ssl
                )
                if error:
                    _LOGGER.warning(
                        "Bridge validation failed (%s) during reconfigure — saving anyway.",
                        error,
                    )

            self._reconfigure_data: dict[str, Any] = {
                CONF_BRIDGE_URL: bridge_url,
                CONF_BRIDGE_TOKEN: bridge_token,
                CONF_VERIFY_SSL: verify_ssl,
            }
            # Carry forward all existing data; step results will overwrite
            for k, v in entry.data.items():
                if k not in self._reconfigure_data:
                    self._reconfigure_data[k] = v
            return await self.async_step_reconfigure_phone()

        return self.async_show_form(
            step_id="reconfigure_bridge",
            data_schema=_bridge_schema(
                entry.data.get(CONF_BRIDGE_URL, DEFAULT_BRIDGE_URL),
                entry.data.get(CONF_BRIDGE_TOKEN, DEFAULT_BRIDGE_TOKEN),
                entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ),
            errors=errors,
        )

    async def async_step_reconfigure_phone(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 2/5 — Phone contacts (JSON).

        phone_targets  : {"name": {"mobile": "notify.xxx", "telegram_chat_id": 123}}
        phone_default_targets : ["name1", "name2"]
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}

        if user_input is not None:
            phone_targets = _parse_json(
                user_input.get("phone_targets_json", "{}"),
                "phone_targets_json",
                errors,
            )
            phone_defaults_raw = user_input.get("phone_default_targets", "").strip()
            phone_default_targets = [
                t.strip() for t in phone_defaults_raw.split(",") if t.strip()
            ]

            if not errors:
                self._reconfigure_data["phone_targets"] = phone_targets
                self._reconfigure_data["phone_default_targets"] = phone_default_targets
                return await self.async_step_reconfigure_whatsapp()

        # Pre-fill: entry.data first, then const (= const_private values)
        current_phone = entry.data.get("phone_targets") or PHONE_TARGETS
        current_defaults = entry.data.get("phone_default_targets") or list(PHONE_DEFAULT_TARGETS)

        schema = vol.Schema(
            {
                vol.Optional("phone_targets_json", default=_to_json(current_phone)): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Optional("phone_default_targets", default=", ".join(current_defaults)): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            }
        )

        return self.async_show_form(
            step_id="reconfigure_phone",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "example": '{"alice": {"mobile": "notify.mobile_app_iphone_alice", "telegram_chat_id": 123456789}}'
            },
        )

    async def async_step_reconfigure_whatsapp(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 3/5 — WhatsApp contacts (JSON).

        whatsapp_contacts : {"alice": "33600000000@s.whatsapp.net"}
        """
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}

        if user_input is not None:
            whatsapp_contacts = _parse_json(
                user_input.get("whatsapp_contacts_json", "{}"),
                "whatsapp_contacts_json",
                errors,
            )

            if not errors:
                self._reconfigure_data["whatsapp_contacts"] = whatsapp_contacts
                return await self.async_step_reconfigure_alexa()

        current_wa = entry.data.get("whatsapp_contacts") or WHATSAPP_CONTACTS

        schema = vol.Schema(
            {
                vol.Optional("whatsapp_contacts_json", default=_to_json(current_wa)): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
            }
        )

        return self.async_show_form(
            step_id="reconfigure_whatsapp",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "example": '{"alice": "33600000000@s.whatsapp.net", "family": "120363000000000000@g.us"}'
            },
        )

    async def async_step_reconfigure_alexa(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 4/5 — Alexa TTS configuration."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}

        if user_input is not None:
            alexa_players_raw = user_input.get("alexa_players_json", "[]").strip()
            alexa_players = _parse_json(alexa_players_raw, "alexa_players_json", errors)

            alexa_en_target: str = user_input.get("alexa_en_target", "").strip()

            try:
                alexa_tts_volume = float(user_input.get("alexa_tts_volume", ALEXA_TTS_VOLUME))
                if not 0.0 <= alexa_tts_volume <= 1.0:
                    errors["alexa_tts_volume"] = "invalid_volume"
            except (TypeError, ValueError):
                errors["alexa_tts_volume"] = "invalid_volume"
                alexa_tts_volume = ALEXA_TTS_VOLUME

            try:
                alexa_post_tts_delay = int(user_input.get("alexa_post_tts_delay", ALEXA_POST_TTS_DELAY))
                if alexa_post_tts_delay < 0:
                    errors["alexa_post_tts_delay"] = "invalid_number"
            except (TypeError, ValueError):
                errors["alexa_post_tts_delay"] = "invalid_number"
                alexa_post_tts_delay = ALEXA_POST_TTS_DELAY

            if not errors:
                self._reconfigure_data["alexa_players"] = alexa_players
                self._reconfigure_data["alexa_en_target"] = alexa_en_target
                self._reconfigure_data["alexa_tts_volume"] = alexa_tts_volume
                self._reconfigure_data["alexa_post_tts_delay"] = alexa_post_tts_delay
                return await self.async_step_reconfigure_alerts()

        current_players = entry.data.get("alexa_players") or ALEXA_PLAYERS
        current_en_target = entry.data.get("alexa_en_target") or ALEXA_EN_TARGET
        # `is not None` (not `or`): 0.0 / 0 are valid saved values and must
        # not silently fall back to the defaults.
        _saved_volume = entry.data.get("alexa_tts_volume")
        current_tts_volume = _saved_volume if _saved_volume is not None else ALEXA_TTS_VOLUME
        _saved_delay = entry.data.get("alexa_post_tts_delay")
        current_post_delay = _saved_delay if _saved_delay is not None else ALEXA_POST_TTS_DELAY

        schema = vol.Schema(
            {
                vol.Optional("alexa_players_json", default=_to_json(current_players)): selector.TextSelector(
                    selector.TextSelectorConfig(multiline=True)
                ),
                vol.Optional("alexa_en_target", default=current_en_target): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional("alexa_tts_volume", default=str(current_tts_volume)): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
                vol.Optional("alexa_post_tts_delay", default=str(current_post_delay)): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            }
        )

        return self.async_show_form(
            step_id="reconfigure_alexa",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reconfigure_alerts(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step 5/5 — Alert configuration; writes the updated entry on success."""
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        errors: dict[str, str] = {}

        if user_input is not None:
            alert_ids_raw = user_input.get("bridge_alert_chat_ids", "").strip()
            bridge_alert_chat_ids: list[int] = []
            try:
                for part in alert_ids_raw.replace(",", " ").split():
                    bridge_alert_chat_ids.append(int(part.strip()))
            except ValueError:
                errors["bridge_alert_chat_ids"] = "invalid_chat_ids"

            if not errors:
                self._reconfigure_data["bridge_alert_chat_ids"] = bridge_alert_chat_ids

                self.hass.config_entries.async_update_entry(
                    entry,
                    data=self._reconfigure_data,
                )
                return self.async_abort(reason="reconfigure_successful")

        current_alert_ids = entry.data.get("bridge_alert_chat_ids") or list(BRIDGE_ALERT_CHAT_IDS)

        schema = vol.Schema(
            {
                vol.Optional(
                    "bridge_alert_chat_ids",
                    default=", ".join(str(i) for i in current_alert_ids),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.TEXT)
                ),
            }
        )

        return self.async_show_form(
            step_id="reconfigure_alerts",
            data_schema=schema,
            errors=errors,
        )

    # ── Options flow ──────────────────────────────────────────────────────────

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NotificationManagerOptionsFlow:
        """Return the options flow handler."""
        return NotificationManagerOptionsFlow()


class NotificationManagerOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Notification Manager (bridge URL/token/TLS only).

    Note: no custom __init__ — explicitly setting self.config_entry was
    deprecated in HA 2024.11 and removed in 2025.12; the base class now
    provides the config_entry property.
    """

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            bridge_url: str = user_input[CONF_BRIDGE_URL].strip()
            bridge_token: str = user_input[CONF_BRIDGE_TOKEN].strip()
            verify_ssl: bool = bool(user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))

            # Validate bridge but don't block — warn and continue
            if bridge_url:
                error = await _async_validate_bridge(
                    self.hass, bridge_url, bridge_token, verify_ssl
                )
                if error:
                    _LOGGER.warning(
                        "Bridge validation failed (%s) in options flow — saving anyway.",
                        error,
                    )

            self.hass.config_entries.async_update_entry(
                self.config_entry,
                data={
                    **self.config_entry.data,
                    CONF_BRIDGE_URL: bridge_url,
                    CONF_BRIDGE_TOKEN: bridge_token,
                    CONF_VERIFY_SSL: verify_ssl,
                },
            )
            return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="init",
            data_schema=_bridge_schema(
                self.config_entry.data.get(CONF_BRIDGE_URL, DEFAULT_BRIDGE_URL),
                self.config_entry.data.get(CONF_BRIDGE_TOKEN, DEFAULT_BRIDGE_TOKEN),
                self.config_entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            ),
            errors=errors,
        )
