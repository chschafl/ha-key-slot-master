"""Sync status of the key slot."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KeySlotConfigEntry
from .const import SYNC_STATES
from .entity import KeySlotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KeySlotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([KeySlotSyncStatus(entry.runtime_data, entry.title, "sync_status")])


class KeySlotSyncStatus(KeySlotEntity, SensorEntity):
    """in_sync / syncing / error."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = SYNC_STATES

    @property
    def native_value(self) -> str:
        return self.coordinator.sync_state

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        coordinator = self.coordinator
        return {
            "lock_entity_id": coordinator.backend.entity_id,
            "slot": coordinator.backend.slot,
            "last_synced": coordinator.last_synced.isoformat() if coordinator.last_synced else None,
            "last_error": coordinator.last_error,
        }
