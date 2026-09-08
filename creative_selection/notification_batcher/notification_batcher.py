"""
This sits between whatever generates notifications (calendar, messages, apps)
and whatever outputs them (LED ring via hardware_bridge, future display).
"""

import time
import threading
import json
from dataclasses import dataclass, field
from typing import List, Optional, Callable
from enum import Enum


#Types 

class Urgency(str, Enum):
    CRITICAL = "critical" # bypass everything
    HIGH     = "high"# deliver at next mode boundary
    LOW      = "low"  # hold until batch window
    AMBIENT  = "ambient" # only on pull


class Mode(str, Enum):
    DEFAULT  = "default"
    FOCUS    = "focus"
    LECTURE  = "lecture"
    SLEEP    = "sleep"


@dataclass
class Notification:
    id:        str
    source:    str              
    summary:   str # short text
    urgency:   Urgency
    received:  float = field(default_factory=time.time)
    delivered: bool = False

    def age_minutes(self) -> float:
        return (time.time() - self.received) / 60.0


#Batcher 

class NotificationBatcher:
    """
    Holds all incoming notifications and decides when to surface them.
        batcher = NotificationBatcher()
        batcher.set_led_callback(bridge.send_led_command)  # optional
        batcher.start()

        # Add a notification from anywhere
        batcher.add_notification(Notification(
            id="msg_001",
            source="messages",
            summary="Ella: are you coming to the meeting?",
            urgency=Urgency.LOW,
        ))

        # intent surface calls this to get what's waiting
        batch = batcher.get_pending_batch()

        # After showing the user:
        batcher.acknowledge(batch)
    """

    #LOW urgency notifications before forcing a batch
    BATCH_WINDOW_MINUTES = 30

    # In focus/lecture mode, hold LOW/AMBIENT indefinitely until mode exits
    # HIGH still delivers at mode boundary
    # CRITICAL always bypasses

    def __init__(self):
        self._queue: List[Notification] = []
        self._lock = threading.Lock()
        self._mode = Mode.DEFAULT
        self._running = False
        self._led_callback: Optional[Callable] = None  # send_led_command from bridge

        # Tracks when the last batch was delivered
        self._last_batch_time = time.time()

        # Tracks pending intent surface touch (set by hardware bridge)
        self._user_checking_in = threading.Event()

    #Public API 

    def set_led_callback(self, fn: Callable):
        """
        Pass hardware_bridge.send_led_command here so the batcher can
        update the LED ring whenever notification state changes.
        """
        self._led_callback = fn

    def set_mode(self, mode: str):
        """
        Called when the user changes Nova's mode (focus, lecture, default, sleep).
        On mode exit (e.g. leaving lecture), triggers a batch delivery.
        """
        old_mode = self._mode
        try:
            new_mode = Mode(mode)
        except ValueError:
            print(f"[BATCHER] unknown mode '{mode}', ignoring")
            return

        prev = self._mode
        self._mode = new_mode
        print(f"[BATCHER] mode: {prev.value} → {new_mode.value}")

        # On exit from focus/lecture, deliver what was held
        if prev in (Mode.FOCUS, Mode.LECTURE) and new_mode == Mode.DEFAULT:
            print("[BATCHER] mode exit — triggering batch delivery")
            self._user_checking_in.set()

        self._update_led()

    def add_notification(self, notification: Notification):
        """
        Add a notification to the queue.
        CRITICAL notifications bypass the queue and trigger immediate delivery.
        """
        with self._lock:
            if notification.urgency == Urgency.CRITICAL:
                print(f"[BATCHER] CRITICAL — bypassing queue: {notification.summary}")
                notification.delivered = True
                # Still goes through LED so the user sees something
                self._fire_led_immediate()
                # In a real system, this would also wake a display or haptic
                return

            self._queue.append(notification)
            count = len(self._queue)
            print(f"[BATCHER] queued ({count} pending): [{notification.urgency.value}] {notification.summary}")

        self._update_led()

    def notify_user_checking_in(self):
        """
        Called by hardware_bridge when user touches the intent surface.
        This is the user saying 'I'm ready to see what's waiting' — trigger
        a batch delivery at the next loop tick.
        """
        print("[BATCHER] intent surface touch — user checking in")
        self._user_checking_in.set()

    def get_pending_batch(self) -> List[Notification]:
        """
        Returns all deliverable notifications right now given the current mode.
        Does NOT clear them — call acknowledge() after showing them.

        Georgia's intent_surface can call this to include pending notifications
        in the context it gathers before passing to the LLM.
        """
        with self._lock:
            return [n for n in self._queue
                    if not n.delivered and self._is_deliverable(n)]

    def acknowledge(self, batch: List[Notification]):
        """Mark a batch as seen. Called after user views them."""
        ids = {n.id for n in batch}
        with self._lock:
            for n in self._queue:
                if n.id in ids:
                    n.delivered = True
            # Clean up delivered notifications older than 1 hour
            self._queue = [n for n in self._queue
                           if not n.delivered or n.age_minutes() < 60]
        self._last_batch_time = time.time()
        self._update_led()
        print(f"[BATCHER] acknowledged {len(batch)} notifications")

    def get_summary(self) -> dict:
        """Returns a status dict for debugging / display."""
        with self._lock:
            pending   = [n for n in self._queue if not n.delivered]
            by_urgency = {}
            for n in pending:
                by_urgency[n.urgency.value] = by_urgency.get(n.urgency.value, 0) + 1
            highest = self._highest_urgency(pending)
            return {
                "mode":        self._mode.value,
                "total":       len(pending),
                "by_urgency":  by_urgency,
                "highest":     highest,
                "minutes_since_last_batch": (time.time() - self._last_batch_time) / 60,
            }

    def start(self):
        """Start the background batch-window timer."""
        self._running = True
        t = threading.Thread(target=self._timer_loop, daemon=True)
        t.start()
        print("[BATCHER] started")

    def stop(self):
        self._running = False
        self._user_checking_in.set() # unblock the wait

    #Internal 

    def _is_deliverable(self, n: Notification) -> bool:
        """Can this notification be delivered right now given the current mode?"""
        if n.urgency == Urgency.CRITICAL:
            return True  # always (handled separately above, but just in case)
        if n.urgency == Urgency.AMBIENT:
            return False # never delivered proactively, only on explicit pull
        if self._mode in (Mode.FOCUS, Mode.LECTURE):
            return n.urgency == Urgency.HIGH  #only high in restricted modes
        return True  # default mode: deliver everything

    def _highest_urgency(self, notifications: List[Notification]) -> str:
        """Returns the highest urgency level in a list (for LED colour)."""
        order = [Urgency.CRITICAL, Urgency.HIGH, Urgency.LOW, Urgency.AMBIENT]
        for u in order:
            if any(n.urgency == u for n in notifications):
                return u.value
        return "none"

    def _update_led(self):
        """Tell the LED ring what to show based on current state."""
        if not self._led_callback:
            return
        with self._lock:
            pending = [n for n in self._queue
                       if not n.delivered and self._is_deliverable(n)]
            count   = len(pending)
            urgency = self._highest_urgency(pending) if pending else "none"
        self._led_callback(self._mode.value, count, urgency)

    def _fire_led_immediate(self):
        """Immediate LED flash for CRITICAL notifications."""
        if self._led_callback:
            self._led_callback("default", 1, "critical")

    def _timer_loop(self):
        """
        Background thread — checks every minute whether the batch window has
        elapsed and triggers delivery if so.
        """
        while self._running:
            #Wait up to 60 seconds, or until user checks in
            triggered = self._user_checking_in.wait(timeout=60)
            if not self._running:
                break

            self._user_checking_in.clear()

            minutes_elapsed = (time.time() - self._last_batch_time) / 60.0

            with self._lock:
                pending_low = [n for n in self._queue
                               if not n.delivered and n.urgency == Urgency.LOW]

            # Deliver if: user checked in, OR batch window elapsed in default mode
            should_deliver = (
                triggered or
                (self._mode == Mode.DEFAULT and
                 minutes_elapsed >= self.BATCH_WINDOW_MINUTES and
                 pending_low)
            )

            if should_deliver:
                batch = self.get_pending_batch()
                if batch:
                    print(f"\n[BATCHER] ── batch ready ({len(batch)} notifications) ──")
                    for n in batch:
                        age = n.age_minutes()
                        print(f"  [{n.urgency.value:8s}] {n.source:12s} | {n.summary}"
                              f"  ({age:.0f}m ago)")
                    print("[BATCHER] call batcher.acknowledge(batch) after showing these\n")
                    # this would also signal the intent surface, to include the batch in the next context gather


