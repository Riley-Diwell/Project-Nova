"""Continuous silent-speech app: talk to teach (live caption), mouth to predict.

VOICED mode (default): speak naturally. A mic energy VAD chops your speech into
phrases; each phrase is transcribed by Whisper; the lip motion for the WHOLE
phrase is saved as one sample tagged with the concatenated CMUdict phoneme
sequence (CTC learns the alignment, so no fragile per-word slicing). Recognized
words scroll as a live caption. Train the phoneme CTC model offline afterwards
(python phoneme_model.py --train), not live.

SILENT mode (press SPACE): the mic is ignored. A lip-motion VAD detects mouthed
words and the decoder predicts each one (after Kalman smoothing), showing its
best guess in the caption. This is provisional (isolated-word SVM) until the
phoneme CTC model lands -- it is where the LLM will later rescore low-confidence
guesses using the phoneme candidates from phoneme_lexicon.

Controls:  SPACE = toggle VOICED/SILENT    ESC = quit

Note: audio and camera share one wall clock; the whole-phrase window is used
(no per-word slicing), which is robust to the ~100ms audio/frame timing offset.
"""
from __future__ import annotations

import os
import queue
import threading
import time
from collections import deque

import cv2
import numpy as np
import sounddevice as sd

from signal_source import WebcamLipSource
from kalman import KalmanSmoother
from asr_labeler import WhisperASR, SR
from phoneme_lexicon import PhonemeLexicon, WORD_SEP
from decoder import DigitDecoder, train_and_save, MODEL_PATH
import dataset_store
import record_session

AUDIO_BLOCK = 1600          # 100 ms @ 16 kHz
VOICED_RMS = 350.0          # speech vs silence (int16 RMS)
HANGOVER = 0.4              # silence needed to end a phrase (s)
MIN_SEG = 0.25              # ignore phrases shorter than this (s)
LIP_MOTION_ON = 0.020       # normalized lip motion to start a mouthed word
RETRAIN_EVERY = 8           # retrain after this many new samples
KQ, KR = 5e-3, 0.25
SIL_MOTION_WIN = 10         # frames for the responsive motion gate (~0.4s)
SIL_MARGIN = 0.0025         # motion above resting level that counts as mouthing
SIL_HANGOVER = 6            # still frames that end a mouthed word (~0.25s)
SIL_MIN_SEG = 6             # ignore mouthed segments shorter than this
SIL_MAX_SEG = 90            # force a decode if motion never settles (~4.5s)


# --------------------------------------------------------------------------- #
# Audio capture + energy VAD -> completed phrase segments
# --------------------------------------------------------------------------- #
class AudioVAD:
    def __init__(self):
        self.q: "queue.Queue[np.ndarray]" = queue.Queue()
        self.stream = None
        self.buf: list[np.ndarray] = []
        self.in_speech = False
        self.silence_s = 0.0
        self.seg_start = 0.0
        self.rms = 0.0

    def _cb(self, indata, frames, t, status):
        self.q.put(indata.copy().reshape(-1))

    def start(self):
        self.stream = sd.InputStream(samplerate=SR, blocksize=AUDIO_BLOCK,
                                     channels=1, dtype="int16", callback=self._cb)
        self.stream.start()

    def stop(self):
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self.buf, self.in_speech, self.silence_s = [], False, 0.0

    def poll(self):
        """Return list of (segment_audio_int16, seg_start_walltime)."""
        segs = []
        while not self.q.empty():
            block = self.q.get()
            dur = len(block) / SR
            self.rms = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))
            voiced = self.rms > VOICED_RMS
            if voiced:
                if not self.in_speech:
                    self.in_speech = True
                    self.seg_start = time.time() - dur
                    self.buf = []
                self.buf.append(block)
                self.silence_s = 0.0
            elif self.in_speech:
                self.buf.append(block)
                self.silence_s += dur
                if self.silence_s >= HANGOVER:
                    seg = np.concatenate(self.buf)
                    if len(seg) / SR - self.silence_s >= MIN_SEG:
                        segs.append((seg, self.seg_start))
                    self.in_speech, self.buf, self.silence_s = False, [], 0.0
        return segs


