"""Sensor platform for Notification Manager — WhatsApp bridge status."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    DOMAIN,
    SENSOR_STATE_UNKNOWN,
)
from .coordinator import NotificationManagerCoordinator

_LOGGER = logging.getLogger(__name__)

SENSOR_DESCRIPTION = SensorEntityDescription(
    key="whatsapp_status",
    name="WhatsApp Bridge Status",
    icon="mdi:whatsapp",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the WhatsApp status sensor."""
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator = NotificationManagerCoordinator(hass, entry_data)

    # Store coordinator so __init__ can update it when options change
    hass.data[DOMAIN][entry.entry_id]["coordinator"] = coordinator

    # Initial refresh (non-blocking; will show unknown until first poll)
    await coordinator.async_config_entry_first_refresh()

    async_add_entities(
        [NotificationManagerSensor(coordinator, entry, SENSOR_DESCRIPTION)]
    )


class NotificationManagerSensor(
    CoordinatorEntity[NotificationManagerCoordinator], SensorEntity
):
    """Sensor representing the WhatsApp bridge connection status."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: NotificationManagerCoordinator,
        entry: ConfigEntry,
        description: SensorEntityDescription,
    ) -> None:
        """Initialise sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "Notification Manager",
            "manufacturer": "rjullien",
            "model": "Notification Manager",
            "sw_version": "1.0.1",
        }

    @property
    def native_value(self) -> str:
        """Return the bridge status."""
        return self.coordinator.data or SENSOR_STATE_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes."""
        return {
            "bridge_url": self.coordinator.bridge_url,
            "last_update_success": self.coordinator.last_update_success,
        }
