"""

Flow:
  ESP32 (button press) 
    → BLE audio stream 
    → speech-to-text (Whisper)
    → Georgia's context 
    → react_intent
    → tool dispatch 
    → response text 
    → BLE → haptic confirm on ESP32

Also runs:
  - notification batcher (background)
  - mode switcher (auto-updates LED ring from context)
  - hardware bridge 

"""

import asyncio
import argparse
import json
import os
import sys
import threading
import time
import tempfile
from pathlib import Path

from tool_set_reminder import set_reminder
from tool_send_message  import send_message
from tool_query_memory  import query_memory

sys.path.insert(0, str(Path(__file__).parent.parent / "nova_code"))
sys.path.insert(0, str(Path(__file__).parent.parent / "nova_georgia"))

try:
    from notification_batcher import NotificationBatcher, Notification, Urgency
    from urgency_classifier   import classify_urgency
    from mode_switcher        import ModeSwitcher
    BATCHER_AVAILABLE = True
except ImportError:
    BATCHER_AVAILABLE = False
    print("[MAIN] notification modules not found — batcher disabled")

try:
    from react_intent import infer_intent
    REACT_AVAILABLE = True
except ImportError:
    REACT_AVAILABLE = False
    print("[MAIN] react_intent not found — using keyword fallback")

try:
    import whisper
    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False
    print("[MAIN] whisper not installed — using mock transcription")


TOOL_REGISTRY = {
    "set_reminder":   set_reminder,
    "send_message":   send_message,
    "query_memory":   query_memory,
    "get_information":query_memory,   # alias
    "unknown":        None,
}

def dispatch_tool(action: str, transcript: str, context=None) -> dict:
    """Route an inferred intent to the correct tool."""
    fn = TOOL_REGISTRY.get(action)
    if fn is None:
        return {"success": False,
                "spoken": "I'm not sure how to help with that yet. "
                          "I can set reminders, send messages, or search your memory."}
    return fn(transcript, context)


#Whisper transcription 
_whisper_model = None

def transcribe(audio_bytes: bytes) -> str:
    """Convert raw 16kHz PCM bytes to text using Whisper."""
    global _whisper_model

    if not WHISPER_AVAILABLE:
        # Mock transcription for demo without Whisper installed
        mocks = [
            "remind me to call Jay at 3pm",
            "message Riley that the build is ready",
            "what did I need to do today",
        ]
        import random
        result = random.choice(mocks)
        print(f"[STT] mock transcript: '{result}'")
        return result

    if _whisper_model is None:
        print("[STT] loading Whisper base.en model...")
        _whisper_model = whisper.load_model("base.en")

    # Write raw PCM to a temp wav file then transcribe
    import wave, struct
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        wav_path = f.name
        with wave.open(f, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)   # 16-bit
            wf.setframerate(16000)
            wf.writeframes(audio_bytes)

    result = _whisper_model.transcribe(wav_path, language="en")["text"].strip()
    os.unlink(wav_path)
    print(f"[STT] transcript: '{result}'")
    return result


# Simple keyword intent fallback (if react_intent unavailable)
def keyword_intent(transcript: str) -> str:
    t = transcript.lower()
    if any(w in t for w in ["remind", "reminder", "remember", "don't forget"]):
        return "set_reminder"
    if any(w in t for w in ["message", "text", "tell", "send", "msg"]):
        return "send_message"
    if any(w in t for w in ["what", "who", "when", "find", "search", "recall", "memory", "note"]):
        return "query_memory"
    return "unknown"


#BLE mock (for demo without hardware) 
class MockBLE:
    """Simulates BLE events for development without an ESP32."""

    def __init__(self, on_audio_ready, on_button):
        self._on_audio  = on_audio_ready
        self._on_button = on_button
        self._running   = False

    def start(self):
        self._running = True
        t = threading.Thread(target=self._loop, daemon=True)
        t.start()
        print("[BLE] mock started — simulating button presses every 10 seconds")

    def _loop(self):
        import random
        while self._running:
            time.sleep(10)
            held_ms = random.randint(80, 200)
            print(f"\n[BLE] mock button press ({held_ms}ms)")
            self._on_button({"event": "button", "held_ms": held_ms})
            # Simulate 2 seconds of audio after button
            time.sleep(0.5)
            mock_audio = bytes(16000 * 2 * 2)  # 2s of silence
            self._on_audio(mock_audio)

    def send(self, payload: str):
        print(f"[BLE → ESP32] {payload}")

    def stop(self):
        self._running = False


#Main pipeline 

