// ------------- import packages -------------
#include <Arduino.h>
#include <driver/i2s.h>
#include <Wire.h>
#include <string>
#include <NimBLEDevice.h>

// ------------- BLE protocol -------------
// See nova_v2/docs/ble-protocol.md for the full spec. UUIDs are fixed - the
// Android app hardcodes these same values to find these characteristics, so
// don't regenerate them without updating both sides.
//
// Needs the "NimBLE-Arduino" library (by h2zero) installed via the Arduino
// Library Manager - not bundled with the core, and not the same as the old
// BLESerial dependency this replaces.
//
// Written against the NimBLE-Arduino 2.x callback API (onConnect/onDisconnect/
// onWrite all take a NimBLEConnInfo& parameter). If the installed version
// differs, those callback signatures may need adjusting - this has not been
// compiled or run against real hardware yet.
#define NOVA_SERVICE_UUID    "0f016870-7232-4454-8f07-c3f09eab3fcc"
#define NOVA_EVENTS_UUID     "2d75cb8a-3dbe-441e-bc69-ba91ad698089"
#define NOVA_AUDIO_UUID      "98a3d8ca-c54d-4266-8706-cf15447d2058"
#define NOVA_COMMANDS_UUID   "660d7ca3-765e-41d2-8c5a-c282ce9cdf90"

// events characteristic (device -> phone, Notify): 1 type byte + payload.
enum NovaEventType : uint8_t {
  EVENT_SINGLE_CLICK = 0x01, // no payload
  EVENT_DOUBLE_CLICK = 0x02, // no payload
  EVENT_MULTI_CLICK  = 0x03, // payload: 1 byte click count
  EVENT_BATTERY      = 0x04, // payload: 1 byte percent 0-100 - NOT SENT YET,
                              // this board revision has no confirmed battery-
                              // sense circuit to read from. Type reserved so
                              // the Android side can already handle it.
  EVENT_HEARTBEAT    = 0x05, // no payload
};

// commands characteristic (phone -> device, Write no response): 1 type byte + payload.
enum NovaCommandType : uint8_t {
  COMMAND_HAPTIC_PULSE = 0x01, // payload: 1 byte duration, x10ms
  COMMAND_LED_PULSE    = 0x02, // payload: 1 byte duration, x10ms
  COMMAND_PING         = 0x03, // no payload - device replies with EVENT_HEARTBEAT
};

// audio characteristic (device -> phone, Notify) envelope flags - see
// docs/ble-protocol.md. Byte 0 of every notification is a wrapping sequence
// number, byte 1 is these flags, bytes 2+ are the existing ADPCM block
// (empty for the dedicated end-of-utterance notification below).
const uint8_t AUDIO_FLAG_START = 0x01;
const uint8_t AUDIO_FLAG_END   = 0x02;

const uint32_t HEARTBEAT_INTERVAL_MS = 5000; // keeps the phone able to tell
                                              // "connected, quiet" from "gone"
uint32_t lastHeartbeatSent = 0;

// A BLE link stays up at the radio level even after the phone-side app's
// process dies (e.g. Android Studio redeploying it) - there is no onDisconnect
// in that case, so bleConnected would otherwise stay true forever and this
// device would never resume advertising, requiring a physical restart to pair
// again. NovaDeviceService pings every 5s (PING_INTERVAL_MS) purely as
// proof-of-life while connected; if nothing at all has been written to
// commandsChar in three of those intervals, the central is gone even though
// the radio link never told us so - force it closed ourselves.
const uint32_t CENTRAL_STALE_TIMEOUT_MS = HEARTBEAT_INTERVAL_MS * 3;
uint32_t lastCentralActivityMs = 0;

// ------------- define pins and variables -------------

// --- button
const int buttonPin = D2;
int prevButtonStatus = 0;
int prevDebouncedButtonStatus = 0;
int debouncedButtonStatus = 0;
const int debounceDelay = 10; // milliseconds
uint32_t lastDebounceTime = 0; // milliseconds
int pressedAt = 0; // when was the button pressed?

const int audioStartTime = 500; // start recording audio after 1000 milliseconds
int isRecording = 0; // 0 for not recording, 1 for recording audio

