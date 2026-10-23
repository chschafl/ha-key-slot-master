"""Whether the PIN is currently live on the lock."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import KeySlotConfigEntry
from .entity import KeySlotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: KeySlotConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([KeySlotAccessActive(entry.runtime_data, entry.title, "access_active")])


class KeySlotAccessActive(KeySlotEntity, BinarySensorEntity):
    """On when the lock reports the slot as enabled — i.e. the PIN opens the door.

    This reflects the lock, not the desired state: it stays off while a sync
    is pending or failed.
    """

    @property
    def is_on(self) -> bool:
        return self.coordinator.access_active
