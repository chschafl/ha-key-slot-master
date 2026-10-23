"""Entities and the set_slot service, driven through Home Assistant."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.util import dt as dt_util

from custom_components.key_slot_master.const import DOMAIN
from custom_components.key_slot_master.zwave import STATUS_ENABLED

CODE = "text.backdoor_slot_3_code"
ENABLED = "switch.backdoor_slot_3_enabled"
FROM = "datetime.backdoor_slot_3_active_from"
TO = "datetime.backdoor_slot_3_active_to"
SYNC = "sensor.backdoor_slot_3_sync_status"
ACTIVE = "binary_sensor.backdoor_slot_3_active_on_lock"
CLEAR = "button.backdoor_slot_3_clear_active_window"
RESYNC = "button.backdoor_slot_3_resync"


async def test_all_entities_created(hass: HomeAssistant, fake_lock, setup_entry):
    fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
    await setup_entry()
    assert hass.states.get(CODE).state == "1234"
    assert hass.states.get(ENABLED).state == "on"
    assert hass.states.get(FROM).state == "unknown"
    assert hass.states.get(TO).state == "unknown"
    sync = hass.states.get(SYNC)
    assert sync.state == "in_sync"
    assert sync.attributes["slot"] == 3
    assert sync.attributes["options"] == ["in_sync", "syncing", "error"]
    assert hass.states.get(ACTIVE).state == "on"
    assert hass.states.get(CLEAR) is not None
    assert hass.states.get(RESYNC) is not None


async def test_entities_drive_the_lock(hass: HomeAssistant, fake_lock, setup_entry):
    await setup_entry()
    await hass.services.async_call(
        "text", "set_value", {"entity_id": CODE, "value": "2580"}, blocking=True
    )
    await hass.services.async_call("switch", "turn_on", {"entity_id": ENABLED}, blocking=True)
    assert fake_lock.writes == [("set", "2580")]
    assert hass.states.get(ACTIVE).state == "on"

    start = dt_util.utcnow() + timedelta(days=1)
    await hass.services.async_call(
        "datetime", "set_value", {"entity_id": FROM, "datetime": start}, blocking=True
    )
    assert fake_lock.writes[-1] == ("clear", None)
    assert hass.states.get(ACTIVE).state == "off"
    assert dt_util.parse_datetime(hass.states.get(FROM).state) == start.replace(microsecond=0)

    await hass.services.async_call("button", "press", {"entity_id": CLEAR}, blocking=True)
    assert hass.states.get(FROM).state == "unknown"
    assert fake_lock.writes[-1] == ("set", "2580")


async def test_text_entity_rejects_non_digits(hass: HomeAssistant, setup_entry):
    await setup_entry()
    with pytest.raises(Exception):  # noqa: B017 - pattern check raises ValueError
        await hass.services.async_call(
            "text", "set_value", {"entity_id": CODE, "value": "abcd"}, blocking=True
        )


async def test_resync_button_reads_the_lock(hass: HomeAssistant, fake_lock, setup_entry):
    await setup_entry()
    await hass.services.async_call("button", "press", {"entity_id": RESYNC}, blocking=True)
    assert fake_lock.refreshes == 1


async def test_set_slot_service(hass: HomeAssistant, fake_lock, setup_entry):
    entry = await setup_entry()
    device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    start = dt_util.utcnow() - timedelta(hours=1)
    await hass.services.async_call(
        DOMAIN,
        "set_slot",
        {
            "device_id": device.id,
            "code": 13579,
            "enabled": True,
            "active_from": start.isoformat(),
            "active_to": (start + timedelta(days=3)).isoformat(),
        },
        blocking=True,
    )
    assert fake_lock.writes == [("set", "13579")]
    assert hass.states.get(SYNC).state == "in_sync"

    await hass.services.async_call(
        DOMAIN, "set_slot", {"device_id": device.id, "active_from": None}, blocking=True
    )
    assert hass.states.get(FROM).state == "unknown"


async def test_set_slot_unknown_device(hass: HomeAssistant, setup_entry):
    await setup_entry()
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "set_slot", {"device_id": "nope", "enabled": True}, blocking=True
        )


async def test_slot_device_hangs_off_the_lock_device(hass: HomeAssistant, fake_lock, setup_entry):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    zwave_entry = MockConfigEntry(domain="zwave_js")
    zwave_entry.add_to_hass(hass)
    lock_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=zwave_entry.entry_id, identifiers={("zwave_js", "123-7")}, name="Backdoor"
    )
    fake_lock.device_id = lock_device.id
    entry = await setup_entry()
    slot_device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0]
    assert slot_device.via_device_id == lock_device.id
    assert slot_device.name == "Backdoor slot 3"


async def test_unload(hass: HomeAssistant, setup_entry):
    entry = await setup_entry()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(CODE).state == "unavailable"


async def test_remove_forgets_state_but_not_the_lock(
    hass: HomeAssistant, fake_lock, setup_entry, hass_storage
):
    from custom_components.key_slot_master.const import STORAGE_KEY

    fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
    entry = await setup_entry()
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert STORAGE_KEY.format(entry_id=entry.entry_id) not in hass_storage
    assert fake_lock.writes == []
