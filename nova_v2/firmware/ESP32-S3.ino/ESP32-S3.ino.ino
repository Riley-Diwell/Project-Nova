// ------------- import packages -------------
#include <Arduino.h>
#include <driver/i2s.h>
#include <Wire.h>
#include <string>
#include "BLESerial.h"
#include "Linereader.h"

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
// With bufferLen=512 this is 4 + 256 = 260 bytes per BLE burst.
uint8_t adpcmOut[4 + bufferLen / 2];

// --- haptics
#define HAPTIC D1 // debug check this, should it be analog?
static unsigned int haptic_level = 0;

// --- leds
# define led1 D3
# define led2 D4

uint32_t pulseStartTime = millis();
bool pulseActive = false;

// --- battery
# define battPin A0

// --- bluetooth
BLESerial        ble; // initialise library

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
  updatePulse(led2);
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
      pulseStartTime = millis();
      startPulse(led2); // quick pulse
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
      bleTx("isSingleClick");
      break;}
    case 2: {
      Serial.println("Double click!"); 
      bleTx("isDoubleClick");
      break;}
    default:
      Serial.print("Multi-click: ");
      Serial.println(clickCount);
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
void setupBLE(){
  while (!Serial) { /* wait for USB serial */ }

  // SecurityMode::None | JustWorks | PasskeyDisplay
  // Mode::Fast | LowPower | LongRange | Balanced
  ble.begin(BLESerial::Mode::Fast, "georgias_esp32", BLESerial::Security::None);

  #ifdef ARDUINO_ARCH_ESP32
    ble.setPumpMode(BLESerial::PumpMode::Task); // background TX pump
  #endif

  Serial.println("BLESerial demo started.");
}

void bleTx(char dat[20]){
  static int lastSent = -1; // init case
    ble.write(dat); // Send any data received from Serial to ble device.
    ble.write('\n');                            // helps nRF Connect show it as a line
    lastSent = debouncedButtonStatus;
  }  

void bleRx(){
  char dat; // Variable to store received byte
  if (ble.available()) {
    dat = ble.read();
    Serial.print(dat); // Print all received data to Serial Console
  }
}

// --- feedback (haptics and led)
void startPulse(int pinNum) {
  pulseStartTime = millis();
  pulseActive = true;
  digitalWrite(pinNum, HIGH);
}

void updatePulse(int pinNum) {
  if (pulseActive && (millis() - pulseStartTime) > 500) {
    digitalWrite(pinNum, LOW);
    pulseActive = false;
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

  // --- BLE stuff
  bleRx();

  // --- microphone stuff
  static bool wasRecording = false;

  if (!isRecording) {
    if (wasRecording) wasRecording = false;
    // Non-blocking drain so the DMA ring doesn't hand us stale data next time.
    size_t bytesIn = 0;
    i2s_read(I2S_PORT, sBuffer, bufferLen * sizeof(int32_t), &bytesIn, 0);
    return;
  }

  // First loop of a new recording: reset encoder so the decoder can lock on.
  if (!wasRecording) {
    resetADPCM();
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
  ble.write(adpcmOut, outBytes);
  // Also dump the block as hex to Serial so you can copy-paste into the decoder.
  for (size_t i = 0; i < outBytes; i++) {
    Serial.printf("%02X", adpcmOut[i]);
  }
  Serial.println();
}

// ------------- references -------------
// https://easyelecmodule.com/a-complete-guide-to-the-inmp441-i2s-microphone/ accessed 11/09/2026
// https://github.com/kikookraft/HapticPatPat/blob/main/firmware/src/main.cpp accessed 12/09/2026
// https://github.com/5pIO/BLESerial accessed 12/09/2026
// https://www.cs.columbia.edu/~hgs/audio/dvi/IMA_ADPCM.pdf accessed 13/09/2026