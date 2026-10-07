"""Admin diagnostic services: bridge logs/restart and recent Alexa emissions."""
from __future__ import annotations

import logging
from datetime import timedelta

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import Unauthorized
from homeassistant.helpers import config_validation as cv

from .alexa import (
    DEFAULT_EMISSION_QUERY_SECONDS,
    MAX_EMISSION_QUERY_SECONDS,
    _get_emission_log,
)
from .bridge_http import async_get_bridge_session
from .const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    DEFAULT_BRIDGE_URL,
    DOMAIN,
    SERVICE_RECENT_ALEXA_EMISSIONS,
)
from .runtime import _entry_verify_ssl, _get_primary_entry

_LOGGER = logging.getLogger(__name__)


async def _async_require_admin(hass: HomeAssistant, call: ServiceCall) -> None:
    """Restrict a service to admin users.

    Calls without a user context (automations, scripts, system) are allowed.
    """
    user_id = getattr(call.context, "user_id", None)
    if not user_id:
        return
    user = await hass.auth.async_get_user(user_id)
    if user is None or not user.is_admin:
        raise Unauthorized()


def async_register_bridge_services(hass: HomeAssistant, _entry: ConfigEntry) -> None:
    """Register admin-only bridge and emission diagnostic services once.

    Services are domain singletons: skip registration when already present so a
    second config entry can load without re-registering. Handlers resolve the
    active entry dynamically via ``_get_primary_entry``.
    """
    BRIDGE_LOGS_SCHEMA = vol.Schema({
        vol.Optional("limit", default=100): vol.All(int, vol.Range(min=1, max=500)),
        vol.Optional("level", default=""): vol.In(["", "error", "warn", "info"]),
    })

    if not hass.services.has_service(DOMAIN, "whatsapp_bridge_logs"):
        async def handle_bridge_logs(call: ServiceCall) -> dict:
            """Fetch logs from the WhatsApp bridge."""
            await _async_require_admin(hass, call)

            active = _get_primary_entry(hass)
            if active is None:
                return {"error": "no config entry loaded"}

            bridge_url = active.data.get(CONF_BRIDGE_URL, "") or DEFAULT_BRIDGE_URL
            bridge_token = active.data.get(CONF_BRIDGE_TOKEN, "")

            if not bridge_url:
                return {"error": "bridge_url not configured"}

            url = bridge_url.rstrip("/") + "/logs"
            params = {"limit": str(call.data.get("limit", 100))}
            level = call.data.get("level", "").strip()
            if level:
                params["level"] = level

            headers = {"Authorization": f"Bearer {bridge_token}"}
            http_session = async_get_bridge_session(hass, _entry_verify_ssl(active))

            try:
                async with http_session.get(
                    url,
                    headers=headers,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        return {"error": f"HTTP {resp.status}", "body": await resp.text()}
                    return await resp.json()
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

        hass.services.async_register(
            DOMAIN,
            "whatsapp_bridge_logs",
            handle_bridge_logs,
            schema=BRIDGE_LOGS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, "whatsapp_bridge_restart"):
        async def handle_bridge_restart(call: ServiceCall) -> dict:
            """Restart the WhatsApp bridge (soft reconnect)."""
            await _async_require_admin(hass, call)

            active = _get_primary_entry(hass)
            if active is None:
                return {"error": "no config entry loaded"}

            bridge_url = active.data.get(CONF_BRIDGE_URL, "") or DEFAULT_BRIDGE_URL
            bridge_token = active.data.get(CONF_BRIDGE_TOKEN, "")

            if not bridge_url:
                return {"error": "bridge_url not configured"}

            url = bridge_url.rstrip("/") + "/restart"
            headers = {
                "Authorization": f"Bearer {bridge_token}",
                "Content-Type": "application/json",
            }
            http_session = async_get_bridge_session(hass, _entry_verify_ssl(active))

            try:
                async with http_session.post(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return {"error": f"HTTP {resp.status}", "body": await resp.text()}
                    return await resp.json()
            except Exception as exc:  # noqa: BLE001
                return {"error": str(exc)}

        hass.services.async_register(
            DOMAIN,
            "whatsapp_bridge_restart",
            handle_bridge_restart,
            schema=vol.Schema({}),
            supports_response=SupportsResponse.ONLY,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_RECENT_ALEXA_EMISSIONS):
        RECENT_EMISSIONS_SCHEMA = vol.Schema({
            vol.Optional("within_seconds", default=DEFAULT_EMISSION_QUERY_SECONDS): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=MAX_EMISSION_QUERY_SECONDS)
            ),
            vol.Optional("local_only", default=False): cv.boolean,
        })

        async def handle_recent_alexa_emissions(call: ServiceCall) -> dict:
            """Return Alexa TTS emissions from the in-memory log (no persistence).

            Intended for real-time correlation ("was that noise just our own
            announcement?") and for diagnostics. Admin-only: the entries carry a
            snippet of what was spoken.
            """
            await _async_require_admin(hass, call)

            log = _get_emission_log(hass)
            if log is None:
                return {"emissions": [], "count": 0, "available": False}

            within = timedelta(
                seconds=call.data.get("within_seconds", DEFAULT_EMISSION_QUERY_SECONDS)
            )
            emissions = log.recent(within, local_only=call.data.get("local_only", False))
            return {
                "emissions": [e.as_dict() for e in emissions],
                "count": len(emissions),
                "available": True,
            }

        hass.services.async_register(
            DOMAIN,
            SERVICE_RECENT_ALEXA_EMISSIONS,
            handle_recent_alexa_emissions,
            schema=RECENT_EMISSIONS_SCHEMA,
            supports_response=SupportsResponse.ONLY,
        )

    _LOGGER.debug("Bridge / emission diagnostic services ensured")
