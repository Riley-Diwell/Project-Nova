"""
Reads button presses and intent surface touches from the Seeed XIAO over
serial/USB and feeds them into  intent_surface.py physiological
context fields (input_type, heart_rate, gaze, posture) which currently
return N/A when no hardware is connected.

Also sends LED ring commands back to the XIAO so the notification
batching layer (notification_batcher.py) can drive the LED ring.

Usage:
  python hardware_bridge.py --port /dev/tty.usbmodem1101 --baud 115200

"""

import argparse
import json
import queue
import threading
import time
import random
from dataclasses import dataclass, field
from typing import Optional


# Types

@dataclass
class PhysiologicalReading:
    """Drop-in replacement for the N/A values in Georgia's Physiological_context."""
    input_type:  str = "N/A" # "buttons"  "touch"  "silent speech"  "N/A"
    input_tone:  str = "neutral" # unchanged
    heart_rate:  str = "N/A"  # int as str or "N/A"
    gaze:        str = "N/A" # for future eye tracking
    posture:     str = "N/A" # for future IMU

@dataclass
class HardwareEvent:
    event:      str  # "button"  "touch"  "hold"  "swipe"  "hr"
    raw:        dict = field(default_factory=dict)
    timestamp:  float = field(default_factory=time.time)


#Bridge

class HardwareBridge:

    def __init__(self, port: Optional[str] = None, baud: int = 115200, mock: bool = False):
        self.port   = port
        self.baud   = baud
        self.mock   = mock
        self._ser   = None
        self._queue: queue.Queue[HardwareEvent] = queue.Queue()
        self._running = False

        # Current physiological reading — updated on every hardware event
        self._reading = PhysiologicalReading()
        self._lock = threading.Lock()

    #Public API 

    def get_physiological_context(self) -> PhysiologicalReading:
        """
        Call this from intent_surface.py instead of returning N/A.

        Example integration:
            from hardware_bridge import HardwareBridge
            bridge = HardwareBridge(port="/dev/tty.usbmodem1101")
            bridge.start()

            # In gather_context(), replace the hardcoded N/A strings:
            phys = bridge.get_physiological_context()
            # Then pass phys.input_type, phys.heart_rate etc into the
            # Physiological_context Pydantic model fields.
        """
        with self._lock:
            import copy
            return copy.copy(self._reading)

    def send_led_command(self, mode: str, notifications: int, urgency: str):
        """
        Send a command to the XIAO to update the LED ring 
        Called by notification_batcher.py when state changes.

        mode:          "focus"  "lecture"  "default"  "sleep"
        notifications: how many are batched and waiting
        urgency:       "none"  "low"  "high"
        """
        cmd = json.dumps({
            "mode": mode,
            "notifications": notifications,
            "urgency": urgency
        }) + "\n"

        if self.mock:
            print(f"[BRIDGE → LED] {cmd.strip()}")
            return

        if self._ser and self._ser.is_open:
            try:
                self._ser.write(cmd.encode())
            except Exception as e:
                print(f"[BRIDGE] LED send failed: {e}")

    def start(self):
        """Start reading from hardware in a background thread."""
        self._running = True
        if self.mock:
            t = threading.Thread(target=self._mock_loop, daemon=True)
        else:
            t = threading.Thread(target=self._serial_loop, daemon=True)
        t.start()
        print(f"[BRIDGE] started ({'mock' if self.mock else self.port})")

    def stop(self):
        self._running = False
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass

    # Internal 

    def _update_reading(self, event: HardwareEvent):
        """Translate a hardware event into physiological_context fields."""
        with self._lock:
            e = event.event
            raw = event.raw

            if e == "button":
                self._reading.input_type = "buttons"
                btn_id   = raw.get("id", 0)
                held_ms  = raw.get("held_ms", 0)
                # Short press vs long press 
                press_type = "long press" if held_ms > 600 else "short press"
                names = {0: "round (back)", 1: "square (confirm)", 2: "oval (forward)"}
                btn_name = names.get(btn_id, f"button {btn_id}")
                print(f"[BRIDGE] {press_type} on {btn_name}")

            elif e in ("touch", "hold"):
                self._reading.input_type = "touch"
                pressure = raw.get("pressure", 0)
                duration = raw.get("duration_ms", 0)
                intent_signal = "strong" if pressure > 0.7 else "light"
                print(f"[BRIDGE] intent surface {e} — pressure={pressure:.2f} ({intent_signal})"
                      + (f" held {duration}ms" if e == "hold" else ""))

            elif e == "swipe":
                self._reading.input_type = "touch"
                direction = raw.get("direction", "unknown")
                print(f"[BRIDGE] intent surface swipe {direction}")

            elif e == "hr":
                bpm = raw.get("bpm")
                if bpm:
                    self._reading.heart_rate = str(bpm)
                    # stress indicator 
                    if bpm > 100:
                        self._reading.gaze = "potentially elevated stress (HR > 100)"
                    elif bpm < 55:
                        self._reading.gaze = "calm or resting"
                    print(f"[BRIDGE] heart rate {bpm} bpm")

    def _serial_loop(self):
        """Real serial port reader."""
        try:
            import serial
        except ImportError:
            print("[BRIDGE] pyserial not installed — run: pip install pyserial")
            return

        try:
            self._ser = serial.Serial(self.port, self.baud, timeout=1)
            print(f"[BRIDGE] opened {self.port} at {self.baud} baud")
        except Exception as e:
            print(f"[BRIDGE] could not open {self.port}: {e}")
            return

        buffer = ""
        while self._running:
            try:
                chunk = self._ser.read(self._ser.in_waiting or 1).decode("utf-8", errors="ignore")
                buffer += chunk
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            raw = json.loads(line)
                            event = HardwareEvent(event=raw.get("event", "unknown"), raw=raw)
                            self._queue.put(event)
                            self._update_reading(event)
                        except json.JSONDecodeError:
                            print(f"[BRIDGE] bad JSON: {line}")
            except Exception as e:
                print(f"[BRIDGE] read error: {e}")
                time.sleep(0.5)

    def _mock_loop(self):
        """Simulates XIAO hardware events for development without real hardware."""
        events = [
            {"event": "button", "id": 1, "held_ms": 130},# confirm tap
            {"event": "touch",  "pressure": 0.65},  # light IS touch
            {"event": "hold",   "pressure": 0.88, "duration_ms": 1200}, # deliberate hold
            {"event": "hr",     "bpm": 68},
            {"event": "button", "id": 0, "held_ms": 80},  # back
            {"event": "swipe",  "direction": "left"},
            {"event": "hr",     "bpm": 72},
            {"event": "touch",  "pressure": 0.30},  # accidental brush
            {"event": "button", "id": 2, "held_ms": 95},# forward
        ]
        while self._running:
            for e in events:
                if not self._running:
                    break
                event = HardwareEvent(event=e["event"], raw=e)
                self._queue.put(event)
                self._update_reading(event)
                time.sleep(random.uniform(1.5, 3.5))


