"""Buttons: clear the active window, resync with the lock."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
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
            KeySlotClearWindow(coordinator, entry.title, "clear_window"),
            KeySlotResync(coordinator, entry.title, "resync"),
        ]
    )


class KeySlotClearWindow(KeySlotEntity, ButtonEntity):
    """Remove both window bounds — the slot is then active whenever enabled.

    Date/time entities can't be emptied from the UI, hence the button.
    """

    async def async_press(self) -> None:
        await self.coordinator.async_update_config(active_from=None, active_to=None)


class KeySlotResync(KeySlotEntity, ButtonEntity):
    """Read the slot from the lock and retry a failed sync right away."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        await self.coordinator.async_resync()
