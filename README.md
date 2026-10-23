# 🔑 Key Slot Master for Home Assistant

[![HACS Custom][hacs-badge]][hacs-url]
[![License: MIT][license-badge]][license-url]

**One device per lock code: set a code, turn it on or off, and choose when it works.**

Key Slot Master turns a single user-code slot of a **Z-Wave lock** into a Home Assistant device.
Pick a lock and a slot number (say `lock.backdoor_lock`, slot 3) and you get one device
with five controls:

| Entity | Type | What it does |
|---|---|---|
| **Code** | `text` | The PIN (4 – 10 digits). Empty removes the code from the lock. |
| **Enabled** | `switch` | Off removes the PIN from the lock, but Key Slot Master remembers it. On puts it back. |
| **Active from** / **Active to** | `datetime` | Optional time window. The PIN is only on the lock while the window is open. |
| **Sync status** | `sensor` | `in_sync` · `syncing` · `error` (*could not sync*). Attributes show the last sync time and the last error. |
| **Active on lock** | `binary_sensor` | On when the lock itself reports the slot as enabled, so you know the code really opens the door. |
| **Clear active window** | `button` | Removes both window limits. Date/time fields can't be emptied from the UI, so this button does it. |
| **Resync** | `button` | Reads the slot from the lock and retries a failed sync right away. |

Add the integration once per slot. You can manage **as many slots on the same lock as you
like**, e.g. one for guests, one for the cleaner, one for family. Each slot device shows
up under its lock ("Connected via").

---

## Z-Wave is the source of truth

Key Slot Master never fights the lock. If a code is changed outside Home Assistant (on the
keypad, in Z-Wave JS UI, by another integration), the slot device **adopts the change**:

| Change made on the Z-Wave side | What the slot device does |
|---|---|
| New or different PIN set | **Code** updates, **Enabled** turns on. If the active window says the code shouldn't work right now, the window is cleared, because the lock says it does. |
| Slot cleared or disabled | **Enabled** turns off. The code is kept so you can turn the slot back on. |

When you add a slot, it starts from whatever the lock already has in that slot. Adding a
slot never erases a code. Changes made while Home Assistant was offline are picked up at
the next start.

Each adoption also fires a `key_slot_master_external_change` event (with `entry_id`,
`lock_entity_id`, `slot`, `enabled`, `code_changed`, `window_cleared`). The PIN itself is
never put on the event bus.

### How "enabled" and the time window work

Very few Z-Wave locks support disabled slots or schedules themselves. So Key Slot Master
does it in Home Assistant: while the slot is enabled **and** inside its window, the PIN is
on the lock. Otherwise the slot on the lock is cleared. The window's start and end times
trigger exactly one write each.

## Battery friendly

Door locks run on batteries, so Key Slot Master only talks to the lock when it has to:

- **No polling.** The slot's state is read from the Z-Wave JS value cache, which the lock
  updates on its own when something changes. Background checks (every 5 minutes) only read
  that cache and never contact the lock.
- **Writes only when something changes.** Saving the same value again, or turning on a
  slot that has no code, sends nothing.
- **At most one read-back per write.** If the lock doesn't confirm a change within 60 s,
  Key Slot Master asks it once. It also asks once at setup if Z-Wave JS has never read the
  slot.
- **Limited retries.** A failed write is retried after 1, 5 and 15 minutes, then it stops.
  The slot stays in `error` until you change it, press **Resync**, the time window
  starts or ends, or the lock reports a change.

## Locks that hide PINs

Some locks (several Schlage and Yale models) answer `****` instead of the real PIN when
asked. Key Slot Master handles this: it can confirm *that* a code is on the lock, and it
remembers which code it wrote. A code set directly on such a lock shows as an empty
**Code** with **Enabled** on (the slot is in use, but the PIN can't be read).

---

## Installation

**Requires Home Assistant 2026.3.0 or newer** and the
[Z-Wave integration](https://www.home-assistant.io/integrations/zwave_js/) with at least one
lock that supports the User Code command class.

### Via HACS (recommended)

1. Open HACS → **Integrations** → ⋮ menu → **Custom repositories**
2. Add `https://github.com/chschafl/ha-key-slot-master` as type **Integration**
3. Find **Key Slot Master** and click **Download**
4. Restart Home Assistant

### Manual

```bash
git clone https://github.com/chschafl/ha-key-slot-master.git /tmp/ksm
cp -r /tmp/ksm/custom_components/key_slot_master config/custom_components/
# Restart Home Assistant.
```

## Setup

**Settings → Devices & Services → Add Integration → Key Slot Master**

1. Pick the Z-Wave lock.
2. Enter the slot number. The form tells you how many slots the lock has.
3. Optionally give the slot a name (default: `<lock name> slot <n>`).

Repeat for every further slot.

## Automations

All entities work like any other: put them on a dashboard or set them from automations.
To change several properties in one step, use the `key_slot_master.set_slot` action.
Fields you leave out stay as they are, and `null` clears a window limit:

```yaml
action: key_slot_master.set_slot
data:
  device_id: 0123456789abcdef0123456789abcdef   # the slot device
  code: "4711"
  enabled: true
  active_from: "2026-11-01 15:00:00"
  active_to: "2026-11-07 11:00:00"
```

It pairs well with [STR Concierge](https://github.com/chschafl/ha-str-concierge): give each
guest their door code for the length of their booking.

Get notified when a sync fails:

```yaml
trigger:
  - trigger: state
    entity_id: sensor.backdoor_slot_3_sync_status
    to: error
    for: "00:05:00"
action:
  - action: notify.mobile_app_phone
    data:
      message: >
        Backdoor slot 3 could not sync:
        {{ state_attr('sensor.backdoor_slot_3_sync_status', 'last_error') }}
```

## Removing a slot

Deleting a slot device stops Key Slot Master from managing that slot. The **PIN stays on the
lock** exactly as it is. To remove it from the lock too, turn the slot off before
deleting the device.

---

## Development

```bash
pip install -r requirements_test.txt   # Python 3.14
make test    # pytest
make lint    # ruff
```

The brand artwork lives in `custom_components/key_slot_master/brand/` (see
[docs/branding.md](docs/branding.md)).

[hacs-badge]: https://img.shields.io/badge/HACS-Custom-orange.svg
[hacs-url]: https://github.com/hacs/integration
[license-badge]: https://img.shields.io/badge/License-MIT-yellow.svg
[license-url]: LICENSE
