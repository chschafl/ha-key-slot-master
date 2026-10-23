"""PIN code of the key slot."""

from __future__ import annotations

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KeySlotConfigEntry
from .const import CODE_MAX_LENGTH, CODE_PATTERN
from .entity import KeySlotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KeySlotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([KeySlotCode(entry.runtime_data, entry.title, "code")])


class KeySlotCode(KeySlotEntity, TextEntity):
    """The slot's PIN. Empty means no code (the slot is cleared on the lock)."""

    _attr_mode = TextMode.TEXT
    _attr_native_min = 0
    _attr_native_max = CODE_MAX_LENGTH
    _attr_pattern = CODE_PATTERN

    @property
    def native_value(self) -> str:
        return self.coordinator.config.code or ""

    async def async_set_value(self, value: str) -> None:
        await self.coordinator.async_update_config(code=value.strip())
