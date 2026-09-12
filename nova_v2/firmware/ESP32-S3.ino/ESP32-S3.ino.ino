// ------------- import packages -------------
#include <Arduino.h>
#include <driver/i2s.h>
#include <Wire.h>
#include <string>
#include "BLESerial.h"
#include "Linereader.h"

// ------------- define pins and variables -------------

// button
const int buttonPin = D2;
//int buttonStatus = 0;
int prevButtonStatus = 0;
int prevDebouncedButtonStatus = 0;
int debouncedButtonStatus = 0;
const int debounceDelay = 50; // milliseconds
uint32_t lastDebounceTime = 0; // milliseconds


// microphone
#define I2S_SCK D8
#define I2S_WS D10
#define I2S_SD D9
#define I2S_PORT I2S_NUM_0 // Use I2S port 0

// Audio buffer configuration
#define bufferLen 1024  // Increase buffer size to accommodate more audio data
int16_t sBuffer[bufferLen]; // Buffer array to hold 16-bit audio samples

// haptics
#define HAPTIC D0
static unsigned int haptic_level = 0;

// leds
const int led1 = D3;
const int led2 = D4;

// bluetooth
//Init Library
BLESerial        ble;

//LineReader<128>  lr;
const char helpmsg[] = "Commands: ?=help, stats, echo <text>";

// ------------- define functions -------------
// button
void debounceButton() {
  int buttonStatus = digitalRead(buttonPin);
  if (buttonStatus != prevButtonStatus) { // raw input changed — restart timer
    lastDebounceTime = millis();
  }
  if ((millis() - lastDebounceTime) > debounceDelay) {
    debouncedButtonStatus = buttonStatus; // reading held steady long enough
    debouncedButtonStatus = !debouncedButtonStatus; // invert for internal pull-up: pressed = 1
  }
  prevDebouncedButtonStatus = debouncedButtonStatus;
  prevButtonStatus   = buttonStatus;
}

void checkButton() {
  static uint32_t pressedAt = 0;

  if (debouncedButtonStatus == HIGH && prevDebouncedButtonStatus == LOW) {
    // just pressed
    pressedAt = millis();
  } else if ((debouncedButtonStatus == HIGH) &&((millis() - pressedAt) > 2000)) {
    Serial.println("Held!");
  } else if (debouncedButtonStatus == LOW && prevDebouncedButtonStatus == HIGH) {
    Serial.println("Stopped being held!");
  }
}

// microphone
// Function to install and configure the I2S driver
void i2s_install() {
    const i2s_config_t i2s_config = {
        .mode = (i2s_mode_t)(I2S_MODE_MASTER | I2S_MODE_RX), // Set as master receiver
        .sample_rate = 16000,              // Audio sample rate (16kHz)
        .bits_per_sample = I2S_BITS_PER_SAMPLE_16BIT, // 16-bit per sample
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

// bluetooth
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

void bleTx(){
  static int lastSent = -1; // init case
  int dat; // Variable to store transmitted byte
  if (debouncedButtonStatus!=lastSent) {
    dat = debouncedButtonStatus;
    ble.write(dat); // Send any data received from Serial to ble device.
    ble.write('\n');                            // helps nRF Connect show it as a line
    lastSent = debouncedButtonStatus;
  }  

}

void bleRx(){
  char dat; // Variable to store received byte
  if (ble.available()) {
    dat = ble.read();
    Serial.print(dat); // Print all received data to Serial Console
  }
}
// ------------- setup -------------

void setup() {
  pinMode(buttonPin, INPUT_PULLUP);
  pinMode(HAPTIC, OUTPUT);

  pinMode(led1, OUTPUT);
  pinMode(led2, OUTPUT);

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
  // button stuff
  debounceButton();
  checkButton();

/*
  digitalWrite(led1, HIGH);   // Turn the LED on (HIGH is the voltage level)
  delay(1000);                       // Wait for a second
  digitalWrite(led1, LOW);    // Turn the LED off by making the voltage LOW
  delay(1000);     

  digitalWrite(led2, HIGH);   // Turn the LED on (HIGH is the voltage level)
  delay(1000);                       // Wait for a second
  digitalWrite(led2, LOW);    // Turn the LED off by making the voltage LOW
  delay(1000);  */

  bleRx();
  bleTx();

  // microphone stuff
  size_t bytesIn = 0;
  // Read audio data from the I2S buffer
  esp_err_t result = i2s_read(I2S_PORT, &sBuffer, bufferLen * sizeof(int16_t), &bytesIn, portMAX_DELAY);

  // If data was read successfully and the buffer isn't empty
  if (result == ESP_OK && bytesIn > 0) {
  int samplesRead = bytesIn / sizeof(int16_t);
  int16_t peak = 0;
  for (int i = 0; i < samplesRead; i++) {
    int16_t v = abs(sBuffer[i]);
    if (v > peak) peak = v;
  }
  //Serial.print("peak = "); // debug
  //Serial.println(peak);   // open Tools → Serial Plotter to see it live
}
 
  haptic_level = 255;
  //Serial.println("Haptic level: " + String(haptic_level));
  
  // create PWM signal for both haptic sensors 
  digitalWrite(HAPTIC, HIGH);
  
  // delay to prevent spamming the server
  delay(10);

}

// ------------- references -------------
// https://easyelecmodule.com/a-complete-guide-to-the-inmp441-i2s-microphone/ accessed 11/09/2026
// https://github.com/kikookraft/HapticPatPat/blob/main/firmware/src/main.cpp accessed 12/09/2026
// https://github.com/5pIO/BLESerial accessed 12/09/2026