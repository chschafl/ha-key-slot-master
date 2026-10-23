"""Active-from / active-to window of the key slot."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.datetime import DateTimeEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KeySlotConfigEntry
from .entity import KeySlotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KeySlotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        [
            KeySlotWindow(coordinator, entry.title, "active_from"),
            KeySlotWindow(coordinator, entry.title, "active_to"),
        ]
    )


class KeySlotWindow(KeySlotEntity, DateTimeEntity):
    """One bound of the active window. Unknown means unbounded on that side."""

    @property
    def native_value(self) -> datetime | None:
        return getattr(self.coordinator.config, self._attr_translation_key)

    async def async_set_value(self, value: datetime) -> None:
        await self.coordinator.async_update_config(**{self._attr_translation_key: value})