# Demo 

if __name__ == "__main__":
    print("Nova Notification Batcher — demo\n")

    # Simulate the LED callback (normally hardware_bridge.send_led_command)
    def mock_led(mode, count, urgency):
        print(f"  [LED] mode={mode:8s} count={count} urgency={urgency}")

    batcher = NotificationBatcher()
    batcher.set_led_callback(mock_led)
    batcher.BATCH_WINDOW_MINUTES = 0.1  # 6 seconds for demo
    batcher.start()

    print(" Simulating incoming notifications \n")
    time.sleep(0.5)

    batcher.add_notification(Notification("1", "messages", "Jay: are you coming?",   Urgency.LOW))
    time.sleep(0.3)
    batcher.add_notification(Notification("2", "calendar", "Lecture starts in 5min", Urgency.HIGH))
    time.sleep(0.3)
    batcher.add_notification(Notification("3", "messages", "Riley: can you review?", Urgency.LOW))
    time.sleep(0.3)

    print("\n Switching to focus mode \n")
    batcher.set_mode("focus")
    time.sleep(0.5)

    batcher.add_notification(Notification("4", "messages", "Naoise: check the PCB", Urgency.LOW))
    batcher.add_notification(Notification("5", "system",   "Battery at 15%",        Urgency.HIGH))
    time.sleep(0.5)

    print("\n Check what's deliverable in focus mode ")
    batch = batcher.get_pending_batch()
    print(f"  Deliverable now: {len(batch)} (only HIGH in focus mode)")
    for n in batch:
        print(f"  → [{n.urgency.value}] {n.summary}")

    print("\nSwitching back to default\n")
    batcher.set_mode("default")
    time.sleep(0.5)

    print("\n User touches intent surface ")
    batcher.notify_user_checking_in()
    time.sleep(0.5)

    print("\n Full pending batch ")
    batch = batcher.get_pending_batch()
    for n in batch:
        print(f"  → [{n.urgency.value}] {n.source}: {n.summary}")

    batcher.acknowledge(batch)
    time.sleep(0.3)

    print("\n Summary ")
    print(json.dumps(batcher.get_summary(), indent=2))

    batcher.stop()
