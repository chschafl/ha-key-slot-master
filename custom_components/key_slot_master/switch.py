"""Enable / disable the key slot."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KeySlotConfigEntry
from .entity import KeySlotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KeySlotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([KeySlotEnabled(entry.runtime_data, entry.title, "enabled")])


class KeySlotEnabled(KeySlotEntity, SwitchEntity):
    """Off clears the PIN from the lock; the code is remembered for later."""

    @property
    def is_on(self) -> bool:
        return self.coordinator.config.enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_config(enabled=True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_update_config(enabled=False)