const int doubleClickDelay = 500; // max time between button press for double click
int firstClickTime = 0;
int clickCount =0;

// --- microphone
#define I2S_SCK D8
#define I2S_WS D10
#define I2S_SD D9
#define I2S_PORT I2S_NUM_0 // Use I2S port 0

// adpcm compression stuff
#define bufferLen 512  // Increase buffer size to accommodate more audio data
int32_t sBuffer[bufferLen]; // raw32-bit i2s samples
int16_t pcmBuffer[bufferLen]; // converted 16-bit PCM samples
// --- ADPCM (IMA ADPCM, 4 bits per sample, ~4:1 compression)
struct ADPCMState {
  int16_t predictor;
  int8_t  index;
};
ADPCMState adpcmState = {0, 0};

static const int16_t adpcmStepTable[89] = {
  7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
  19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
  50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
  130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
  337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
  876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
  2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
  5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
  15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767
};

static const int8_t adpcmIndexTable[16] = {
  -1, -1, -1, -1, 2, 4, 6, 8,
  -1, -1, -1, -1, 2, 4, 6, 8
};

// Fixed-size encoded output: 4-byte block header + one nibble per sample.
// With bufferLen=512 this is 4 + 256 = 260 bytes per block, sent as a 262-byte
// BLE notification once the 2-byte audio envelope (seq + flags) is added.
uint8_t adpcmOut[4 + bufferLen / 2];

// --- haptics
#define HAPTIC D1 // debug check this, should it be analog?
static unsigned int haptic_level = 0;

// --- leds
# define led1 D3
# define led2 D4

// Per-pin pulse state, so the led2 confirmation flash and a haptic pulse
// (driven by the phone over BLE) can run independently instead of sharing
// one global timer like the original single-pulse implementation did.
struct PulseState {
  uint32_t startTime = 0;
  uint32_t durationMs = 500;
  bool active = false;
};
PulseState led2Pulse;
PulseState hapticPulse;

// --- battery
# define battPin A0

// --- bluetooth (NimBLE)
NimBLEServer* bleServer = nullptr;
NimBLECharacteristic* eventsChar = nullptr;
NimBLECharacteristic* audioChar = nullptr;
NimBLECharacteristic* commandsChar = nullptr;
volatile bool bleConnected = false;
uint16_t bleConnHandle = 0; // set on connect, used by the stale-connection watchdog to disconnect
uint8_t audioSeq = 0;

// ------------- define functions -------------
// --- button
void debounceButton() {
  int buttonStatus = digitalRead(buttonPin);
  if (buttonStatus != prevButtonStatus) { // raw input changed — restart timer
    lastDebounceTime = millis();
  }

  prevDebouncedButtonStatus = debouncedButtonStatus;

  if ((millis() - lastDebounceTime) > debounceDelay) {
    debouncedButtonStatus = !buttonStatus; // reading held steady long enough, invert for internal pull up resistor, pressed = 1
  }
  prevButtonStatus   = buttonStatus;
}

void checkButton() {
  updatePulse(led2, led2Pulse);
  if (debouncedButtonStatus == HIGH && prevDebouncedButtonStatus == LOW) {
    // just pressed
    pressedAt = millis();
  }

  // button stopped being pressed
  else if (debouncedButtonStatus == LOW && prevDebouncedButtonStatus == HIGH) {
    if (isRecording == 1){
      isRecording = 0;
      Serial.println("Stop recording audio");
      digitalWrite(led1, LOW);
      startPulse(led2, led2Pulse, 500); // quick pulse
      clickCount = 0; // if we were recording audio, we don't want this to influence future single or double clicks
    } else {
      if (clickCount == 0) {
        firstClickTime = millis(); // start window on first click
        }
    clickCount++;
    }
    }

  // button is held for long enough to start recording audio
  else if (debouncedButtonStatus == HIGH) {
    if ((millis() - pressedAt) > audioStartTime) {
      if (isRecording == 0) { // if not recording already
      isRecording = 1;
      Serial.println("Begin audio recording!");
      digitalWrite(led1, HIGH);
      }
    }
  }

  // dispatch when double click window closes
  if (clickCount > 0 && (millis() - firstClickTime) > doubleClickDelay) {
  switch (clickCount) {
    case 1: {
      Serial.println("Single click!");
      sendEvent(EVENT_SINGLE_CLICK, nullptr, 0);
      break;}
    case 2: {
      Serial.println("Double click!");
      sendEvent(EVENT_DOUBLE_CLICK, nullptr, 0);
      break;}
    default: {
      Serial.print("Multi-click: ");
      Serial.println(clickCount);
      uint8_t count = (uint8_t)min(clickCount, 255);
      sendEvent(EVENT_MULTI_CLICK, &count, 1); // previously detected but never sent
    }
  }
  clickCount = 0; // reset
}
}

