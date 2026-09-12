// ------------- import packages -------------
//#include <driver/i2s.h>
//#include <Adafruit_I2S.h>
#include <Arduino.h>
//#include <WiFi.h>
#include <driver/i2s.h>
//#include <WiFiUdp.h>
#include <Wire.h>
#include <BluetoothSerial.h>
#include <string>

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

  // debug
  //Serial.print("Button Status = ");
  //Serial.println(buttonStatus);
  //Serial.print("Debounced Button Status = ");
  //Serial.println(debouncedButtonStatus);
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

// ------------- setup -------------

void setup() {
  pinMode(buttonPin, INPUT_PULLUP);
  pinMode(HAPTIC, OUTPUT);

  // initialize the digital pin with an off state
  digitalWrite(HAPTIC, LOW);

  Serial.begin(115200);
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
  Serial.print("peak = ");
  Serial.println(peak);   // open Tools → Serial Plotter to see it live
}
 
  haptic_level = 255;
  Serial.println("Haptic level: " + String(haptic_level));
  
  // create PWM signal for both haptic sensors 
  digitalWrite(HAPTIC, HIGH);
  
  // delay to prevent spamming the server
  delay(10);

}

// ------------- references -------------
// https://easyelecmodule.com/a-complete-guide-to-the-inmp441-i2s-microphone/ accessed 11/09/2026
// https://github.com/kikookraft/HapticPatPat/blob/main/firmware/src/main.cpp accessed 12/09/2026