# --------------------------------------------------------------------------- #
# Scrolling caption
# --------------------------------------------------------------------------- #
class Caption:
    def __init__(self, width=52, lines=3):
        self.words: list[str] = []
        self.width, self.lines = width, lines

    def add(self, words):
        self.words.extend(words)
        self.words = self.words[-60:]

    def render(self):
        out, line = [], ""
        for w in self.words:
            if len(line) + len(w) + 1 > self.width:
                out.append(line)
                line = w
            else:
                line = (line + " " + w).strip()
        out.append(line)
        return out[-self.lines:]


def save_sample(source, label, raw, phones):
    return dataset_store.append(raw, label, phones, modality=source.modality,
                                feature_names=source.feature_names)


def slice_feats(lipbuf, t0, t1, pad=0.15):
    return np.array([f for (t, f) in list(lipbuf) if t0 - pad <= t <= t1 + pad])


def distinct_labels():
    return set(dataset_store.label_counts().keys())


def asr_worker(asr, lex, source, seg_queue, result_queue, state):
    """Background thread: transcribe phrases, slice/save lip data, retrain.

    Keeps all the heavy CPU work (Whisper, SVM retrain) off the render loop so
    the camera and caption stay smooth. Communicates via queues only.
    """
    while True:
        item = seg_queue.get()
        if item is None:
            break
        seg, seg_start = item
        try:
            words = asr.transcribe_words(seg, SR)
        except Exception as e:
            result_queue.put(("note", f"asr error: {e}"))
            continue
        if not words:
            continue
        tokens = [w["word"] for w in words]
        result_queue.put(("words", tokens))
        # Save the WHOLE phrase as one sample: features over the full segment,
        # target = concatenated CMUdict phonemes of every word. CTC learns the
        # alignment, so no fragile per-word slicing.
        snap = list(state["lipbuf"])
        raw = slice_feats(snap, seg_start, seg_start + len(seg) / SR)
        phones = []
        for tok in tokens:
            prn = lex.word_to_phonemes(tok)
            if prn:
                if phones:
                    phones.append(WORD_SEP)     # mark the word boundary
                phones.extend(prn[0])
        if len(raw) < 3 or not phones:
            continue
        if len(raw) < len(phones):          # CTC needs frames >= phonemes
            result_queue.put(("note", f"skipped '{' '.join(tokens)}' (too fast)"))
            continue
        save_sample(source, " ".join(tokens), raw, phones)
        state["new"] += 1
        result_queue.put(("note", f"saved phrase (+{state['new']}): "
                          f"{len(phones)} phonemes, {len(raw)} frames"))