// --- microphone
// Function to install and configure the I2S driver
void i2s_install() {
    const i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX), // Set as master receiver
        .sample_rate = 16000,              // Audio sample rate (16kHz)
        .bits_per_sample = I2S_BITS_PER_SAMPLE_32BIT, // 32-bit per sample
        .channel_format = I2S_CHANNEL_FMT_ONLY_LEFT, // Use left channel only (mono)
        .communication_format = (i2s_comm_format_t)(I2S_COMM_FORMAT_STAND_I2S), // Standard I2S format
        .intr_alloc_flags = 0,             // No interrupt flags
        .dma_buf_count = 8,                // Number of DMA buffers
        .dma_buf_len = bufferLen,          // Size of each DMA buffer
        .use_apll = false                  // Do not use APLL clock
    };

    i2s_driver_install(I2S_PORT, &i2s_config, 0, NULL); // Install the driver
}

// Function to set the I2S pinout
void i2s_setpin() {
    const i2s_pin_config_t pin_config = {
        .bck_io_num = I2S_SCK,   // Bit clock pin
        .ws_io_num = I2S_WS,     // Word select pin
        .data_out_num = I2S_PIN_NO_CHANGE, // No data output needed (RX only)
        .data_in_num = I2S_SD    // Data input pin (from microphone)
    };

    i2s_set_pin(I2S_PORT, &pin_config); // Apply the pin configuration
}

// --- ADPCM encoder
uint8_t encodeADPCMSample(int16_t sample, ADPCMState& s) {
  int step = adpcmStepTable[s.index];
  int diff = sample - s.predictor;
  uint8_t code = 0;

  if (diff < 0) { code = 8; diff = -diff; }

  int t = step;
  if (diff >= t) { code |= 4; diff -= t; }
  t >>= 1;
  if (diff >= t) { code |= 2; diff -= t; }
  t >>= 1;
  if (diff >= t) { code |= 1; }

  int diffq = step >> 3;
  if (code & 4) diffq += step;
  if (code & 2) diffq += step >> 1;
  if (code & 1) diffq += step >> 2;

  int pred = s.predictor + ((code & 8) ? -diffq : diffq);
  if (pred >  32767) pred =  32767;
  if (pred < -32768) pred = -32768;
  s.predictor = (int16_t)pred;

  s.index += adpcmIndexTable[code];
  if (s.index < 0)  s.index = 0;
  if (s.index > 88) s.index = 88;

  return code;
}

// Encode one block. Header = predictor(2) + index(1) + reserved(1).
size_t encodeADPCMBlock(const int16_t* pcm, int samples, uint8_t* out, ADPCMState& s) {
  out[0] = s.predictor & 0xFF;
  out[1] = (s.predictor >> 8) & 0xFF;
  out[2] = (uint8_t)s.index;
  out[3] = 0;

  size_t idx = 4;
  for (int i = 0; i < samples; i += 2) {
    uint8_t lo = encodeADPCMSample(pcm[i], s);
    uint8_t hi = (i + 1 < samples) ? encodeADPCMSample(pcm[i + 1], s) : 0;
    out[idx++] = lo | (hi << 4);
  }
  return idx;
}

void resetADPCM() {
  adpcmState.predictor = 0;
  adpcmState.index = 0;
}

