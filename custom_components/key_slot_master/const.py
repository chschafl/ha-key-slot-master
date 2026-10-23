"""Constants for the Key Slot Master integration."""

from homeassistant.const import Platform

DOMAIN = "key_slot_master"
PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.DATETIME,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

# ── Config entry keys ─────────────────────────────────────────────────
CONF_LOCK_ENTITY_ID = "lock_entity_id"
# Entity-registry UUID of the lock. Survives the user renaming the lock's
# entity_id, so it — not the entity_id — is what the slot is bound to.
CONF_LOCK_REGISTRY_ID = "lock_registry_id"
CONF_SLOT = "slot"
CONF_NAME = "name"

# ── PIN constraints ───────────────────────────────────────────────────
# The User Code CC allows 4–10 ASCII digits. Individual locks are often
# stricter; if a lock rejects a length the sync sensor reports the error.
CODE_MIN_LENGTH = 4
CODE_MAX_LENGTH = 10
CODE_PATTERN = rf"^(\d{{{CODE_MIN_LENGTH},{CODE_MAX_LENGTH}}})?$"

# ── Sync behaviour ────────────────────────────────────────────────────
# How long to wait for the lock to confirm a write before asking it
# directly, and then before giving up and reporting an error.
SYNC_CONFIRM_TIMEOUT = 60
# Delays before each automatic retry after a failed sync (seconds). Every
# retry wakes the lock's radio, so they are capped: once exhausted, the slot
# stays in "error" until the user changes it, presses Resync, the active
# window opens/closes, or the lock reports a change on its own.
RETRY_BACKOFF = (60, 300, 900)
# Safety-net reconcile: re-reads the Z-Wave JS value cache only — it never
# talks to the lock — and re-binds to the node if Z-Wave JS was reloaded.
HEALTH_CHECK_INTERVAL = 300
# Coalesces the userCode + userIdStatus value updates that one change
# produces into a single reconcile.
LOCK_UPDATE_DEBOUNCE = 1.0

# ── Sync states ───────────────────────────────────────────────────────
SYNC_IN_SYNC = "in_sync"
SYNC_SYNCING = "syncing"
SYNC_ERROR = "error"
SYNC_STATES = [SYNC_IN_SYNC, SYNC_SYNCING, SYNC_ERROR]

# ── Storage ───────────────────────────────────────────────────────────
STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.{{entry_id}}"

# ── Events ────────────────────────────────────────────────────────────
# Fired when the slot was changed on the Z-Wave side (lock keypad, Z-Wave JS
# UI, another integration) and Key Slot Master adopted the change.
EVENT_EXTERNAL_CHANGE = f"{DOMAIN}_external_change"

# ── Services ──────────────────────────────────────────────────────────
SERVICE_SET_SLOT = "set_slot"
ATTR_CODE = "code"
ATTR_ENABLED = "enabled"
ATTR_ACTIVE_FROM = "active_from"
ATTR_ACTIVE_TO = "active_to"
