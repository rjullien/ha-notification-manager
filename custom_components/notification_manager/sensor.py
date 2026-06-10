"""Sensor platform for Notification Manager — WhatsApp bridge status."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.loader import async_get_integration

from .const import (
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

    # Version from the integration manifest, via HA's loader (already cached —
    # no manual manifest.json read, no blocking file I/O in the event loop).
    integration = await async_get_integration(hass, DOMAIN)
    sw_version = str(integration.version) if integration.version else "unknown"

    # Start polling (non-blocking; sensor shows 'unknown' until first successful poll)
    # Do NOT use async_config_entry_first_refresh — if bridge is unreachable
    # it raises ConfigEntryNotReady and prevents the whole integration from loading.
    coordinator.async_set_updated_data(SENSOR_STATE_UNKNOWN)

    async_add_entities(
        [NotificationManagerSensor(coordinator, entry, SENSOR_DESCRIPTION, sw_version)]
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
        sw_version: str,
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
            "sw_version": sw_version,
        }

    @property
    def native_value(self) -> str:
        """Return the bridge status."""
        return self.coordinator.data or SENSOR_STATE_UNKNOWN

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return additional attributes.

        The bridge URL is intentionally NOT exposed here: state attributes are
        visible to every HA user and persisted by the recorder, which leaked
        internal infrastructure details (Tailscale hostname / internal IP).
        """
        return {
            "last_update_success": self.coordinator.last_update_success,
        }