// --- bluetooth
class NovaServerCallbacks : public NimBLEServerCallbacks {
  void onConnect(NimBLEServer* server, NimBLEConnInfo& connInfo) override {
    bleConnected = true;
    bleConnHandle = connInfo.getConnHandle();
    lastCentralActivityMs = millis(); // starts the stale-connection countdown fresh
    Serial.println("[BLE] phone connected");
    // commands/audio are ENC-gated (see docs/ble-protocol.md "Connection
    // setup"), but commands is WRITE_NR (write without response), which gets
    // no ATT response at all - so a write to it over an unencrypted link
    // can't trigger the usual "insufficient encryption" error that would
    // otherwise make the phone's BLE stack auto-start pairing. Request
    // security proactively instead of waiting for some GATT operation to
    // demand it. Security is started via NimBLEDevice (a static/host-level
    // call), not NimBLEServer - there is no NimBLEServer::startSecurity in
    // this installed library version.
    NimBLEDevice::startSecurity(connInfo.getConnHandle());
  }
  void onDisconnect(NimBLEServer* server, NimBLEConnInfo& connInfo, int reason) override {
    bleConnected = false;
    Serial.println("[BLE] phone disconnected — resuming advertising");
    NimBLEDevice::startAdvertising();
  }
};

class NovaCommandsCallbacks : public NimBLECharacteristicCallbacks {
  void onWrite(NimBLECharacteristic* characteristic, NimBLEConnInfo& connInfo) override {
    lastCentralActivityMs = millis(); // any write at all proves the phone app is still there
    std::string value = characteristic->getValue();
    if (value.empty()) return;

    uint8_t type = (uint8_t)value[0];
    switch (type) {
      case COMMAND_HAPTIC_PULSE: {
        uint32_t durationMs = (value.size() > 1) ? (uint8_t)value[1] * 10 : 100;
        Serial.printf("[BLE] command: haptic pulse (%lums)\n", durationMs);
        startPulse(HAPTIC, hapticPulse, durationMs);
        break;
      }
      case COMMAND_LED_PULSE: {
        uint32_t durationMs = (value.size() > 1) ? (uint8_t)value[1] * 10 : 500;
        Serial.printf("[BLE] command: LED pulse (%lums)\n", durationMs);
        startPulse(led2, led2Pulse, durationMs);
        break;
      }
      case COMMAND_PING:
        Serial.println("[BLE] command: ping");
        sendHeartbeat();
        break;
      default:
        Serial.printf("[BLE] unknown command type 0x%02X\n", type);
    }
  }
};

void setupBLE() {
  while (!Serial) { /* wait for USB serial */ }

  NimBLEDevice::init("NovaDevice"); // was "georgias_esp32" - a real,
                                     // filterable product name so the phone
                                     // can tell a Nova device apart from any
                                     // other BLE peripheral nearby.

  // TEMP bring-up aid: prints the exact address to look for in a scanner
  // app, since whether this is the chip's public MAC or a random static
  // address isn't obvious from the code alone. Remove once pairing works.
  Serial.print("[BLE] address: ");
  Serial.println(NimBLEDevice::getAddress().toString().c_str());

  // Default ATT MTU is 23 bytes (20 usable) - nowhere near enough for a
  // 262-byte audio notification. 265 covers that plus the 3-byte ATT header,
  // per docs/ble-protocol.md "Connection setup". The Android side has to
  // request/accept this too - MTU is negotiated, not unilaterally set by
  // either side alone.
  NimBLEDevice::setMTU(265);

  // JustWorks bonding: bonding on, no MITM/passkey (this device has no
  // display or keyboard to show or enter one), secure connections on. This
  // is the actual mechanism that stops a second, unpaired phone connecting
  // once bonded — see docs/ble-protocol.md "Connection setup".
  NimBLEDevice::setSecurityAuth(/*bonding=*/true, /*mitm=*/false, /*sc=*/true);
  NimBLEDevice::setSecurityIOCap(BLE_HS_IO_NO_INPUT_OUTPUT);

  bleServer = NimBLEDevice::createServer();
  bleServer->setCallbacks(new NovaServerCallbacks());

  NimBLEService* service = bleServer->createService(NOVA_SERVICE_UUID);

  // READ_ENC/WRITE_ENC require an encrypted (i.e. bonded) link before the
  // characteristic will notify or accept writes at all - without this,
  // bonding would be configured but not actually enforced per-characteristic.
  eventsChar = service->createCharacteristic(
    NOVA_EVENTS_UUID,
    NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC
  );

  audioChar = service->createCharacteristic(
    NOVA_AUDIO_UUID,
    NIMBLE_PROPERTY::NOTIFY | NIMBLE_PROPERTY::READ_ENC
  );

  commandsChar = service->createCharacteristic(
    NOVA_COMMANDS_UUID,
    NIMBLE_PROPERTY::WRITE_NR | NIMBLE_PROPERTY::WRITE_ENC
  );
  commandsChar->setCallbacks(new NovaCommandsCallbacks());

  service->start();

  NimBLEAdvertising* advertising = NimBLEDevice::getAdvertising();
  advertising->addServiceUUID(NOVA_SERVICE_UUID);
  advertising->setName("NovaDevice");
  advertising->start();

  Serial.println("NovaDevice BLE service started, advertising.");
}