# Port scanner 
def scan_ports():
    try:
        import serial.tools.list_ports
        ports = list(serial.tools.list_ports.comports())
        if not ports:
            print("No serial ports found.")
            return
        print("Available serial ports:")
        for p in ports:
            print(f"  {p.device:30s}  {p.description}")
    except ImportError:
        print("pyserial not installed — run: pip install pyserial")


# CLI 

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nova hardware bridge")
    parser.add_argument("--port",  default=None, help="Serial port, e.g. /dev/tty.usbmodem1101")
    parser.add_argument("--baud",  type=int, default=115200)
    parser.add_argument("--mock",  action="store_true", help="Run with simulated hardware events")
    parser.add_argument("--scan",  action="store_true", help="List available serial ports and exit")
    args = parser.parse_args()

    if args.scan:
        scan_ports()
        exit(0)

    if not args.port and not args.mock:
        print("Provide --port or use --mock for development. Use --scan to list ports.")
        exit(1)

    bridge = HardwareBridge(port=args.port, baud=args.baud, mock=args.mock)
    bridge.start()

    print("Bridge running. Press Ctrl+C to stop.")
    print("Current physiological context updates every time hardware fires:\n")
    try:
        while True:
            time.sleep(2)
            reading = bridge.get_physiological_context()
            print(f"  input_type={reading.input_type:12s} "
                  f"heart_rate={reading.heart_rate:5s} "
                  f"gaze={reading.gaze}")
    except KeyboardInterrupt:
        bridge.stop()
        print("\n[BRIDGE] stopped")
