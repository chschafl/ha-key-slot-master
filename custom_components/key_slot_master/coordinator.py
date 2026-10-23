"""Keeps one Key Slot in sync with its Z-Wave lock.

The model is a three-way merge between:

* the **desired** slot (``SlotConfig``: code, enabled, active window) that the
  user edits through the slot's entities,
* what the **lock** reports right now, and
* the **last lock state we saw** (persisted, so it survives restarts).

Rules, applied on every lock update, config change, window boundary and
periodic health check:

1. Lock matches desired → in sync.
2. A write is in flight → wait for the lock to confirm (or time out).
3. The lock changed since we last looked, and not because of us → someone
   changed it on the Z-Wave side. **Z-Wave is the source of truth**, so the
   change is adopted into the desired state.
4. Otherwise the lock is simply behind (new desired state, or the active
   window opened/closed) → push the desired state to the lock.

Battery: the lock is only ever contacted to write a change, or to read the
slot back once when a write went unconfirmed (and on setup / Resync if the
cache has nothing). Everything else reads the Z-Wave JS value cache, and
automatic retries are capped (see ``RETRY_BACKOFF``).

"Enabled" and the active window are enforced by putting the PIN on the lock
or clearing it, since very few Z-Wave locks support disabled slots or
schedules natively.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import (
    async_call_later,
    async_track_point_in_utc_time,
    async_track_time_interval,
)
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CODE_PATTERN,
    DOMAIN,
    EVENT_EXTERNAL_CHANGE,
    HEALTH_CHECK_INTERVAL,
    LOCK_UPDATE_DEBOUNCE,
    RETRY_BACKOFF,
    STORAGE_KEY,
    STORAGE_VERSION,
    SYNC_CONFIRM_TIMEOUT,
    SYNC_ERROR,
    SYNC_IN_SYNC,
    SYNC_SYNCING,
)
from .zwave import LockSlotState, LockUnavailableError, ZWaveLockBackend

_LOGGER = logging.getLogger(__name__)

_UNSET: Any = object()


@dataclass(frozen=True)
class SlotConfig:
    """The desired state of a key slot."""

    code: str | None = None
    enabled: bool = False
    active_from: datetime | None = None
    active_to: datetime | None = None

    def in_window(self, now: datetime) -> bool:
        if self.active_from is not None and now < self.active_from:
            return False
        if self.active_to is not None and now >= self.active_to:
            return False
        return True

    def wants_active(self, now: datetime) -> bool:
        """True if the PIN should currently be programmed on the lock."""
        return self.enabled and bool(self.code) and self.in_window(now)

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "enabled": self.enabled,
            "active_from": self.active_from.isoformat() if self.active_from else None,
            "active_to": self.active_to.isoformat() if self.active_to else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SlotConfig:
        return cls(
            code=data.get("code"),
            enabled=bool(data.get("enabled")),
            active_from=_parse_dt(data.get("active_from")),
            active_to=_parse_dt(data.get("active_to")),
        )


def _parse_dt(value: str | None) -> datetime | None:
    return dt_util.parse_datetime(value) if value else None


class KeySlotCoordinator:
    """Owns the desired state of one slot and drives the lock towards it."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        backend: ZWaveLockBackend,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.backend = backend
        self.config = SlotConfig()
        self.lock_state: LockSlotState | None = None
        self.sync_state = SYNC_SYNCING
        self.last_error: str | None = None
        self.last_synced: datetime | None = None

        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, STORAGE_KEY.format(entry_id=entry_id)
        )
        self._lock = asyncio.Lock()
        self._listeners: list[CALLBACK_TYPE] = []
        self._pending = False
        # What the in-flight write puts on the lock: the PIN, or None to clear.
        self._expected_code: str | None = None
        # The PIN we know is on the lock. Only needed for locks that mask
        # codes on read-back; for the rest the lock tells us directly.
        self._synced_code: str | None = None
        self._retry_count = 0
        self._gave_up = False
        self._unsub_confirm: CALLBACK_TYPE | None = None
        self._unsub_retry: CALLBACK_TYPE | None = None
        self._unsub_window: CALLBACK_TYPE | None = None
        self._unsubs: list[CALLBACK_TYPE] = []
        self._debouncer = Debouncer(
            hass,
            _LOGGER,
            cooldown=LOCK_UPDATE_DEBOUNCE,
            immediate=False,
            function=self.async_reconcile,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────

    async def async_setup(self) -> None:
        """Load state, bind to the lock and run the first reconcile.

        Raises LockUnavailableError if Z-Wave JS can't give us the node.
        """
        self.backend.async_ensure_node()
        self._unsubs.append(self.backend.async_subscribe(self._handle_lock_update))

        stored = await self._store.async_load()
        if stored:
            self.config = SlotConfig.from_dict(stored.get("config", {}))
            self.lock_state = LockSlotState.from_dict(stored.get("lock_state"))
            self.last_synced = _parse_dt(stored.get("last_synced"))
            self._synced_code = stored.get("synced_code")

        try:
            if not self.backend.read().known:
                # Z-Wave JS hasn't read this slot yet (common right after
                # inclusion, or for slots it skips during the interview).
                await self.backend.async_refresh()
        except Exception as err:  # noqa: BLE001 - reconcile reports it
            _LOGGER.debug("Initial refresh of slot %s failed: %s", self.backend.slot, err)

        self._unsubs.append(
            async_track_time_interval(
                self.hass, self._async_health_check, timedelta(seconds=HEALTH_CHECK_INTERVAL)
            )
        )
        await self.async_reconcile()
        self._schedule_window_timer()

    async def async_shutdown(self) -> None:
        self._debouncer.async_shutdown()
        for unsub in (self._unsub_confirm, self._unsub_retry, self._unsub_window, *self._unsubs):
            if unsub is not None:
                unsub()
        self._unsub_confirm = self._unsub_retry = self._unsub_window = None
        self._unsubs.clear()
        await self._store.async_save(self._data_to_save())

    # ── Listeners (entities) ──────────────────────────────────────────

    @callback
    def async_add_listener(self, update_callback: CALLBACK_TYPE) -> Callable[[], None]:
        self._listeners.append(update_callback)

        @callback
        def _remove() -> None:
            self._listeners.remove(update_callback)

        return _remove

    @callback
    def _notify(self) -> None:
        for update_callback in list(self._listeners):
            update_callback()

    # ── Public API used by entities and services ──────────────────────

    @property
    def access_active(self) -> bool:
        """True if the lock currently reports the slot as granting access."""
        return bool(self.lock_state and self.lock_state.active)

    async def async_update_config(
        self,
        *,
        code: str | None = _UNSET,
        enabled: bool = _UNSET,
        active_from: datetime | None = _UNSET,
        active_to: datetime | None = _UNSET,
    ) -> None:
        """Change the desired state and push it to the lock."""
        changes: dict[str, Any] = {}
        if code is not _UNSET:
            code = code or None
            if code is not None and not re.fullmatch(CODE_PATTERN, code):
                raise ServiceValidationError(
                    translation_domain=DOMAIN, translation_key="invalid_code"
                )
            changes["code"] = code
        if enabled is not _UNSET:
            changes["enabled"] = enabled
        if active_from is not _UNSET:
            changes["active_from"] = dt_util.as_utc(active_from) if active_from else None
        if active_to is not _UNSET:
            changes["active_to"] = dt_util.as_utc(active_to) if active_to else None

        new = replace(self.config, **changes)
        if new.active_from and new.active_to and new.active_to <= new.active_from:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="invalid_window"
            )
        if new == self.config:
            return
        self.config = new
        self._reset_retries()
        self._save()
        self._schedule_window_timer()
        self._notify()
        await self.async_reconcile()

    async def async_resync(self) -> None:
        """Re-read the slot from the lock itself and reconcile, retrying now."""
        self._reset_retries()
        try:
            await self.backend.async_refresh()
        except Exception as err:  # noqa: BLE001 - reconcile reports it
            _LOGGER.debug("Refresh of slot %s failed: %s", self.backend.slot, err)
        await self.async_reconcile()

    # ── Reconcile ─────────────────────────────────────────────────────

    def _target_code(self, now: datetime) -> str | None:
        """The PIN that should be on the lock right now, or None for cleared."""
        return self.config.code if self.config.wants_active(now) else None

    def _lock_code(self, state: LockSlotState) -> str | None:
        """The PIN on the lock, as far as we can tell."""
        if not state.active:
            return None
        if not state.masked:
            return state.code
        # Masked read-back: trust what we wrote, once the lock shows active.
        return self._expected_code if self._pending else self._synced_code

    def _matches(self, state: LockSlotState, now: datetime) -> bool:
        config = self.config
        if config.wants_active(now):
            return state.active and self._lock_code(state) == config.code
        if (
            config.enabled
            and config.code is None
            and config.in_window(now)
            and state.active
            and state.masked
        ):
            # The lock holds a PIN it won't read back to us (adopted from
            # the Z-Wave side). Leave it alone until the user sets one.
            return True
        return not state.active

    async def async_reconcile(self) -> None:
        async with self._lock:
            await self._reconcile_locked()

    async def _reconcile_locked(self) -> None:
        now = dt_util.utcnow()
        try:
            state = self.backend.read()
        except LockUnavailableError as err:
            self._set_error(str(err))
            return
        if not state.known:
            self._set_error("The lock has not reported this slot yet")
            return

        previous = self.lock_state
        if self._matches(state, now):
            self._mark_in_sync(state)
            return

        if self._pending:
            if self._target_code(now) != self._expected_code:
                # The user changed the slot again before the lock confirmed
                # the previous write; send the newer state right away.
                await self._push(now)
                return
            # Our write is in flight; the lock may report intermediate values
            # (status before code). Track them so a timeout doesn't mistake
            # them for an external change.
            self.lock_state = state
            self._save()
            self._notify()
            return

        if previous is None or state != previous:
            self._adopt(state, now)
            return

        if self._unsub_retry is not None:
            # A back-off retry is already scheduled; don't jump the queue.
            return
        if self._gave_up:
            # Out of automatic retries — don't keep waking the lock.
            if self.sync_state != SYNC_ERROR:
                self._set_error(self.last_error or "Could not sync with the lock")
            return
        await self._push(now)

    async def _push(self, now: datetime) -> None:
        wants_active = self.config.wants_active(now)
        self._expected_code = self._target_code(now)
        self._pending = True
        self.sync_state = SYNC_SYNCING
        self._notify()
        _LOGGER.debug(
            "Slot %s on %s: %s",
            self.backend.slot,
            self.backend.entity_id,
            "setting code" if wants_active else "clearing code",
        )
        try:
            if wants_active:
                assert self.config.code is not None
                await self.backend.async_set_code(self.config.code)
            else:
                await self.backend.async_clear()
        except Exception as err:  # noqa: BLE001 - surfaced on the sync sensor
            self._pending = False
            self._set_error(f"Could not write to the lock: {err}", retry=True)
            return

        self._cancel_confirm()
        self._unsub_confirm = async_call_later(
            self.hass, SYNC_CONFIRM_TIMEOUT, self._async_confirm_timeout
        )
        # A supervised set updates the value cache before the service returns.
        try:
            state = self.backend.read()
        except LockUnavailableError:
            return
        if self._matches(state, now):
            self._mark_in_sync(state)

    async def _async_confirm_timeout(self, _now: datetime) -> None:
        self._unsub_confirm = None
        if not self._pending:
            return
        try:
            # Ask the lock directly — maybe it applied the change but never
            # sent an unsolicited report.
            await self.backend.async_refresh()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Refresh after timeout failed: %s", err)
        async with self._lock:
            if not self._pending:
                return
            now = dt_util.utcnow()
            try:
                state = self.backend.read()
            except LockUnavailableError as err:
                self._pending = False
                self._set_error(str(err))
                return
            if self._matches(state, now):
                self._mark_in_sync(state)
                return
            self._pending = False
            self.lock_state = state
            self._set_error("The lock did not confirm the change", retry=True)

    # ── State transitions ─────────────────────────────────────────────

    @callback
    def _mark_in_sync(self, state: LockSlotState) -> None:
        was_in_sync = self.sync_state == SYNC_IN_SYNC
        changed = state != self.lock_state
        self._synced_code = self._lock_code(state)
        self._pending = False
        self._reset_retries()
        self._cancel_confirm()
        self.lock_state = state
        self.sync_state = SYNC_IN_SYNC
        self.last_error = None
        if not was_in_sync or changed or self.last_synced is None:
            self.last_synced = dt_util.utcnow()
        self._save()
        self._notify()

    @callback
    def _adopt(self, state: LockSlotState, now: datetime) -> None:
        """Take over a change that was made on the Z-Wave side."""
        old = self.config
        new = old
        if state.active:
            # A masked lock won't tell us the new PIN; show none rather than
            # a stale one.
            new = replace(new, enabled=True, code=None if state.masked else state.code)
            if not new.in_window(now):
                # The lock grants access right now; a window that says it
                # shouldn't would contradict the source of truth.
                new = replace(new, active_from=None, active_to=None)
        else:
            new = replace(new, enabled=False)

        first_sync = self.lock_state is None
        self.config = new
        self._synced_code = None
        _LOGGER.info(
            "Slot %s on %s was changed on the Z-Wave side; adopting (enabled=%s)",
            self.backend.slot,
            self.backend.entity_id,
            new.enabled,
        )
        self._schedule_window_timer()
        self._mark_in_sync(state)
        if not first_sync and new != old:
            self.hass.bus.async_fire(
                EVENT_EXTERNAL_CHANGE,
                {
                    "entry_id": self.entry_id,
                    "lock_entity_id": self.backend.entity_id,
                    "slot": self.backend.slot,
                    "enabled": new.enabled,
                    "code_changed": new.code != old.code,
                    "window_cleared": (old.active_from, old.active_to) != (None, None)
                    and (new.active_from, new.active_to) == (None, None),
                },
            )

    @callback
    def _set_error(self, message: str, *, retry: bool = False) -> None:
        """Report an error. ``retry`` schedules a write retry with back-off.

        Read-side errors (Z-Wave JS not loaded, slot not reported yet) don't
        retry here: they cost no radio traffic and the health check and lock
        updates re-evaluate them anyway.
        """
        if self.last_error != message:
            _LOGGER.warning(
                "Key slot %s on %s: %s", self.backend.slot, self.backend.entity_id, message
            )
        self.sync_state = SYNC_ERROR
        self.last_error = message
        if retry:
            self._schedule_retry()
        self._notify()

    # ── Timers ────────────────────────────────────────────────────────

    @callback
    def _handle_lock_update(self) -> None:
        self._debouncer.async_schedule_call()

    async def _async_health_check(self, _now: datetime) -> None:
        await self.async_reconcile()

    @callback
    def _schedule_retry(self) -> None:
        if self._unsub_retry is not None or self._gave_up:
            return
        if self._retry_count >= len(RETRY_BACKOFF):
            self._gave_up = True
            return
        delay = RETRY_BACKOFF[self._retry_count]
        self._retry_count += 1

        async def _retry(_now: datetime) -> None:
            self._unsub_retry = None
            await self.async_reconcile()

        self._unsub_retry = async_call_later(self.hass, delay, _retry)

    @callback
    def _schedule_window_timer(self) -> None:
        if self._unsub_window is not None:
            self._unsub_window()
            self._unsub_window = None
        now = dt_util.utcnow()
        upcoming = [t for t in (self.config.active_from, self.config.active_to) if t and t > now]
        if not upcoming:
            return

        async def _boundary(_now: datetime) -> None:
            self._unsub_window = None
            # The target changed, so it deserves a fresh set of retries.
            self._reset_retries()
            self._schedule_window_timer()
            await self.async_reconcile()

        self._unsub_window = async_track_point_in_utc_time(self.hass, _boundary, min(upcoming))

    @callback
    def _cancel_confirm(self) -> None:
        if self._unsub_confirm is not None:
            self._unsub_confirm()
            self._unsub_confirm = None

    @callback
    def _reset_retries(self) -> None:
        self._retry_count = 0
        self._gave_up = False
        self._cancel_retry()

    @callback
    def _cancel_retry(self) -> None:
        if self._unsub_retry is not None:
            self._unsub_retry()
            self._unsub_retry = None

    # ── Persistence ───────────────────────────────────────────────────

    @callback
    def _save(self) -> None:
        self._store.async_delay_save(self._data_to_save, 1)

    @callback
    def _data_to_save(self) -> dict[str, Any]:
        return {
            "config": self.config.as_dict(),
            "lock_state": self.lock_state.as_dict() if self.lock_state else None,
            "last_synced": self.last_synced.isoformat() if self.last_synced else None,
            "synced_code": self._synced_code,
        }