// Sends one events-characteristic notification: 1 type byte + up to 8 bytes
// of payload (every event type defined today needs at most 1). No-op while
// nothing is connected - there is no reconnect-and-flush queue, an event
// missed while disconnected is just missed, same stance as every other
// best-effort signal in this codebase.
//
// No default arguments here on purpose - the Arduino .ino build step
// auto-generates function prototypes by scanning this file, and a defaulted
// parameter repeated between that generated prototype and this definition
// is a known source of "redefinition of default argument" compile errors.
// Every call site passes all three explicitly instead.
void sendEvent(uint8_t type, const uint8_t* payload, size_t payloadLen) {
  if (!bleConnected || eventsChar == nullptr) return;
  uint8_t frame[1 + 8];
  frame[0] = type;
  size_t len = min(payloadLen, sizeof(frame) - 1);
  if (payload && len > 0) {
    memcpy(frame + 1, payload, len);
  }
  eventsChar->setValue(frame, 1 + len);
  eventsChar->notify();
}

void sendHeartbeat() {
  sendEvent(EVENT_HEARTBEAT, nullptr, 0);
  lastHeartbeatSent = millis();
}

// Sends one audio-characteristic notification: 1 sequence byte (wraps at
// 256, lets the phone notice a dropped notification) + 1 flags byte +
// the ADPCM block itself (empty for the dedicated end-of-utterance call in
// loop() below - there's no audio left to send at that point, only the
// marker). `frame` is static and reused across calls rather than
// re-allocated every ~32ms tick; NimBLE copies the value internally on
// setValue()/notify(), so reusing the buffer before the next call is safe.
void sendAudioChunk(uint8_t flags, const uint8_t* adpcmBlock, size_t blockLen) {
  if (!bleConnected || audioChar == nullptr) return;
  static uint8_t frame[2 + sizeof(adpcmOut)];
  frame[0] = audioSeq++;
  frame[1] = flags;
  if (adpcmBlock && blockLen > 0) {
    memcpy(frame + 2, adpcmBlock, blockLen);
  }
  audioChar->setValue(frame, 2 + blockLen);
  audioChar->notify();
}

// --- feedback (haptics and led)
void startPulse(int pinNum, PulseState& state, uint32_t durationMs) {
  state.startTime = millis();
  state.durationMs = durationMs;
  state.active = true;
  digitalWrite(pinNum, HIGH);
}

void updatePulse(int pinNum, PulseState& state) {
  if (state.active && (millis() - state.startTime) > state.durationMs) {
    digitalWrite(pinNum, LOW);
    state.active = false;
  }
}

// --- battery

void checkBatteryLevel(){
  uint32_t Vbatt = 0;
  for(int i = 0; i < 16; i++) {
    Vbatt = Vbatt + analogReadMilliVolts(A0); // ADC with correction   
  }
  float Vbattf = 2 * Vbatt / 16 / 1000.0;     // attenuation ratio 1/2, mV --> V
  Serial.println(Vbattf, 3);
}

// ------------- setup -------------

