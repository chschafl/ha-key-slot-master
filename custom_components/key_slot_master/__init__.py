"""Key Slot Master — manage individual user-code slots of Z-Wave locks."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .const import (
    ATTR_ACTIVE_FROM,
    ATTR_ACTIVE_TO,
    ATTR_CODE,
    ATTR_ENABLED,
    CONF_LOCK_ENTITY_ID,
    CONF_LOCK_REGISTRY_ID,
    CONF_SLOT,
    DOMAIN,
    PLATFORMS,
    SERVICE_SET_SLOT,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import KeySlotCoordinator
from .zwave import LockUnavailableError, ZWaveLockBackend

_LOGGER = logging.getLogger(__name__)

type KeySlotConfigEntry = ConfigEntry[KeySlotCoordinator]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

SET_SLOT_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_CODE): vol.Any(None, cv.string),
        vol.Optional(ATTR_ENABLED): cv.boolean,
        vol.Optional(ATTR_ACTIVE_FROM): vol.Any(None, cv.datetime),
        vol.Optional(ATTR_ACTIVE_TO): vol.Any(None, cv.datetime),
    }
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the domain services."""

    async def _set_slot(call: ServiceCall) -> None:
        changes = {
            key: call.data[key]
            for key in (ATTR_CODE, ATTR_ENABLED, ATTR_ACTIVE_FROM, ATTR_ACTIVE_TO)
            if key in call.data
        }
        if ATTR_CODE in changes and changes[ATTR_CODE] is not None:
            changes[ATTR_CODE] = str(changes[ATTR_CODE]).strip()
        for coordinator in _coordinators_for_devices(hass, call.data[ATTR_DEVICE_ID]):
            await coordinator.async_update_config(**changes)

    hass.services.async_register(DOMAIN, SERVICE_SET_SLOT, _set_slot, schema=SET_SLOT_SCHEMA)
    return True


def _coordinators_for_devices(
    hass: HomeAssistant, device_ids: list[str]
) -> list[KeySlotCoordinator]:
    dev_reg = dr.async_get(hass)
    coordinators: list[KeySlotCoordinator] = []
    for device_id in device_ids:
        device = dev_reg.async_get(device_id)
        entry = next(
            (
                e
                for e in hass.config_entries.async_loaded_entries(DOMAIN)
                if device and e.entry_id in device.config_entries
            ),
            None,
        )
        if entry is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_device",
                translation_placeholders={"device_id": device_id},
            )
        coordinators.append(entry.runtime_data)
    return coordinators


async def async_setup_entry(hass: HomeAssistant, entry: KeySlotConfigEntry) -> bool:
    """Set up one key slot from a config entry."""
    lock_ref = entry.data.get(CONF_LOCK_REGISTRY_ID) or entry.data[CONF_LOCK_ENTITY_ID]
    backend = ZWaveLockBackend(hass, lock_ref, int(entry.data[CONF_SLOT]))
    coordinator = KeySlotCoordinator(hass, entry.entry_id, backend)
    try:
        await coordinator.async_setup()
    except LockUnavailableError as err:
        await coordinator.async_shutdown()
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_shutdown)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: KeySlotConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass: HomeAssistant, entry: KeySlotConfigEntry) -> None:
    """Forget the stored slot state. The PIN on the lock is left untouched."""
    await Store(hass, STORAGE_VERSION, STORAGE_KEY.format(entry_id=entry.entry_id)).async_remove()
