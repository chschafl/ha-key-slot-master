"""ZWaveLockBackend against a stand-in Z-Wave JS node."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.key_slot_master.zwave import (
    STATUS_AVAILABLE,
    STATUS_ENABLED,
    LockSlotState,
    LockUnavailableError,
    ZWaveLockBackend,
)

RESOLVE = "homeassistant.components.zwave_js.helpers.async_get_node_from_entity_id"


def _value(node_id: int, prop: str, slot: int, value):
    return SimpleNamespace(
        command_class=99,
        property_=prop,
        property_key=slot,
        value=value,
        metadata=SimpleNamespace(label=f"User Code ({slot})"),
        value_id=f"{node_id}-99-0-{prop}-{slot}",
    )


class FakeNode:
    def __init__(self, node_id: int = 7, slots: int = 5) -> None:
        self.node_id = node_id
        self.values = {}
        for slot in range(1, slots + 1):
            for prop, val in (("userIdStatus", STATUS_AVAILABLE), ("userCode", "")):
                v = _value(node_id, prop, slot, val)
                self.values[v.value_id] = v
        self.listeners = []
        self.async_invoke_cc_api = AsyncMock()

    def set_slot(self, slot: int, status: int, code: str) -> None:
        self.values[f"{self.node_id}-99-0-userIdStatus-{slot}"].value = status
        self.values[f"{self.node_id}-99-0-userCode-{slot}"].value = code

    def on(self, event, cb):
        assert event == "value updated"
        self.listeners.append(cb)
        return lambda: self.listeners.remove(cb)

    def emit(self, value) -> None:
        for cb in list(self.listeners):
            cb({"value": value})


@pytest.fixture
def lock_entry(hass: HomeAssistant) -> er.RegistryEntry:
    hass.config.components.add("zwave_js")
    return er.async_get(hass).async_get_or_create(
        "lock", "zwave_js", "home-7-98-0", suggested_object_id="backdoor_lock"
    )


async def test_reads_slot_from_value_cache(hass, lock_entry):
    node = FakeNode()
    node.set_slot(3, STATUS_ENABLED, "1234")
    backend = ZWaveLockBackend(hass, lock_entry.id, 3)
    with patch(RESOLVE, return_value=node):
        assert backend.read() == LockSlotState(STATUS_ENABLED, "1234")
        assert backend.max_slot() == 5
    assert node.async_invoke_cc_api.await_count == 0  # cache only, no radio


async def test_follows_entity_renames(hass, lock_entry):
    backend = ZWaveLockBackend(hass, lock_entry.id, 3)
    er.async_get(hass).async_update_entity(lock_entry.entity_id, new_entity_id="lock.front")
    assert backend.entity_id == "lock.front"


async def test_masked_and_empty_codes(hass):
    assert LockSlotState(STATUS_ENABLED, "****").masked
    assert LockSlotState(STATUS_ENABLED, None).masked
    assert not LockSlotState(STATUS_ENABLED, "1234").masked
    assert not LockSlotState(None, None).known


async def test_missing_slot_raises(hass, lock_entry):
    backend = ZWaveLockBackend(hass, lock_entry.id, 9)
    with patch(RESOLVE, return_value=FakeNode()), pytest.raises(LockUnavailableError):
        backend.read()


async def test_unresolvable_node_raises(hass, lock_entry):
    backend = ZWaveLockBackend(hass, lock_entry.id, 3)
    with patch(RESOLVE, side_effect=ValueError("not loaded")), pytest.raises(LockUnavailableError):
        backend.read()


async def test_deleted_lock_raises(hass, lock_entry):
    backend = ZWaveLockBackend(hass, lock_entry.id, 3)
    er.async_get(hass).async_remove(lock_entry.entity_id)
    with pytest.raises(LockUnavailableError):
        backend.read()


async def test_subscription_filters_to_this_slot(hass, lock_entry):
    node = FakeNode()
    backend = ZWaveLockBackend(hass, lock_entry.id, 3)
    calls = []
    with patch(RESOLVE, return_value=node):
        backend.async_ensure_node()
        unsub = backend.async_subscribe(lambda: calls.append(1))
        node.emit(node.values["7-99-0-userCode-3"])
        node.emit(node.values["7-99-0-userIdStatus-3"])
        node.emit(node.values["7-99-0-userCode-4"])  # other slot
        node.emit(SimpleNamespace(command_class=98, property_="currentMode", property_key=None))
        assert len(calls) == 2
        unsub()
        assert node.listeners == []


async def test_rebinds_after_zwave_reload(hass, lock_entry):
    old, new = FakeNode(), FakeNode()
    backend = ZWaveLockBackend(hass, lock_entry.id, 3)
    calls = []
    backend.async_subscribe(lambda: calls.append(1))
    with patch(RESOLVE, return_value=old):
        backend.async_ensure_node()
    with patch(RESOLVE, return_value=new):
        backend.async_ensure_node()
    assert old.listeners == [] and len(new.listeners) == 1
    new.emit(new.values["7-99-0-userCode-3"])
    assert calls == [1]


async def test_refresh_queries_the_lock(hass, lock_entry):
    node = FakeNode()
    backend = ZWaveLockBackend(hass, lock_entry.id, 3)
    with patch(RESOLVE, return_value=node):
        await backend.async_refresh()
    node.async_invoke_cc_api.assert_awaited_once_with(99, "get", 3, wait_for_result=True)


async def test_writes_use_zwave_js_services(hass, lock_entry):
    set_calls = async_mock_service(hass, "zwave_js", "set_lock_usercode")
    clear_calls = async_mock_service(hass, "zwave_js", "clear_lock_usercode")
    backend = ZWaveLockBackend(hass, lock_entry.id, 3)
    await backend.async_set_code("4711")
    await backend.async_clear()
    assert set_calls[0].data == {
        "entity_id": "lock.backdoor_lock",
        "code_slot": 3,
        "usercode": "4711",
    }
    assert clear_calls[0].data == {"entity_id": "lock.backdoor_lock", "code_slot": 3}
