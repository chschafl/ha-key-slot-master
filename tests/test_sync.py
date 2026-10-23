"""Sync engine: desired state ↔ Z-Wave lock, with Z-Wave as source of truth."""

from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_capture_events

from custom_components.key_slot_master.const import (
    CONF_LOCK_ENTITY_ID,
    CONF_SLOT,
    DOMAIN,
    EVENT_EXTERNAL_CHANGE,
    RETRY_BACKOFF,
    STORAGE_KEY,
    SYNC_CONFIRM_TIMEOUT,
    SYNC_ERROR,
    SYNC_IN_SYNC,
)
from custom_components.key_slot_master.zwave import (
    STATUS_AVAILABLE,
    STATUS_DISABLED,
    STATUS_ENABLED,
)

from .conftest import LOCK_ENTITY_ID, SLOT, FakeLock, advance


def coord(entry):
    return entry.runtime_data


class TestInitialAdoption:
    async def test_existing_code_on_lock_is_taken_over(self, hass, fake_lock, setup_entry):
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
        c = coord(await setup_entry())
        assert c.config.code == "1234"
        assert c.config.enabled is True
        assert c.sync_state == SYNC_IN_SYNC
        assert fake_lock.writes == []

    async def test_empty_slot_starts_disabled(self, hass, fake_lock, setup_entry):
        c = coord(await setup_entry())
        assert c.config.code is None
        assert c.config.enabled is False
        assert c.sync_state == SYNC_IN_SYNC
        assert fake_lock.writes == []

    async def test_unread_slot_is_queried_once(self, hass, fake_lock, setup_entry):
        fake_lock.status = None
        fake_lock._actual = (STATUS_ENABLED, "5555")
        c = coord(await setup_entry())
        assert fake_lock.refreshes == 1
        assert c.config.code == "5555"

    async def test_slot_never_reported_does_not_wipe_lock(
        self, hass, freezer, fake_lock, setup_entry
    ):
        """No baseline yet → when the slot finally reports, adopt it, don't clear it."""
        fake_lock.status = None
        fake_lock.report_on_refresh = False
        c = coord(await setup_entry())
        assert c.sync_state == SYNC_ERROR
        fake_lock.external_change(STATUS_ENABLED, "9999")
        await advance(hass, freezer, 2)
        assert c.config.code == "9999"
        assert fake_lock.writes == []


class TestUserChanges:
    async def test_set_code_and_enable_programs_lock(self, hass, fake_lock, setup_entry):
        c = coord(await setup_entry())
        await c.async_update_config(code="4711", enabled=True)
        assert fake_lock.writes == [("set", "4711")]
        assert c.sync_state == SYNC_IN_SYNC
        assert c.access_active is True

    async def test_disable_clears_lock_but_remembers_code(self, hass, fake_lock, setup_entry):
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
        c = coord(await setup_entry())
        await c.async_update_config(enabled=False)
        assert fake_lock.writes == [("clear", None)]
        assert c.config.code == "1234"
        assert c.access_active is False

        await c.async_update_config(enabled=True)
        assert fake_lock.writes[-1] == ("set", "1234")

    async def test_empty_code_clears_lock(self, hass, fake_lock, setup_entry):
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
        c = coord(await setup_entry())
        await c.async_update_config(code="")
        assert fake_lock.writes == [("clear", None)]
        assert c.config.code is None

    async def test_no_write_when_nothing_changes(self, hass, fake_lock, setup_entry):
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
        c = coord(await setup_entry())
        await c.async_update_config(code="1234", enabled=True)
        assert fake_lock.writes == []

    async def test_enabling_without_code_does_not_touch_lock(self, hass, fake_lock, setup_entry):
        c = coord(await setup_entry())
        await c.async_update_config(enabled=True)
        assert fake_lock.writes == []
        assert c.sync_state == SYNC_IN_SYNC

    @pytest.mark.parametrize("bad", ["12", "12345678901", "12a4", "12 34"])
    async def test_invalid_code_rejected(self, hass, fake_lock, setup_entry, bad):
        c = coord(await setup_entry())
        with pytest.raises(ServiceValidationError):
            await c.async_update_config(code=bad)
        assert fake_lock.writes == []

    async def test_window_end_before_start_rejected(self, hass, setup_entry):
        c = coord(await setup_entry())
        now = dt_util.utcnow()
        with pytest.raises(ServiceValidationError):
            await c.async_update_config(active_from=now, active_to=now - timedelta(hours=1))


