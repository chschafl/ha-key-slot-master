"""Config flow: pick a Z-Wave lock and slot."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.key_slot_master.const import DOMAIN
from custom_components.key_slot_master.zwave import LockUnavailableError

from .conftest import FakeLock


def _register_lock(hass: HomeAssistant, platform: str = "zwave_js") -> er.RegistryEntry:
    entry = er.async_get(hass).async_get_or_create(
        "lock", platform, "3245146787-7-98-0", suggested_object_id="backdoor_lock"
    )
    assert entry.entity_id == "lock.backdoor_lock"
    hass.states.async_set(entry.entity_id, "locked", {"friendly_name": "Backdoor"})
    return entry


async def _start(hass):
    hass.config.components.add("zwave_js")
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_creates_entry(hass: HomeAssistant):
    reg = _register_lock(hass)
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    with (
        patch(
            "custom_components.key_slot_master.config_flow.ZWaveLockBackend",
            return_value=FakeLock(),
        ),
        patch("custom_components.key_slot_master.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"lock_entity_id": "lock.backdoor_lock", "slot": 3}
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Backdoor slot 3"
    assert result["data"] == {
        "lock_entity_id": "lock.backdoor_lock",
        "lock_registry_id": reg.id,
        "slot": 3,
    }
    assert result["result"].unique_id == f"{reg.id}_3"


async def test_custom_name_and_several_slots_per_lock(hass: HomeAssistant):
    reg = _register_lock(hass)
    MockConfigEntry(domain=DOMAIN, unique_id=f"{reg.id}_3").add_to_hass(hass)
    result = await _start(hass)
    with (
        patch(
            "custom_components.key_slot_master.config_flow.ZWaveLockBackend",
            return_value=FakeLock(),
        ),
        patch("custom_components.key_slot_master.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"lock_entity_id": "lock.backdoor_lock", "slot": 4, "name": "Cleaner"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Cleaner"


async def test_same_slot_twice_aborts(hass: HomeAssistant):
    reg = _register_lock(hass)
    MockConfigEntry(domain=DOMAIN, unique_id=f"{reg.id}_3").add_to_hass(hass)
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"lock_entity_id": "lock.backdoor_lock", "slot": 3}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_non_zwave_lock_rejected(hass: HomeAssistant):
    _register_lock(hass, platform="template")
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"lock_entity_id": "lock.backdoor_lock", "slot": 3}
    )
    assert result["errors"] == {"lock_entity_id": "not_zwave_lock"}


async def test_slot_out_of_range(hass: HomeAssistant):
    _register_lock(hass)
    result = await _start(hass)
    with patch(
        "custom_components.key_slot_master.config_flow.ZWaveLockBackend", return_value=FakeLock()
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"lock_entity_id": "lock.backdoor_lock", "slot": 31}
        )
    assert result["errors"] == {"slot": "invalid_slot"}
    assert result["description_placeholders"]["max_slot"] == "30"


async def test_lock_unavailable(hass: HomeAssistant):
    _register_lock(hass)
    result = await _start(hass)
    with patch(
        "custom_components.key_slot_master.config_flow.ZWaveLockBackend.max_slot",
        side_effect=LockUnavailableError("down"),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"lock_entity_id": "lock.backdoor_lock", "slot": 3}
        )
    assert result["errors"] == {"base": "lock_unavailable"}
