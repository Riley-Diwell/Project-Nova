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
| Single click | `0x01` | none — older firmware only, see Press |
| Double click | `0x02` | none — older firmware only |
| Multi-click | `0x03` | 1 byte: click count — older firmware only |
| Battery level | `0x04` | 3 bytes: percentage 0–100, millivolts (2 bytes LE) |
| Heartbeat | `0x05` | none |
| Press | `0x06` | 3 bytes: press count, mode, token |

Battery is sent 3 s after connecting (time for the phone to subscribe) and
every 15 s after that. The firmware reads the cell through the 220k/220k
divider on A0 every 2 s (skipping samples while the motor runs), smooths them,
and maps the voltage onto a LiPo discharge curve. That curve is an estimate,
which is why the millivolts travel with it. Firmware older than this sent the
percentage alone, and the app still reads that. On USB with the power switch
off, A0 sees the charger's output, so the reading sits near 100%.

Press replaces `0x01`–`0x03`; the app still reads those as presses with no
mode, for firmware that predates it. Mode and token are whatever Set mode last
set, captured when the sequence's first press went down (see Button).

### `audio` — device → phone

Same ADPCM block the firmware already produces (4-byte header + 256 bytes of
packed 4-bit samples = 260 bytes), wrapped in a small envelope so a dropped
BLE notification is detectable instead of silently corrupting the decode:

```
byte 0:      sequence number (wraps at 256)
byte 1:      flags (below)
bytes 2-261: existing 260-byte ADPCM block, unchanged
```

| Bit | Value | Name | On | Meaning |
|---|---|---|---|---|
| 0 | `0x01` | START | first frame | start of a recording |
| 1 | `0x02` | END | last frame (empty block) | end of a recording |
| 2 | `0x04` | NOTE | START frame | a dictated note. Defined, but the firmware doesn't send it yet |

A START without NOTE is a plain hold (a command or question). Apps ignore
bits they don't know.

START is sent on exactly the first frame of a recording. It used to be sent
whenever the sequence number was 0, which it is again every 256 frames
(~8.2 s) — so every longer recording carried a false START and the phone
discarded everything before it. The phone also ignores a
START that arrives while a recording is already open, and closes a recording
itself (marked truncated) if no frame arrives for 1.5 s, so a lost END frame
or a dropped link can't leave it open forever.

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
| LED pulse | `0x02` | 1 byte: duration in 10ms units — flashes the RG LED green |
| Ping | `0x03` | none — device should reply on `events` with a heartbeat |
| Set LED layer | `0x04` | 10 bytes, below |
| Clear LED layer | `0x05` | 1 byte: layer id, `0xFF` clears every layer |
| Play haptic | `0x06` | 1–6 bytes: alternating on/off step durations in 10ms units, starting with on |
| Set mode | `0x07` | 4 bytes: mode, token, timeout seconds (little-endian, `0` = until changed) |

Set LED layer payload (11 bytes with the type byte):

| Byte | Field | Notes |
|---|---|---|
| 1 | id | same id replaces that layer |
| 2 | priority | highest active layer is shown; newest wins a tie |
| 3 | red | 0–255 |
| 4 | green | 0–255 |
| 5 | pattern | `0x00` solid, `0x01` blink (on for on-time at the start of each period), `0x02` breathe |
| 6–7 | period | 10ms units, little-endian |
| 8 | on-time | 10ms units (blink only) |
| 9–10 | timeout | seconds, little-endian; `0` = until cleared |

The firmware queues every write and applies it from `loop()`, so the BLE task
never touches LED/haptic state directly.

### LED layers

Ambient cues (a pending reminder, "leave soon", "leave now") are layers the
firmware plays on its own clock: once set, a pattern keeps going with no further
BLE traffic until it is cleared, times out, or the link drops. The device holds
up to 4; when full, a new layer evicts the lowest-priority one unless that
outranks it.

What the LED shows, first match wins:

1. recording: solid red
2. the release / `LED pulse` green flash
3. the top layer, including the dark half of a blink
4. the compass heading gradient, if a compass is fitted
5. off

**All layers are dropped on disconnect.** The phone (`DeviceLayers`) is the
source of truth: on every connect it clears all and re-sends the layers that
still apply, with their remaining time as the timeout.

Layers the app uses today (rhythm carries the meaning; colour is secondary,
since red/green is the pair red-green colour blindness confuses):

| Cue | id / priority | Pattern | Colour | Lasts |
|---|---|---|---|---|
| Reminder fired, unanswered | 1 / 10 | blink 150ms every 3s | amber | until answered, at most 6h after the latest one fired |
| Leave soon | 2 / 20 | blink 150ms every 1s | orange | until the leave-by time |
| Leave now | 2 / 20 | blink 120ms every 330ms | red | 5 minutes |

Leave soon and leave now share a slot; a leave-soon never replaces a live
leave-now.

## Button

One button, read by a small state machine in the firmware (`checkButton`):

| Input | What the firmware does |
|---|---|
| Hold (≥ 500 ms) | records for Nova: audio, START. LED solid red, green flash on release |
| Tap, then hold | records a note: audio, START + NOTE |
| 1–n presses, ≤ 500 ms apart | `events` Press with the count, once 500 ms pass with no further press |

Two taps then a hold is a plain hold. With no phone connected, a press or a
hold just blinks the LED red twice — nothing is recorded or sent.

**What a press means is the phone's call** (`DeviceButtonPolicy`), because it
depends on things only the phone knows:

| Situation | 1 press | 2 presses | 3 presses |
|---|---|---|---|
| Nova is thinking | busy | busy | busy |
| Nova just replied (15 s after it finishes speaking) | repeat it | done | — |
| "Leave soon/now" showing | got it | got it | read it aloud |
| A reminder went off unanswered | done | snooze | read it aloud |
| Otherwise | user's idle action | user's idle action | user's idle action |

Idle actions are presets or a free-text instruction sent to Nova as if spoken
(defaults: status buzz / "What's next?" / repeat Nova's last reply). Feedback is
haptic: one tick = done, two light taps = nothing to do, and the status action
buzzes once per reminder due in the next hour (up to three; one long buzz for
none). "Read aloud" only ever uses headphones.

**Modes and tokens.** The phone sends Set mode as it moves between idle (`0`),
thinking (`1`, LED breathes green) and replied (`2`). Each change carries a new
token (1–255). The device echoes the mode and token with every press,
captured at the press's first touch, so a press made in the last moment of the
reply window still means "repeat" even though it reaches the phone 500 ms
later. The device drops back to idle when the timeout passes or the link drops.

The phone confirms a saved note with its own haptic command (one buzz = saved,
two long = failed).

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