class TestExternalChanges:
    """Z-Wave is the source of truth."""

    async def test_code_changed_on_lock_is_adopted(self, hass, freezer, fake_lock, setup_entry):
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
        c = coord(await setup_entry())
        events = async_capture_events(hass, EVENT_EXTERNAL_CHANGE)

        fake_lock.external_change(STATUS_ENABLED, "8888")
        await advance(hass, freezer, 2)

        assert c.config.code == "8888"
        assert c.sync_state == SYNC_IN_SYNC
        assert fake_lock.writes == []
        assert len(events) == 1
        assert events[0].data["code_changed"] is True
        assert "8888" not in str(events[0].data)  # never leak the PIN on the bus

    async def test_code_cleared_on_lock_disables_slot(self, hass, freezer, fake_lock, setup_entry):
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
        c = coord(await setup_entry())
        fake_lock.external_change(STATUS_AVAILABLE, None)
        await advance(hass, freezer, 2)
        assert c.config.enabled is False
        assert c.config.code == "1234"
        assert fake_lock.writes == []

    async def test_slot_disabled_on_lock_disables_slot(self, hass, freezer, fake_lock, setup_entry):
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
        c = coord(await setup_entry())
        fake_lock.external_change(STATUS_DISABLED, "1234")
        await advance(hass, freezer, 2)
        assert c.config.enabled is False
        assert fake_lock.writes == []

    async def test_code_added_outside_window_clears_window(
        self, hass, freezer, fake_lock, setup_entry
    ):
        c = coord(await setup_entry())
        start = dt_util.utcnow() + timedelta(days=2)
        await c.async_update_config(
            code="1111", enabled=True, active_from=start, active_to=start + timedelta(days=1)
        )
        assert fake_lock.writes == []  # window not open yet

        fake_lock.external_change(STATUS_ENABLED, "2222")
        await advance(hass, freezer, 2)
        assert c.config.code == "2222"
        assert c.config.active_from is None and c.config.active_to is None
        assert fake_lock.writes == []

    async def test_change_while_ha_was_down_is_adopted(
        self, hass, fake_lock, setup_entry, hass_storage
    ):
        entry = _stored_entry(
            hass_storage,
            config={"code": "1234", "enabled": True},
            lock_state={"status": STATUS_ENABLED, "code": "1234"},
        )
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "7777"
        c = coord(await setup_entry(entry))
        assert c.config.code == "7777"
        assert fake_lock.writes == []

    async def test_pending_desired_state_is_pushed_after_restart(
        self, hass, fake_lock, setup_entry, hass_storage
    ):
        entry = _stored_entry(
            hass_storage,
            config={"code": "4321", "enabled": True},
            lock_state={"status": STATUS_AVAILABLE, "code": None},
        )
        c = coord(await setup_entry(entry))
        assert fake_lock.writes == [("set", "4321")]
        assert c.sync_state == SYNC_IN_SYNC


