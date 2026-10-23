"""Config flow for Key Slot Master: pick a Z-Wave lock and a slot number."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import CONF_LOCK_ENTITY_ID, CONF_LOCK_REGISTRY_ID, CONF_NAME, CONF_SLOT, DOMAIN
from .zwave import ZWAVE_DOMAIN, LockUnavailableError, ZWaveLockBackend

_LOGGER = logging.getLogger(__name__)


class KeySlotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """One config entry (and device) per lock + slot."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"max_slot": "?"}

        if user_input is not None:
            entity_id: str = user_input[CONF_LOCK_ENTITY_ID]
            slot = int(user_input[CONF_SLOT])
            registry_entry = er.async_get(self.hass).async_get(entity_id)

            if registry_entry is None or registry_entry.platform != ZWAVE_DOMAIN:
                errors[CONF_LOCK_ENTITY_ID] = "not_zwave_lock"
            else:
                await self.async_set_unique_id(f"{registry_entry.id}_{slot}")
                self._abort_if_unique_id_configured()
                backend = ZWaveLockBackend(self.hass, registry_entry.id, slot)
                try:
                    max_slot = backend.max_slot()
                except LockUnavailableError as err:
                    _LOGGER.debug("Lock lookup failed: %s", err)
                    errors["base"] = "lock_unavailable"
                else:
                    placeholders["max_slot"] = str(max_slot)
                    if max_slot == 0:
                        errors[CONF_LOCK_ENTITY_ID] = "no_user_codes"
                    elif not 1 <= slot <= max_slot:
                        errors[CONF_SLOT] = "invalid_slot"

            if not errors:
                state = self.hass.states.get(entity_id)
                lock_name = state.name if state else entity_id
                title = (user_input.get(CONF_NAME) or "").strip() or f"{lock_name} slot {slot}"
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_LOCK_ENTITY_ID: entity_id,
                        CONF_LOCK_REGISTRY_ID: registry_entry.id,
                        CONF_SLOT: slot,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_LOCK_ENTITY_ID): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="lock", integration=ZWAVE_DOMAIN)
                ),
                vol.Required(CONF_SLOT, default=1): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=1, max=255, step=1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(CONF_NAME): selector.TextSelector(),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
            description_placeholders=placeholders,
        )