class NovaPipeline:
    def __init__(self, mock: bool = False):
        self.mock = mock
        self._audio_buffer  = bytearray()
        self._recording     = False
        self._ble           = None
        self._context_cache = None

        # Notification batcher
        if BATCHER_AVAILABLE:
            self.batcher = NotificationBatcher()
            self.batcher.set_led_callback(self._led_callback)
            self.batcher.start()
            self.mode_switcher = ModeSwitcher(
                led_callback=self._led_callback,
                batcher=self.batcher,
            )
        else:
            self.batcher       = None
            self.mode_switcher = None

    def start(self):
        if self.mock:
            self._ble = MockBLE(
                on_audio_ready=self._on_audio_ready,
                on_button=self._on_button,
            )
            self._ble.start()
        else:
            # Real BLE — connect to nova_wearable
            # TODO: integrate bleak BLE client here
            print("[MAIN] real BLE not yet integrated — run with --mock")
            return

        print("\n[NOVA V1] pipeline running")
        print("  Tools: set_reminder | send_message | query_memory")
        print("  Waiting for button press from wearable...\n")

        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[NOVA] shutting down")
            if self.batcher:
                self.batcher.stop()

    def _on_button(self, event: dict):
        """Button pressed → start recording."""
        held_ms = event.get("held_ms", 0)
        if held_ms > 600:
            # Long press → check pending notifications
            if self.batcher:
                self.batcher.notify_user_checking_in()
                batch = self.batcher.get_pending_batch()
                if batch:
                    summary = f"{len(batch)} notifications waiting: " + \
                              "; ".join(n.summary for n in batch[:3])
                    self._speak(summary)
                    self.batcher.acknowledge(batch)
                else:
                    self._speak("Nothing waiting.")
        else:
            # Short press → begin listening
            print("[NOVA] listening...")
            self._audio_buffer.clear()
            self._recording = True
            # In real hardware, send record:start to ESP32
            if self._ble:
                self._ble.send('{"cmd":"record","state":"start"}')

    def _on_audio_ready(self, audio_bytes: bytes):
        """Audio chunk received → transcribe → process."""
        if self._ble:
            self._ble.send('{"cmd":"record","state":"stop"}')
        self._recording = False

        print("[NOVA] processing...")

        # 1. Transcribe
        transcript = transcribe(audio_bytes)
        if not transcript:
            self._speak("Sorry, I didn't catch that.")
            return

        # 2. Gather context (Georgia's intent surface)
        context = self._get_context(transcript)

        # 3. Update mode from context
        if self.mode_switcher and context:
            self.mode_switcher.update(context)

        # 4. Infer intent
        if REACT_AVAILABLE and context:
            try:
                import json as _j
                ctx_json = _j.dumps(context) if isinstance(context, dict) else str(context)
                intent   = infer_intent(ctx_json)
                action   = intent.action
                print(f"[NOVA] intent: {action} (confidence={intent.confidence:.0%})")
            except Exception as e:
                print(f"[NOVA] react_intent failed: {e}, falling back to keywords")
                action = keyword_intent(transcript)
        else:
            action = keyword_intent(transcript)
            print(f"[NOVA] intent (keyword): {action}")

        # 5. Dispatch tool
        result = dispatch_tool(action, transcript, context)

        # 6. Speak response
        spoken = result.get("spoken") or result.get("stored") or \
                 result.get("confirmation") or ("Done." if result.get("success") else "Something went wrong.")
        self._speak(spoken)

        # 7. Send Android command if needed (send_message)
        if "ble_command" in result and self._ble:
            self._ble.send(result["ble_command"])

    def _get_context(self, transcript: str) -> dict:
        """Call Georgia's context gatherer. Returns dict or None."""
        try:
            # Try Gemini version first, fall back to Qwen
            try:
                from gemini_intent_surface import gather_context, change_state
                import asyncio as _asyncio
                state   = change_state(transcript)
                context = _asyncio.run(gather_context(transcript, state))
                if hasattr(context, "model_dump"):
                    return context.model_dump()
                return context
            except Exception:
                pass

            try:
                from qwen_intent_surface import gather_context as qwen_gather, change_state
                state   = change_state(transcript)
                context = qwen_gather(transcript)
                if hasattr(context, "model_dump"):
                    return context.model_dump()
                return context
            except Exception:
                pass

        except Exception as e:
            print(f"[NOVA] context assembly failed: {e}")

        # Fallback minimal context
        from datetime import datetime
        return {
            "immediate_context": {
                "time": datetime.now().strftime("%H:%M"),
                "day":  datetime.now().strftime("%A %d %B"),
                "location": "N/A", "current_events": "N/A",
                "future_events": "N/A", "weather": "N/A",
                "prompt": transcript, "task": "N/A", "application": "N/A",
            },
            "behavioural_context": {
                "similar_routines": "N/A", "conflicting_routines": "N/A",
                "previous_corrections": "N/A",
            },
            "physiological_context": {
                "input_type": "voice", "input_tone": "neutral",
                "heart_rate": "N/A", "gaze": "N/A", "posture": "N/A",
            },
        }

    def _speak(self, text: str):
        """TTS → BLE. V1: prints to console. V2: real TTS → audio stream."""
        print(f"\n[NOVA → user] \"{text}\"\n")
        if self._ble:
            self._ble.send(json.dumps({"cmd": "speak", "text": text}))
        # Haptic confirm
        if self._ble:
            self._ble.send('{"cmd":"haptic","pattern":"short"}')

    def _led_callback(self, mode: str, count: int, urgency: str):
        """Update LED ring — send to ESP32 via BLE."""
        cmd = json.dumps({"cmd": "led", "mode": mode,
                          "notifications": count, "urgency": urgency})
        if self._ble:
            self._ble.send(cmd)
        else:
            print(f"[LED] mode={mode} count={count} urgency={urgency}")


#CLI 
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nova V1 orchestrator")
    parser.add_argument("--mock", action="store_true",
                        help="Run with mock BLE (no hardware needed)")
    args = parser.parse_args()

    print("=" * 55)
    print("  NOVA V1 — Personal Computing System")
    print("  Tools: set reminder | send message | query memory")
    print("=" * 55)

    os.makedirs(os.path.join(os.path.dirname(__file__), "data"), exist_ok=True)

    pipeline = NovaPipeline(mock=args.mock or True)
    pipeline.start()
