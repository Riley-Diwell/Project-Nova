"""Voice first: while a voice turn runs, model calls nobody is waiting on hold
back (core/llm.py voice_turn / yield_to_voice), capped so nothing starves."""
import threading
import time

from app.core import llm


def _hold_voice_turn(seconds: float) -> threading.Thread:
    """A voice turn on another thread, running for `seconds`."""
    started = threading.Event()

    def turn():
        with llm.voice_turn():
            started.set()
            time.sleep(seconds)

    thread = threading.Thread(target=turn)
    thread.start()
    started.wait()
    return thread


def test_no_voice_turn_no_wait():
    start = time.monotonic()
    llm.yield_to_voice()
    assert time.monotonic() - start < 0.05


def test_background_call_waits_for_the_voice_turn():
    turn = _hold_voice_turn(0.3)
    start = time.monotonic()
    llm.yield_to_voice()
    waited = time.monotonic() - start
    turn.join()
    assert 0.2 < waited < 1.0


def test_the_voice_turn_never_waits_on_itself():
    turn = _hold_voice_turn(0.5)
    with llm.voice_turn():
        start = time.monotonic()
        llm.yield_to_voice()
        assert time.monotonic() - start < 0.05
    turn.join()


def test_the_wait_is_capped():
    turn = _hold_voice_turn(1.0)
    start = time.monotonic()
    llm.yield_to_voice(max_wait=0.1)
    waited = time.monotonic() - start
    turn.join()
    assert waited < 0.5


def test_a_thread_started_inside_a_voice_turn_still_waits():
    waited: list[float] = []

    def background():
        start = time.monotonic()
        llm.yield_to_voice()
        waited.append(time.monotonic() - start)

    with llm.voice_turn():
        thread = threading.Thread(target=background)
        thread.start()
        time.sleep(0.2)
    thread.join()
    assert waited[0] > 0.1