def _stored_entry(hass_storage, config, lock_state) -> MockConfigEntry:
    """An entry whose slot state was persisted by a previous HA run."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_LOCK_ENTITY_ID: LOCK_ENTITY_ID, CONF_SLOT: SLOT}
    )
    key = STORAGE_KEY.format(entry_id=entry.entry_id)
    hass_storage[key] = {
        "version": 1,
        "key": key,
        "data": {"config": config, "lock_state": lock_state},
    }
    return entry


class TestActiveWindow:
    async def test_code_goes_on_and_off_at_window_bounds(
        self, hass, freezer, fake_lock, setup_entry
    ):
        c = coord(await setup_entry())
        start = dt_util.utcnow() + timedelta(hours=1)
        end = start + timedelta(hours=2)
        await c.async_update_config(code="2468", enabled=True, active_from=start, active_to=end)
        assert fake_lock.writes == []
        assert c.sync_state == SYNC_IN_SYNC

        await advance(hass, freezer, 3601)
        assert fake_lock.writes == [("set", "2468")]
        assert c.access_active

        await advance(hass, freezer, 7200)
        assert fake_lock.writes[-1] == ("clear", None)
        assert not c.access_active
        # Settings survive the window closing; only the lock is cleared.
        assert c.config.enabled and c.config.code == "2468"

    async def test_clearing_window_activates_immediately(self, hass, fake_lock, setup_entry):
        c = coord(await setup_entry())
        start = dt_util.utcnow() + timedelta(days=1)
        await c.async_update_config(code="2468", enabled=True, active_from=start)
        assert fake_lock.writes == []
        await c.async_update_config(active_from=None, active_to=None)
        assert fake_lock.writes == [("set", "2468")]


class TestMaskedLocks:
    async def test_masked_code_counts_as_in_sync(self, hass, fake_lock, setup_entry):
        fake_lock.masked = True
        c = coord(await setup_entry())
        await c.async_update_config(code="1357", enabled=True)
        assert fake_lock.writes == [("set", "1357")]
        assert c.sync_state == SYNC_IN_SYNC
        assert c.config.code == "1357"  # not overwritten with ****

    async def test_code_change_on_masked_lock_is_written(self, hass, fake_lock, setup_entry):
        """Regression: "****" must not count as whatever code we want."""
        fake_lock.masked = True
        c = coord(await setup_entry())
        await c.async_update_config(code="1357", enabled=True)
        await c.async_update_config(code="2468")
        assert fake_lock.writes == [("set", "1357"), ("set", "2468")]
        assert c.sync_state == SYNC_IN_SYNC

    async def test_masked_lock_remembers_code_across_restart(
        self, hass, fake_lock, setup_entry, hass_storage
    ):
        fake_lock.masked = True
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1357"
        entry = _stored_entry(
            hass_storage,
            config={"code": "1357", "enabled": True},
            lock_state={"status": STATUS_ENABLED, "code": "****"},
        )
        hass_storage[STORAGE_KEY.format(entry_id=entry.entry_id)]["data"]["synced_code"] = "1357"
        c = coord(await setup_entry(entry))
        assert fake_lock.writes == []
        assert c.sync_state == SYNC_IN_SYNC

    async def test_masked_existing_code_is_left_alone(self, hass, freezer, fake_lock, setup_entry):
        fake_lock.masked = True
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1357"
        c = coord(await setup_entry())
        assert c.config.enabled is True
        assert c.config.code is None
        await advance(hass, freezer, 3600)
        assert fake_lock.writes == []
        assert c.sync_state == SYNC_IN_SYNC


class TestErrorsAndBattery:
    async def test_failed_write_reports_error_and_retries_a_limited_number_of_times(
        self, hass, freezer, fake_lock, setup_entry
    ):
        c = coord(await setup_entry())
        fake_lock.fail_writes = True
        await c.async_update_config(code="1234", enabled=True)
        assert c.sync_state == SYNC_ERROR
        assert "no ack" in c.last_error

        for delay in RETRY_BACKOFF:
            await advance(hass, freezer, delay + 1)
        assert len(fake_lock.writes) == 1 + len(RETRY_BACKOFF)

        # Out of retries: hours of health checks must not wake the lock again.
        for _ in range(24):
            await advance(hass, freezer, 3600)
        assert len(fake_lock.writes) == 1 + len(RETRY_BACKOFF)
        assert c.sync_state == SYNC_ERROR

    async def test_resync_retries_after_giving_up(self, hass, freezer, fake_lock, setup_entry):
        c = coord(await setup_entry())
        fake_lock.fail_writes = True
        await c.async_update_config(code="1234", enabled=True)
        for delay in RETRY_BACKOFF:
            await advance(hass, freezer, delay + 1)
        fake_lock.fail_writes = False
        await c.async_resync()
        assert c.sync_state == SYNC_IN_SYNC
        assert fake_lock.writes[-1] == ("set", "1234")

    async def test_retry_succeeds_once_lock_recovers(self, hass, freezer, fake_lock, setup_entry):
        c = coord(await setup_entry())
        fake_lock.fail_writes = True
        await c.async_update_config(code="1234", enabled=True)
        fake_lock.fail_writes = False
        await advance(hass, freezer, RETRY_BACKOFF[0] + 1)
        assert c.sync_state == SYNC_IN_SYNC
        assert c.last_error is None

    async def test_unconfirmed_write_is_read_back_once(self, hass, freezer, fake_lock, setup_entry):
        c = coord(await setup_entry())
        fake_lock.confirm_writes = False
        await c.async_update_config(code="1234", enabled=True)
        assert c.sync_state == "syncing"
        await advance(hass, freezer, SYNC_CONFIRM_TIMEOUT + 1)
        assert fake_lock.refreshes == 1
        assert c.sync_state == SYNC_IN_SYNC

    async def test_edit_during_unconfirmed_write_is_sent_immediately(
        self, hass, freezer, fake_lock, setup_entry
    ):
        c = coord(await setup_entry())
        fake_lock.confirm_writes = False
        await c.async_update_config(code="1234", enabled=True)
        await c.async_update_config(code="5678")
        assert fake_lock.writes == [("set", "1234"), ("set", "5678")]
        fake_lock.external_change(STATUS_ENABLED, "5678")  # lock confirms the latest
        await advance(hass, freezer, 2)
        assert c.sync_state == SYNC_IN_SYNC
        assert c.config.code == "5678"

    async def test_lock_never_confirms(self, hass, freezer, fake_lock, setup_entry):
        c = coord(await setup_entry())
        fake_lock.confirm_writes = False
        fake_lock.report_on_refresh = False
        await c.async_update_config(code="1234", enabled=True)
        await advance(hass, freezer, SYNC_CONFIRM_TIMEOUT + 1)
        assert c.sync_state == SYNC_ERROR
        assert "did not confirm" in c.last_error

    async def test_late_confirmation_via_value_update(self, hass, freezer, fake_lock, setup_entry):
        c = coord(await setup_entry())
        fake_lock.confirm_writes = False
        await c.async_update_config(code="1234", enabled=True)
        fake_lock.external_change(STATUS_ENABLED, "1234")  # the lock's own report
        await advance(hass, freezer, 2)
        assert c.sync_state == SYNC_IN_SYNC
        assert fake_lock.refreshes == 0
        assert c.config.code == "1234"

    async def test_idle_slot_never_talks_to_the_lock(self, hass, freezer, fake_lock, setup_entry):
        fake_lock.status, fake_lock.code = STATUS_ENABLED, "1234"
        await setup_entry()
        for _ in range(48):
            await advance(hass, freezer, 3600)
        assert fake_lock.writes == []
        assert fake_lock.refreshes == 0

    async def test_lock_unavailable_is_an_error_without_radio_retries(
        self, hass, freezer, fake_lock, setup_entry
    ):
        c = coord(await setup_entry())
        fake_lock.available = False
        await advance(hass, freezer, 301)
        assert c.sync_state == SYNC_ERROR
        fake_lock.available = True
        await advance(hass, freezer, 301)
        assert c.sync_state == SYNC_IN_SYNC


async def test_setup_retries_when_zwave_is_not_ready(
    hass: HomeAssistant, fake_lock: FakeLock, setup_entry
):
    from homeassistant.config_entries import ConfigEntryState

    fake_lock.available = False
    entry = await setup_entry()
    assert entry.state is ConfigEntryState.SETUP_RETRY
