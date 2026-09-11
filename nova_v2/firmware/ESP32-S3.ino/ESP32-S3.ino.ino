// ------------- import packages -------------
//#include <driver/i2s.h>
//#include <Adafruit_I2S.h>
#include <Arduino.h>
//#include <WiFi.h>
#include <driver/i2s.h>
//#include <WiFiUdp.h>

// ------------- define pins -------------

// button
const int buttonPin = D2;
int prevButtonStatus = 0;

// microphone
//const int sck = D8;
//const int sd = D9;
//const int ws = D10;

#define I2S_SCK D8
#define I2S_WS D10
#define I2S_SD D9
#define I2S_PORT I2S_NUM_0 // Use I2S port 0

// Audio buffer configuration
#define bufferLen 1024  // Increase buffer size to accommodate more audio data

int16_t sBuffer[bufferLen]; // Buffer array to hold 16-bit audio samples

// ------------- define functions -------------
// button
void checkButton() {
  int buttonStatus = digitalRead(buttonPin);
  buttonStatus = !buttonStatus; // invert because of internal pull-up resistor
  static uint32_t pressedAt = 0;

  if (buttonStatus == HIGH && prevButtonStatus == LOW) {
    // just pressed
    pressedAt = millis();
  } else if ((buttonStatus == HIGH) &&((millis() - pressedAt) > 2000)) {
    Serial.println("Held!");
  } else if (buttonStatus == LOW && prevButtonStatus == HIGH) {
    Serial.println("Stopped being held!");
  }
  prevButtonStatus = buttonStatus;
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
  checkButton();

    size_t bytesIn = 0;
    // Read audio data from the I2S buffer
    esp_err_t result = i2s_read(I2S_PORT, &sBuffer, bufferLen * sizeof(int16_t), &bytesIn, portMAX_DELAY);

    // If data was read successfully and the buffer isn't empty
    if (result == ESP_OK && bytesIn > 0) {
        Serial.println("Result = " + result);
    }
}


// ------------- references -------------
// https://easyelecmodule.com/a-complete-guide-to-the-inmp441-i2s-microphone/ accessed 11/09/2026