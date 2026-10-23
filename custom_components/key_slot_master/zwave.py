"""Thin adapter around one user-code slot of a Z-Wave JS lock.

Everything that touches Z-Wave lives here, so the slot logic in
``coordinator.py`` can be exercised against a fake in tests.

Reads come straight from the Z-Wave JS value cache (no radio traffic). Writes
go through the public ``zwave_js.set_lock_usercode`` / ``clear_lock_usercode``
services so they get the same validation and error translation as a user
calling them by hand.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er

_LOGGER = logging.getLogger(__name__)

ZWAVE_DOMAIN = "zwave_js"
USER_CODE_CC = 99
USERCODE_PROPERTY = "userCode"
USERCODE_STATUS_PROPERTY = "userIdStatus"

# User ID status values from the User Code CC.
STATUS_AVAILABLE = 0
STATUS_ENABLED = 1
STATUS_DISABLED = 2


class LockUnavailableError(Exception):
    """The lock (or its Z-Wave node / code slot) cannot be reached right now."""


@dataclass(frozen=True)
class LockSlotState:
    """What the lock currently reports for one code slot."""

    status: int | None
    code: str | None

    @property
    def known(self) -> bool:
        """False until Z-Wave JS has read the slot from the lock at least once."""
        return self.status is not None

    @property
    def active(self) -> bool:
        """True if the slot currently grants access on the lock."""
        return self.status == STATUS_ENABLED

    @property
    def masked(self) -> bool:
        """True if the lock hides the PIN when it is read back.

        Several locks (Schlage, some Yale models) report ``****`` or nothing
        at all for an occupied slot. We can then confirm *that* a code is
        set, but not *which* one.
        """
        return not self.code or set(self.code) == {"*"}

    def as_dict(self) -> dict[str, Any]:
        return {"status": self.status, "code": self.code}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> LockSlotState | None:
        if not data:
            return None
        return cls(status=data.get("status"), code=data.get("code"))


class ZWaveLockBackend:
    """Read, write and watch one user-code slot on a Z-Wave JS lock."""

    def __init__(self, hass: HomeAssistant, lock_ref: str, slot: int) -> None:
        # lock_ref is the entity-registry UUID (preferred) or an entity_id.
        self._hass = hass
        self._lock_ref = lock_ref
        self.slot = slot
        self._node: Any = None
        self._unsub_node: Callable[[], None] | None = None
        self._on_update: Callable[[], None] | None = None

    # ── Lookup ────────────────────────────────────────────────────────

    @property
    def entity_id(self) -> str | None:
        """The lock's current entity_id (follows renames)."""
        return er.async_resolve_entity_id(er.async_get(self._hass), self._lock_ref)

    @property
    def device_id(self) -> str | None:
        """Device-registry id of the lock, used to nest the slot device under it."""
        entity_id = self.entity_id
        if entity_id is None:
            return None
        entry = er.async_get(self._hass).async_get(entity_id)
        return entry.device_id if entry else None

    def _resolve_node(self) -> Any:
        # Imported lazily: zwave_js is a manifest dependency, but importing it
        # at module load would drag its whole dependency tree into config-flow
        # imports and tests that never touch a real node.
        from homeassistant.components.zwave_js.helpers import async_get_node_from_entity_id

        entity_id = self.entity_id
        if entity_id is None:
            raise LockUnavailableError(f"Lock entity {self._lock_ref} no longer exists")
        try:
            return async_get_node_from_entity_id(self._hass, entity_id)
        except ValueError as err:
            raise LockUnavailableError(str(err)) from err

    @callback
    def async_ensure_node(self) -> None:
        """Bind to the lock's node, re-binding if Z-Wave JS was reloaded.

        A reload of the Z-Wave integration creates fresh node objects, and
        listeners registered on the old one silently stop firing.
        """
        node = self._resolve_node()
        if node is self._node:
            return
        if self._unsub_node is not None:
            self._unsub_node()
        self._node = node
        self._unsub_node = node.on("value updated", self._handle_value_updated)
        _LOGGER.debug("Bound to Z-Wave node %s for slot %s", node.node_id, self.slot)

    def max_slot(self) -> int:
        """Highest user-code slot the lock exposes (0 if none)."""
        from zwave_js_server.util.lock import get_code_slots

        self.async_ensure_node()
        slots = get_code_slots(self._node)
        return max((s["code_slot"] for s in slots), default=0)

    # ── Read ──────────────────────────────────────────────────────────

    def read(self) -> LockSlotState:
        """Return the slot as cached by Z-Wave JS."""
        self.async_ensure_node()
        status = self._value(USERCODE_STATUS_PROPERTY)
        code = self._value(USERCODE_PROPERTY)
        return LockSlotState(
            status=None if status.value is None else int(status.value),
            code=None if code.value in (None, "") else str(code.value),
        )

    async def async_refresh(self) -> LockSlotState:
        """Ask the lock itself for the slot, then return the refreshed cache."""
        self.async_ensure_node()
        await self._node.async_invoke_cc_api(USER_CODE_CC, "get", self.slot, wait_for_result=True)
        return self.read()

    def _value(self, property_name: str) -> Any:
        value_id = f"{self._node.node_id}-{USER_CODE_CC}-0-{property_name}-{self.slot}"
        value = self._node.values.get(value_id)
        if value is None:
            raise LockUnavailableError(
                f"Slot {self.slot} does not exist on {self.entity_id} ({property_name} missing)"
            )
        return value

    # ── Write ─────────────────────────────────────────────────────────

    async def async_set_code(self, code: str) -> None:
        await self._call("set_lock_usercode", {"code_slot": self.slot, "usercode": code})

    async def async_clear(self) -> None:
        await self._call("clear_lock_usercode", {"code_slot": self.slot})

    async def _call(self, service: str, data: dict[str, Any]) -> None:
        entity_id = self.entity_id
        if entity_id is None:
            raise LockUnavailableError(f"Lock entity {self._lock_ref} no longer exists")
        await self._hass.services.async_call(
            ZWAVE_DOMAIN, service, {"entity_id": entity_id, **data}, blocking=True
        )

    # ── Watch ─────────────────────────────────────────────────────────

    @callback
    def async_subscribe(self, on_update: Callable[[], None]) -> Callable[[], None]:
        """Call ``on_update`` whenever the lock reports a change to this slot."""
        self._on_update = on_update

        @callback
        def _unsubscribe() -> None:
            self._on_update = None
            if self._unsub_node is not None:
                self._unsub_node()
                self._unsub_node = None
            self._node = None

        return _unsubscribe

    @callback
    def _handle_value_updated(self, event: dict[str, Any]) -> None:
        value = event.get("value")
        if value is None or self._on_update is None:
            return
        if (
            value.command_class == USER_CODE_CC
            and value.property_ in (USERCODE_PROPERTY, USERCODE_STATUS_PROPERTY)
            and value.property_key == self.slot
        ):
            self._on_update()