def silent_worker(n_features, lex, feat_q, result_queue):
    """Background thread: SEGMENTED CTC decode for SILENT (predict) mode.

    A short, responsive lip-motion gate detects each mouthed word's onset and
    offset; the accumulated segment is decoded ONCE when the word ends. This is
    why it feels snappy: no heavy inference runs while you are mouthing (just a
    ~1ms motion check per frame, so the video stays smooth), and each prediction
    lands right after you finish the word instead of trailing a rolling 3.5s
    window. Owns the phoneme model (hot-reloaded), the Kalman smoother, and the
    auto-calibrating gate. Communicates via queues only.
    """
    try:
        import torch
        torch.set_num_threads(1)                # leave cores for MediaPipe/camera
        from phoneme_model import (CTCNet, greedy_decode,
                                   featurize as ph_feat, MODEL as PH_MODEL)
    except Exception as e:                      # torch/model import failed
        result_queue.put(("note", f"CTC unavailable: {e}"))
        return

    ks = KalmanSmoother(n_features, dt=1.0, q=KQ, r=KR)
    motion_win: "deque" = deque(maxlen=SIL_MOTION_WIN)
    seg: list = []             # frames of the word currently being mouthed
    active = False             # inside a mouthed word?
    quiet = 0                  # consecutive still frames since motion last seen
    rest_motion = 0.005        # running estimate of "still" lip motion
    pm = None
    pm_mtime = 0.0
    i = 0

    def reload_model():
        nonlocal pm, pm_mtime
        if not os.path.exists(PH_MODEL):
            return
        mt = os.path.getmtime(PH_MODEL)
        if mt == pm_mtime:
            return
        try:
            m = pm if pm is not None else CTCNet()
            m.load_state_dict(torch.load(PH_MODEL, map_location="cpu")["state"])
            m.eval()
            pm, pm_mtime = m, mt
        except Exception:
            pass

    def decode_segment(frames, m, gate):
        """Decode one mouthed-word segment and post phones/words to the UI.

        The RAW segment frames ride along so the render loop can save this exact
        utterance as a silent-labeled training sample when the user confirms or
        corrects the guess (the reinforcement loop that closes the domain gap).
        """
        if pm is None or len(frames) < SIL_MIN_SEG:
            return
        raw = np.array(frames, dtype=np.float32)
        sm = ks.smooth(raw.astype(np.float64))
        x = ph_feat(sm).astype(np.float32)
        with torch.no_grad():
            logp = pm(torch.from_numpy(x)[None])[0]
        phones = greedy_decode(logp)
        words = lex.decode_words(phones)
        result_queue.put(("silent", (" ".join(phones), words, m, gate, raw)))

    while True:
        item = feat_q.get()
        if item is None:
            break
        frames = [item]
        stop = False
        try:                                    # drain backlog, process each
            while True:
                nxt = feat_q.get_nowait()
                if nxt is None:
                    stop = True
                    break
                frames.append(nxt)
        except queue.Empty:
            pass

        for feat in frames:
            i += 1
            if i % 30 == 1 or pm is None:
                reload_model()
            motion_win.append(feat)
            if len(motion_win) < 3:
                continue
            m = float(np.mean(np.abs(np.diff(np.array(motion_win, np.float64),
                                             axis=0))))
            gate = rest_motion + SIL_MARGIN
            # Track the RESTING level: snap down instantly to new lows, creep up
            # ONLY while at rest, so sustained mouthing can't drag the gate over
            # its own signal.
            if m < rest_motion:
                rest_motion = m
            elif m <= gate:
                rest_motion += 0.00003
            gate = rest_motion + SIL_MARGIN

            if m > gate:                        # mouthing
                if not active:
                    active, quiet = True, 0
                    seg = list(motion_win)      # include the onset ramp
                else:
                    seg.append(feat)
                    quiet = 0
                if len(seg) >= SIL_MAX_SEG:     # runaway -> commit and reset
                    decode_segment(seg, m, gate)
                    active, seg, quiet = False, [], 0
            elif active:                        # trailing still frames
                seg.append(feat)
                quiet += 1
                if quiet >= SIL_HANGOVER:       # word ended -> decode once
                    decode_segment(seg, m, gate)
                    active, seg, quiet = False, [], 0
            if i % 4 == 0:                      # light HUD meter (motion/gate)
                result_queue.put(("silent_meter", (m, gate)))
        if stop:
            break


