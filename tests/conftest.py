"""Shared test fixtures for notification_manager tests."""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock homeassistant modules so we can import the component without HA installed
ha_mock = MagicMock()
ha_mock.core.HomeAssistant = MagicMock
ha_mock.core.ServiceCall = MagicMock
# Passthrough decorator — bridge_http decorates async_get_bridge_session with
# @callback; a MagicMock decorator would replace the real function.
ha_mock.core.callback = lambda f: f
ha_mock.config_entries.ConfigEntry = MagicMock
ha_mock.exceptions.Unauthorized = type("Unauthorized", (Exception,), {})
ha_mock.helpers.config_validation = MagicMock()
ha_mock.helpers.config_validation.string = str
ha_mock.helpers.aiohttp_client.async_get_clientsession = MagicMock()
ha_mock.helpers.update_coordinator.DataUpdateCoordinator = MagicMock
ha_mock.helpers.update_coordinator.UpdateFailed = Exception
ha_mock.helpers.entity_platform.AddEntitiesCallback = MagicMock
ha_mock.components.sensor.SensorEntity = MagicMock
ha_mock.components.sensor.SensorEntityDescription = MagicMock
ha_mock.loader.async_get_integration = AsyncMock()

# aiohttp mock: bridge_http.py subclasses aiohttp.abc.AbstractResolver and
# imports aiohttp.resolver.DefaultResolver — both need importable stand-ins.
aiohttp_mock = MagicMock()
aiohttp_mock.abc.AbstractResolver = type("AbstractResolver", (), {})
# Real exception classes: `except aiohttp.ClientConnectorError` requires a
# BaseException subclass, a MagicMock attribute would raise TypeError.
aiohttp_mock.ClientConnectorError = type("ClientConnectorError", (Exception,), {})
aiohttp_mock.InvalidURL = type("InvalidURL", (Exception,), {})
aiohttp_mock.ClientTimeout = MagicMock
aiohttp_resolver_mock = MagicMock()
aiohttp_resolver_mock.DefaultResolver = MagicMock

modules_to_mock = {
    "homeassistant": ha_mock,
    "homeassistant.core": ha_mock.core,
    "homeassistant.config_entries": ha_mock.config_entries,
    "homeassistant.exceptions": ha_mock.exceptions,
    "homeassistant.loader": ha_mock.loader,
    "homeassistant.helpers": ha_mock.helpers,
    "homeassistant.helpers.config_validation": ha_mock.helpers.config_validation,
    "homeassistant.helpers.aiohttp_client": ha_mock.helpers.aiohttp_client,
    "homeassistant.helpers.update_coordinator": ha_mock.helpers.update_coordinator,
    "homeassistant.helpers.entity_platform": ha_mock.helpers.entity_platform,
    "homeassistant.components": ha_mock.components,
    "homeassistant.components.sensor": ha_mock.components.sensor,
    "homeassistant.data_entry_flow": MagicMock(),
    "voluptuous": MagicMock(),
    "aiohttp": aiohttp_mock,
    "aiohttp.abc": aiohttp_mock.abc,
    "aiohttp.resolver": aiohttp_resolver_mock,
}

for mod_name, mod_mock in modules_to_mock.items():
    sys.modules.setdefault(mod_name, mod_mock)

# Add the component to the path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "custom_components"))
