"""Shared test fixtures for notification_manager tests."""
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Mock homeassistant modules so we can import the component without HA installed
ha_mock = MagicMock()
ha_mock.core.HomeAssistant = MagicMock
ha_mock.core.ServiceCall = MagicMock
ha_mock.config_entries.ConfigEntry = MagicMock
ha_mock.helpers.config_validation = MagicMock()
ha_mock.helpers.config_validation.string = str
ha_mock.helpers.aiohttp_client.async_get_clientsession = MagicMock()
ha_mock.helpers.update_coordinator.DataUpdateCoordinator = MagicMock
ha_mock.helpers.update_coordinator.UpdateFailed = Exception
ha_mock.helpers.entity_platform.AddEntitiesCallback = MagicMock
ha_mock.components.sensor.SensorEntity = MagicMock
ha_mock.components.sensor.SensorEntityDescription = MagicMock

modules_to_mock = {
    "homeassistant": ha_mock,
    "homeassistant.core": ha_mock.core,
    "homeassistant.config_entries": ha_mock.config_entries,
    "homeassistant.helpers": ha_mock.helpers,
    "homeassistant.helpers.config_validation": ha_mock.helpers.config_validation,
    "homeassistant.helpers.aiohttp_client": ha_mock.helpers.aiohttp_client,
    "homeassistant.helpers.update_coordinator": ha_mock.helpers.update_coordinator,
    "homeassistant.helpers.entity_platform": ha_mock.helpers.entity_platform,
    "homeassistant.components": ha_mock.components,
    "homeassistant.components.sensor": ha_mock.components.sensor,
    "homeassistant.data_entry_flow": MagicMock(),
    "voluptuous": MagicMock(),
    "aiohttp": MagicMock(),
}

for mod_name, mod_mock in modules_to_mock.items():
    sys.modules.setdefault(mod_name, mod_mock)

# Add the component to the path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "custom_components"))
