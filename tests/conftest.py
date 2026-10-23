"""Shared fixtures for Key Slot Master tests."""

from __future__ import annotations

pytest_plugins = "pytest_homeassistant_custom_component"

from collections.abc import Callable
from datetime import timedelta
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.key_slot_master.const import (
    CONF_LOCK_ENTITY_ID,
    CONF_LOCK_REGISTRY_ID,
    CONF_SLOT,
    DOMAIN,
)
from custom_components.key_slot_master.zwave import (
    STATUS_AVAILABLE,
    STATUS_ENABLED,
    LockSlotState,
    LockUnavailableError,
)

LOCK_ENTITY_ID = "lock.backdoor_lock"
SLOT = 3


class FakeLock:
    """Stands in for ZWaveLockBackend and simulates the lock behind it.

    Counts every operation that would wake the lock's radio, so tests can
    pin down the battery behaviour.
    """

    def __init__(self, status: int | None = STATUS_AVAILABLE, code: str | None = None) -> None:
        self.status = status
        self.code = code
        self.slot = SLOT
        self.entity_id = LOCK_ENTITY_ID
        self.device_id: str | None = None
        self.available = True
        # Behaviour knobs
        self.confirm_writes = True  # cache reflects a write as soon as it returns
        self.report_on_refresh = True  # a direct CC get reveals the real state
        self.fail_writes = False
        self.masked = False
        # Simulated "real" lock content when writes aren't confirmed.
        self._actual: tuple[int | None, str | None] | None = None
        # Radio counters
        self.writes: list[tuple[str, str | None]] = []
        self.refreshes = 0
        self._on_update: Callable[[], None] | None = None

    # ── backend interface ──
    def async_ensure_node(self) -> None:
        if not self.available:
            raise LockUnavailableError("node gone")

    def max_slot(self) -> int:
        self.async_ensure_node()
        return 30

    def read(self) -> LockSlotState:
        self.async_ensure_node()
        code = self.code
        if self.masked and code:
            code = "*" * len(code)
        return LockSlotState(status=self.status, code=code)

    async def async_refresh(self) -> LockSlotState:
        self.async_ensure_node()
        self.refreshes += 1
        if self._actual is not None and self.report_on_refresh:
            self.status, self.code = self._actual
            self._actual = None
        return self.read()

    async def async_set_code(self, code: str) -> None:
        self.writes.append(("set", code))
        self._write(STATUS_ENABLED, code)

    async def async_clear(self) -> None:
        self.writes.append(("clear", None))
        self._write(STATUS_AVAILABLE, None)

    def _write(self, status: int, code: str | None) -> None:
        if self.fail_writes:
            raise HomeAssistantError("Z-Wave error: no ack")
        if self.confirm_writes:
            self.status, self.code = status, code
        else:
            self._actual = (status, code)

    def async_subscribe(self, on_update: Callable[[], None]) -> Callable[[], None]:
        self._on_update = on_update

        def _unsub() -> None:
            self._on_update = None

        return _unsub

    # ── test helpers ──
    def external_change(self, status: int, code: str | None) -> None:
        """Somebody changed the slot on the Z-Wave side (keypad, Z-Wave JS UI)."""
        self.status, self.code = status, code
        if self._on_update:
            self._on_update()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def fake_lock() -> FakeLock:
    return FakeLock()


@pytest.fixture
def mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="Backdoor slot 3",
        unique_id=f"registry-id_{SLOT}",
        data={
            CONF_LOCK_ENTITY_ID: LOCK_ENTITY_ID,
            CONF_LOCK_REGISTRY_ID: LOCK_ENTITY_ID,
            CONF_SLOT: SLOT,
        },
    )


@pytest.fixture
def setup_entry(hass: HomeAssistant, fake_lock: FakeLock, mock_entry: MockConfigEntry):
    """Return a coroutine that sets up the entry against the fake lock."""

    async def _setup(entry: MockConfigEntry | None = None) -> MockConfigEntry:
        entry = entry or mock_entry
        # The real zwave_js integration isn't needed: the fake replaces it.
        hass.config.components.add("zwave_js")
        entry.add_to_hass(hass)
        with patch("custom_components.key_slot_master.ZWaveLockBackend", return_value=fake_lock):
            await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
        return entry

    return _setup


async def advance(hass: HomeAssistant, freezer, seconds: float) -> None:
    """Move the (frozen) clock forward and let due timers fire."""
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