def main():
    source = WebcamLipSource()
    asr = WhisperASR()
    lex = PhonemeLexicon()
    # Closed-vocabulary decode: restrict fuzzy phoneme->word matching to the words
    # actually trained on (labels with a few reps), so a noisy phoneme guess maps
    # to the nearest of those words instead of the nearest of ~126k CMUdict words.
    # Drops stray low-count labels that leaked in from voiced free-speech capture.
    _counts = dataset_store.label_counts()
    _vocab = sorted({lab for lab, n in _counts.items()
                     if n >= 5 and " " not in lab and lab.strip()})
    nprons = lex.restrict_vocab(_vocab)
    print(f"[vocab] closed decode over {len(_vocab)} words "
          f"({nprons} pronunciations): {', '.join(_vocab) or '(none yet)'}",
          flush=True)
    source.open()
    audio = AudioVAD()
    audio.start()
    caption = Caption()
    lipbuf: "deque[tuple]" = deque(maxlen=1200)   # ~60s of frames

    decoder = DigitDecoder() if os.path.exists(MODEL_PATH) else None
    mode = "voiced"
    note = "VOICED: speak naturally to teach"

    seg_queue: "queue.Queue" = queue.Queue()
    result_queue: "queue.Queue" = queue.Queue()
    wstate = {"lipbuf": lipbuf, "new": 0}
    threading.Thread(target=asr_worker, daemon=True,
                     args=(asr, lex, source, seg_queue, result_queue,
                           wstate)).start()

    # Continuous streaming silent decode runs in its own thread (build #3) so
    # CTC inference never blocks the camera. The worker owns the phoneme model
    # (hot-reloaded from train_daemon), the rolling window, and the motion gate;
    # here we just feed it frames and read back live phonemes/words.
    from phoneme_model import MODEL as PH_MODEL
    sil_queue: "queue.Queue" = queue.Queue(maxsize=8)
    threading.Thread(target=silent_worker, daemon=True,
                     args=(source.n_features, lex, sil_queue,
                           result_queue)).start()
    live_phones, live_words = "", "(mouth a word)"
    cur_wm = 0.0               # latest window motion (from the worker, for HUD)
    cur_gate = 0.0

    # --- silent-mode correction / reinforcement state ---
    vocab_set = set(_vocab)    # words the closed decode currently knows
    last_seg = None            # raw frames of the last mouthed word (to relabel)
    last_pred = ""             # its predicted word
    typing = False             # user is entering a correction?
    buf = ""                   # correction text being typed
    corr_n = 0                 # corrections/confirmations saved this session

    def save_labeled(word):
        """Save the last mouthed segment as a silent-labeled training sample.

        This is the reinforcement loop: confirming or correcting a guess adds a
        real silent-domain example the train_daemon will learn from (and the app
        hot-reloads the improved model). New words are added to the closed vocab
        live so they can be decoded immediately (fully only after a retrain)."""
        nonlocal last_seg, last_pred, corr_n, note, live_words
        word = word.strip().lower()
        if last_seg is None or not word:
            return
        prn = lex.word_to_phonemes(word)
        phones = list(prn[0]) if prn else []
        dataset_store.append(last_seg, word, phones, modality=source.modality,
                             feature_names=source.feature_names, voicing="silent")
        if " " not in word and word not in vocab_set:
            vocab_set.add(word)
            lex.restrict_vocab(sorted(vocab_set))   # decode it right away
        corr_n += 1
        note = f"saved '{word}' (silent, +{corr_n}) - trainer will learn it"
        live_words, last_seg, last_pred = word, None, ""

    try:
        while True:
            feat = source.read()
            now = time.time()
            if feat is not None:
                lipbuf.append((now, feat))

            if mode == "voiced":
                for seg, seg_start in audio.poll():
                    if seg_queue.qsize() < 3:      # drop if worker is behind
                        seg_queue.put((seg, seg_start))

            # Feed the silent-decode worker the freshest frame; drop rather than
            # block if it is still busy on the previous window.
            if mode == "silent" and feat is not None:
                try:
                    sil_queue.put_nowait(feat)
                except queue.Full:
                    pass

            # apply background-worker results (words, notes, silent decode)
            while not result_queue.empty():
                kind, val = result_queue.get()
                if kind == "words":
                    caption.add(val)
                elif kind == "note":
                    note = val
                elif kind == "decoder":
                    decoder = val
                elif kind == "silent":          # a mouthed word was decoded
                    ph_str, words, cur_wm, cur_gate, raw_seg = val
                    live_phones = ph_str
                    live_words = " ".join(words) if words else "(no match)"
                    if not typing:              # hold it for confirm/correct
                        last_seg = raw_seg
                        last_pred = words[0] if words else ""
                elif kind == "silent_meter":    # live motion/gate for the HUD
                    cur_wm, cur_gate = val

            # ---- draw ----
            frame = source.last_frame
            frame = (np.zeros((480, 640, 3), np.uint8) if frame is None
                     else frame.copy())
            face = getattr(source, "last_face_ok", False)
            hud = "VOICED (teach)" if mode == "voiced" else "SILENT (predict)"
            col = (0, 220, 0) if mode == "voiced" else (0, 180, 255)
            cv2.putText(frame, f"{hud}   SPACE=switch  ESC=quit", (12, 26),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)
            if mode == "voiced":
                status = f"face:{'Y' if face else 'N'}  rms:{audio.rms:0.0f}  {note}"
            else:
                status = (f"face:{'Y' if face else 'N'}  "
                          f"motion:{cur_wm:0.3f}/gate:{cur_gate:0.3f}  {note}")
            cv2.putText(frame, status, (12, 50), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (200, 200, 200), 1)
            # correction / reinforcement prompt (silent mode)
            if mode == "silent":
                if typing:
                    prompt = f"correct word: {buf}_   [Enter=save  Esc=cancel]"
                    pcol = (0, 220, 255)
                elif last_seg is not None:
                    guess = last_pred or "(no match)"
                    prompt = f"guess '{guess}'   [Y]=correct   [N]=fix"
                    pcol = (0, 220, 255)
                else:
                    prompt = ""
                if prompt:
                    cv2.putText(frame, prompt, (12, 74),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.55, pcol, 1)
            # bottom ribbon: caption (voiced) or live phoneme stream (silent)
            y = frame.shape[0] - 10 - 26 * (caption.lines - 1)
            cv2.rectangle(frame, (0, y - 30),
                          (frame.shape[1], frame.shape[0]), (0, 0, 0), -1)
            if mode == "silent":
                if not os.path.exists(PH_MODEL):
                    ribbon = ["no phoneme model yet - collect data, then run",
                              "phoneme_model.py --train"]
                else:
                    ribbon = [f"phonemes: {live_phones}", f"~word: {live_words}"]
            else:
                ribbon = caption.render()
            for line in ribbon:
                cv2.putText(frame, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX,
                            0.7, (255, 255, 255), 2)
                y += 26
            cv2.imshow("Silent Speech - Continuous", frame)

            k = cv2.waitKey(1) & 0xFF

            if typing:                          # entering a correction: capture text
                if k in (13, 10):               # Enter -> save the typed label
                    save_labeled(buf)
                    typing, buf = False, ""
                elif k == 27:                   # Esc -> cancel (does NOT quit)
                    typing, buf, note = False, "", "correction cancelled"
                elif k == 8:                    # Backspace
                    buf = buf[:-1]
                elif k == ord("'") or (65 <= k <= 90) or (97 <= k <= 122):
                    buf += chr(k).lower()
                continue                        # swallow all other keys while typing

            if k == 27:
                break
            if mode == "silent" and last_seg is not None:
                if k in (ord("y"), ord("Y")) and last_pred:
                    save_labeled(last_pred)     # confirm: reinforce the guess
                elif k in (ord("n"), ord("N")):
                    typing, buf = True, ""       # fix: type the correct word
            if k == 32:
                if mode == "voiced":
                    audio.stop()
                    mode, note = "silent", "SILENT: mouth words to predict"
                    live_phones, live_words = "", "(mouth a word)"
                    last_seg, last_pred = None, ""
                else:
                    audio.start()
                    mode, note = "voiced", "VOICED: speak naturally to teach"
                    last_seg, last_pred, typing, buf = None, "", False, ""
                    with sil_queue.mutex:      # stop feeding the silent worker
                        sil_queue.queue.clear()
    finally:
        audio.stop()
        source.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