void setup() {
  pinMode(buttonPin, INPUT_PULLUP);
  pinMode(HAPTIC, OUTPUT);

  pinMode(led1, OUTPUT);
  pinMode(led2, OUTPUT);

  pinMode(battPin, INPUT);

  // initialize the digital pin with an off state
  digitalWrite(HAPTIC, LOW);

  Serial.begin(115200);
  setupBLE();
  Serial.println("Setting up I2S...");
  i2s_install();   // Configure and install the I2S driver
  i2s_setpin();    // Set the I2S pins
  i2s_start(I2S_PORT); // Start the I2S receiver
  delay(1000);           // give USB serial a moment to come up
  Serial.println("Ready. Press the button.");
}

// ------------- main loop -------------

void loop() {
  // --- button stuff
  debounceButton();
  checkButton();

  // --- BLE feedback stuff (RX is callback-driven now, not polled here)
  updatePulse(HAPTIC, hapticPulse);
  if (bleConnected && (millis() - lastHeartbeatSent) > HEARTBEAT_INTERVAL_MS) {
    sendHeartbeat();
  }
  if (bleConnected && (millis() - lastCentralActivityMs) > CENTRAL_STALE_TIMEOUT_MS) {
    // See CENTRAL_STALE_TIMEOUT_MS's comment - the radio link is still up but
    // nothing has proven the phone app is still on the other end of it.
    Serial.println("[BLE] central gone quiet - disconnecting to resume advertising");
    lastCentralActivityMs = millis(); // don't re-trigger every loop() until onDisconnect clears bleConnected
    bleServer->disconnect(bleConnHandle); // NimBLEServer::disconnect(uint16_t, uint8_t reason = ...) in
    // NimBLE-Arduino 1.4/2.x - like the rest of this file's BLE calls, not yet verified against the
    // exact installed library version on real hardware.
  }

  // --- microphone stuff
  static bool wasRecording = false;

  if (!isRecording) {
    if (wasRecording) {
      wasRecording = false;
      sendAudioChunk(AUDIO_FLAG_END, nullptr, 0); // explicit end-of-utterance marker
    }
    // Non-blocking drain so the DMA ring doesn't hand us stale data next time.
    size_t bytesIn = 0;
    i2s_read(I2S_PORT, sBuffer, bufferLen * sizeof(int32_t), &bytesIn, 0);
    return;
  }

  // First loop of a new recording: reset encoder so the decoder can lock on,
  // and restart the sequence number so the phone knows this is a new stream.
  if (!wasRecording) {
    resetADPCM();
    audioSeq = 0;
    wasRecording = true;
  }

  // Blocks up to ~32 ms while the DMA fills — fine, button poll resumes after.
  size_t bytesIn = 0;
  esp_err_t result = i2s_read(I2S_PORT, sBuffer,
                              bufferLen * sizeof(int32_t),
                              &bytesIn, portMAX_DELAY);
  if (result != ESP_OK || bytesIn == 0) return;

  int samplesRead = bytesIn / sizeof(int32_t);

  // 32-bit -> 16-bit PCM. INMP441 data sits in the upper bits;
  // >>14 gives audible signal with headroom. Increase shift to cut, decrease to boost.
  for (int i = 0; i < samplesRead; i++) {
    int32_t v = sBuffer[i] >> 14;
    if (v >  32767) v =  32767;
    if (v < -32768) v = -32768;
    pcmBuffer[i] = (int16_t)v;
  }

  size_t outBytes = encodeADPCMBlock(pcmBuffer, samplesRead, adpcmOut, adpcmState);
  uint8_t flags = (audioSeq == 0) ? AUDIO_FLAG_START : 0x00;
  sendAudioChunk(flags, adpcmOut, outBytes);
  // Also dump the block as hex to Serial so you can copy-paste into the decoder.
  for (size_t i = 0; i < outBytes; i++) {
    Serial.printf("%02X", adpcmOut[i]);
  }
  Serial.println();
}

// ------------- references -------------
// https://easyelecmodule.com/a-complete-guide-to-the-inmp441-i2s-microphone/ accessed 11/09/2026
// https://github.com/kikookraft/HapticPatPat/blob/main/firmware/src/main.cpp accessed 12/09/2026
// https://github.com/h2zero/NimBLE-Arduino - replaces the earlier BLESerial
// dependency (see nova_v2/docs/ble-protocol.md for why)
// https://www.cs.columbia.edu/~hgs/audio/dvi/IMA_ADPCM.pdf accessed 13/09/2026
