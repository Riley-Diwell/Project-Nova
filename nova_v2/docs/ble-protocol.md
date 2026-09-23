# Nova Device BLE Protocol (draft)

STATUS: draft — not yet implemented on either side. Written before any firmware
or Android code changes, so both sides get built against the same spec instead
of guessing at each other.

## Why this exists

The device currently talks BLE via a third-party "serial cable emulation"
library (`BLESerial`) whose actual source/UUIDs/security behaviour couldn't be
verified (see investigation notes — the referenced repo doesn't match the API
in use). There is no Android-side code to preserve compatibility with either.
So rather than reverse-engineer an opaque dependency, this defines a GATT
profile we own on both ends.

## GATT profile

One custom service, three characteristics.

| Name | UUID | Direction | Property | Payload |
|---|---|---|---|---|
| Nova Service | `0f016870-7232-4454-8f07-c3f09eab3fcc` | — | — | — |
| `events` | `2d75cb8a-3dbe-441e-bc69-ba91ad698089` | Device → Phone | Notify | see below |
| `audio` | `98a3d8ca-c54d-4266-8706-cf15447d2058` | Device → Phone | Notify | see below |
| `commands` | `660d7ca3-765e-41d2-8c5a-c282ce9cdf90` | Phone → Device | Write (no response) | see below |

All three UUIDs are freshly generated (v4), not reused from anywhere — a
custom service needs its own, there's no existing Nova-specific UUID to draw
on.

### `events` — device → phone

1 byte type + payload. Replaces today's raw ASCII strings (`"isSingleClick"`
etc.) with a real byte format, and adds the two things currently missing:
multi-click (detected in firmware today but never sent) and a heartbeat (so
the phone can tell "still connected, nothing to report" apart from "silently
dropped").

| Type | Value | Payload |
|---|---|---|
| Single click | `0x01` | none |
| Double click | `0x02` | none |
| Multi-click | `0x03` | 1 byte: click count |
| Battery level | `0x04` | 1 byte: percentage 0–100 |
| Heartbeat | `0x05` | none |

### `audio` — device → phone

Same ADPCM block the firmware already produces (4-byte header + 256 bytes of
packed 4-bit samples = 260 bytes), wrapped in a small envelope so a dropped
BLE notification is detectable instead of silently corrupting the decode:

```
byte 0:      sequence number (wraps at 256)
byte 1:      flags — bit0 = start of utterance, bit1 = end of utterance
bytes 2-261: existing 260-byte ADPCM block, unchanged
```

262 bytes total per notification. This is the reason MTU negotiation matters
(see Connection setup) — the default un-negotiated ATT MTU (23 bytes) can't
carry this in one packet.

The sequence number lets the phone detect a dropped chunk (gap in the
sequence) instead of silently feeding a corrupted stream to the ADPCM
decoder. The start/end flags replace "button held = recording" as the
authoritative signal for where an utterance begins and ends, since that's
currently implicit and BLE notifications aren't guaranteed to arrive in
lockstep with button state.

### `commands` — phone → device

1 byte type + payload. Nothing here today; this is net-new.

| Type | Value | Payload |
|---|---|---|
| Haptic pulse | `0x01` | 1 byte: duration in 10ms units |
| LED pulse | `0x02` | 1 byte: duration in 10ms units |
| Ping | `0x03` | none — device should reply on `events` with a heartbeat |

First real use: the departure-alert work already built
(`AmbientNotifier`/`DepartureAlarmReceiver`) writes a haptic pulse here
alongside posting the phone notification it already sends.

## Connection setup

- **MTU**: negotiate up to at least 265 bytes right after connecting (23-byte
  default MTU can't carry a 262-byte `audio` notification in one packet).
- **Connection interval**: not fixed by this spec — needs tuning against real
  battery data once hardware is in hand. Start with NimBLE's default/balanced
  preset rather than guessing at a number here.
- **Bonding**: required. `commands` and `audio` must refuse to operate over an
  unencrypted (unbonded) link — this is what actually enforces "only my
  phone." JustWorks bonding (no passkey — the device has no display/keyboard
  to show or enter one).
- **Advertising**: device advertises the Nova Service UUID plus a real product
  name (not `"georgias_esp32"`), so the Android side can filter discovery to
  actual Nova devices instead of every BLE peripheral nearby.

### Connection liveness

A BLE link stays up at the radio level independent of either side's app-layer
process — the phone's Bluetooth stack doesn't tear it down just because the
app that opened it died (e.g. Android Studio redeploying the app during
development). Without anything to catch this, the firmware's `bleConnected`
flag would stay true forever after that, and since advertising only resumes
in `onDisconnect`, the device would never become connectable again short of a
physical restart.

To catch it: the phone pings (`commands` `0x03`) every 5s while connected,
purely as proof-of-life — no reply is needed since the firmware already sends
its own heartbeat on the same cadence regardless. If the firmware hasn't seen
*any* write on `commands` (a ping or otherwise) for 3 of those intervals
(15s), it disconnects the link itself and resumes advertising. Symmetrically,
if the phone hasn't seen a heartbeat in 15s despite still believing it's
connected, it tears down and reconnects on its own end too.

## What changes in firmware vs. what doesn't

**Unchanged**: I2S mic capture, the ADPCM encoder, button debounce/click
detection, LED pulse logic. All of this already works.

**Changed**: only the transport layer — `BLESerial`'s `ble.begin()` /
`ble.write()` / `ble.read()` calls get replaced with a NimBLE-Arduino GATT
server implementing the three characteristics above. The audio path gains the
2-byte envelope; the click path moves from ASCII strings to the `events` byte
format.

## What changes on Android

Net new — there's nothing to preserve. GATT client scaffolding, a persistent
connection service, `CompanionDeviceManager` pairing, and an ADPCM decoder
(none of this exists anywhere in the app yet).

## Open questions (need real hardware to resolve, not guessable from here)

- Actual battery/power budget → what connection interval is acceptable.
- Whether JustWorks bonding via NimBLE-Arduino behaves as expected on the
  XIAO ESP32-S3 specifically — needs testing, not just reading docs.
- Whether decoding ADPCM on-phone (recommended — keeps raw audio local
  longest) is fast enough on a background thread without audible lag; not
  measured yet.
