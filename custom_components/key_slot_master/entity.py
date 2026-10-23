"""Base entity for Key Slot Master."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import KeySlotCoordinator


class KeySlotEntity(Entity):
    """Shared plumbing: device grouping and coordinator updates."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, coordinator: KeySlotCoordinator, title: str, key: str) -> None:
        self.coordinator = coordinator
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.entry_id}_{key}"
        info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.entry_id)},
            name=title,
            manufacturer="Key Slot Master",
            model="Z-Wave key slot",
            serial_number=f"slot {coordinator.backend.slot}",
        )
        # Nest the slot under the lock in the device page ("Connected via").
        if lock_device_id := coordinator.backend.device_id:
            info["via_device_id"] = lock_device_id
        self._attr_device_info = info

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.coordinator.async_add_listener(self.async_write_ha_state))
