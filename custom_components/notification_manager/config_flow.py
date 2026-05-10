"""Config flow for Notification Manager."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    BRIDGE_HEALTH_ENDPOINT,
    BRIDGE_TIMEOUT,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    DEFAULT_BRIDGE_TOKEN,
    DEFAULT_BRIDGE_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Optional(CONF_BRIDGE_URL, default=DEFAULT_BRIDGE_URL): str,
        vol.Optional(CONF_BRIDGE_TOKEN, default=DEFAULT_BRIDGE_TOKEN): str,
    }
)


async def _async_validate_bridge(
    hass: HomeAssistant, bridge_url: str, bridge_token: str
) -> str | None:
    """Validate the whatsmeow bridge connection.

    Returns None on success, or an error key string on failure.
    """
    if not bridge_url:
        return None  # WhatsApp bridge is optional

    url = bridge_url.rstrip("/") + BRIDGE_HEALTH_ENDPOINT
    session = async_get_clientsession(hass, verify_ssl=False)
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


class NotificationManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the config flow for Notification Manager."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            bridge_url: str = user_input[CONF_BRIDGE_URL].strip()
            bridge_token: str = user_input[CONF_BRIDGE_TOKEN].strip()

            error = None
            if bridge_url:
                error = await _async_validate_bridge(
                    self.hass, bridge_url, bridge_token
                )
            if error:
                errors["base"] = error
            else:
                # Avoid duplicate entries
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="Notification Manager",
                    data={
                        CONF_BRIDGE_URL: bridge_url,
                        CONF_BRIDGE_TOKEN: bridge_token,
                    },
                )

        # Rebuild schema with user's input as defaults (so fields aren't cleared on error)
        suggested_url = user_input.get(CONF_BRIDGE_URL, DEFAULT_BRIDGE_URL) if user_input else DEFAULT_BRIDGE_URL
        suggested_token = user_input.get(CONF_BRIDGE_TOKEN, DEFAULT_BRIDGE_TOKEN) if user_input else DEFAULT_BRIDGE_TOKEN
        schema = vol.Schema(
            {
                vol.Optional(CONF_BRIDGE_URL, default=suggested_url): str,
                vol.Optional(CONF_BRIDGE_TOKEN, default=suggested_token): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> NotificationManagerOptionsFlow:
        """Return the options flow handler."""
        return NotificationManagerOptionsFlow(config_entry)


class NotificationManagerOptionsFlow(config_entries.OptionsFlow):
    """Handle options for Notification Manager."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle options."""
        errors: dict[str, str] = {}

        if user_input is not None:
            bridge_url: str = user_input[CONF_BRIDGE_URL].strip()
            bridge_token: str = user_input[CONF_BRIDGE_TOKEN].strip()

            error = None
            if bridge_url:
                error = await _async_validate_bridge(
                    self.hass, bridge_url, bridge_token
                )
            if error:
                errors["base"] = error
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        CONF_BRIDGE_URL: bridge_url,
                        CONF_BRIDGE_TOKEN: bridge_token,
                    },
                )
                return self.async_create_entry(title="", data={})

        current_url = self.config_entry.data.get(CONF_BRIDGE_URL, DEFAULT_BRIDGE_URL)
        current_token = self.config_entry.data.get(
            CONF_BRIDGE_TOKEN, DEFAULT_BRIDGE_TOKEN
        )

        schema = vol.Schema(
            {
                vol.Optional(CONF_BRIDGE_URL, default=current_url): str,
                vol.Optional(CONF_BRIDGE_TOKEN, default=current_token): str,
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
