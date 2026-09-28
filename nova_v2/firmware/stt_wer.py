#!/usr/bin/env python3
"""
STT word-error-rate gate for voice notes

Decides whether on-phone Vosk small is good enough for lecture captures, or
whether it's worth moving to Vosk lgraph or offering opt-in enhanced
(Whisper-class) transcription. The bar: WER above ~25% on real lecture
audio from the device means Vosk small is unusable for captures.

INPUTS - a directory of recordings, each with a hand-typed reference:

    lecture1.adpcm   audio kept on the phone (Settings -> Notes -> Keep
                     recordings), pulled off with:
                       adb exec-out run-as com.example.novav2 \\
                         cat no_backup/note_audio/<note-id>.adpcm > lecture1.adpcm
    lecture1.wav     ...or any 16 kHz mono 16-bit WAV instead
    lecture1.txt     what was actually said, typed by a person

and optionally, per engine you can't run from here (e.g. whisper.cpp on the
phone, or a hosted API), a directory of its transcripts with the same stems:

    whisper/lecture1.txt

USAGE
    pip install vosk        # only needed for --vosk-model
    python stt_wer.py recordings/ \\
        --vosk-model ~/models/vosk-model-small-en-us-0.15 \\
        --vosk-model ~/models/vosk-model-en-us-0.22-lgraph \\
        --hyp-dir whisper=recordings/whisper

Prints WER per recording and pooled per engine (errors summed over all words,
not an average of per-file rates, so a long lecture counts for more than a
short one).
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import wave
from pathlib import Path

from audio_playback_test import SAMPLE_RATE, decode_block

NVA1_MAGIC = 0x4E564131  # NoteAudioStore.MAGIC on the phone
UNUSABLE_WER = 0.25      # above this, Vosk small is unusable


# --- audio ---------------------------------------------------------------------

def read_kept_audio(path: Path) -> list[int]:
    """A phone-kept recording: big-endian magic, then [u16 length][ADPCM block]...
    Each block carries its own decoder state in its header, so they decode
    independently."""
    data = path.read_bytes()
    if len(data) < 4 or struct.unpack(">I", data[:4])[0] != NVA1_MAGIC:
        raise ValueError(f"{path.name}: not an NVA1 kept-audio file")
    samples: list[int] = []
    pos = 4
    while pos + 2 <= len(data):
        (length,) = struct.unpack(">H", data[pos:pos + 2])
        block = data[pos + 2:pos + 2 + length]
        pos += 2 + length
        if len(block) == length and length > 4:
            samples.extend(decode_block(block))
    return samples


def read_wav(path: Path) -> list[int]:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path.name}: need 16 kHz mono 16-bit")
        raw = w.readframes(w.getnframes())
    return list(struct.unpack(f"<{len(raw) // 2}h", raw))


def load_audio(path: Path) -> list[int]:
    return read_kept_audio(path) if path.suffix == ".adpcm" else read_wav(path)


# --- transcription -------------------------------------------------------------

def vosk_transcribe(model_dir: Path, samples: list[int]) -> str:
    """Feeds audio in 32 ms blocks like the phone's StreamingTranscriber does."""
    from vosk import KaldiRecognizer, Model, SetLogLevel

    SetLogLevel(-1)
    model = _MODELS.setdefault(model_dir, Model(str(model_dir)))
    rec = KaldiRecognizer(model, SAMPLE_RATE)
    rec.SetWords(True)
    parts = []
    pcm = struct.pack(f"<{len(samples)}h", *samples)
    step = 512 * 2
    for i in range(0, len(pcm), step):
        if rec.AcceptWaveform(pcm[i:i + step]):
            parts.append(json.loads(rec.Result()).get("text", ""))
    parts.append(json.loads(rec.FinalResult()).get("text", ""))
    return " ".join(p for p in parts if p)


_MODELS: dict = {}


# --- scoring -------------------------------------------------------------------

def words(text: str) -> list[str]:
    """Lowercase, punctuation dropped, digits kept ('Q3' -> 'q3')."""
    return re.findall(r"[a-z0-9']+", text.lower())


def edit_distance(ref: list[str], hyp: list[str]) -> int:
    """Word-level Levenshtein: substitutions + deletions + insertions."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def wer(reference: str, hypothesis: str) -> tuple[int, int]:
    ref, hyp = words(reference), words(hypothesis)
    return edit_distance(ref, hyp), len(ref)


# --- main ----------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("recordings", type=Path)
    parser.add_argument("--vosk-model", type=Path, action="append", default=[],
                        help="a Vosk model directory to run (repeatable)")
    parser.add_argument("--hyp-dir", action="append", default=[],
                        help="NAME=DIR of existing transcripts to score (repeatable)")
    args = parser.parse_args()

    refs = sorted(args.recordings.glob("*.txt"))
    if not refs:
        print(f"no reference .txt files in {args.recordings}", file=sys.stderr)
        return 2

    engines: dict[str, callable] = {}
    for model_dir in args.vosk_model:
        engines[model_dir.name] = lambda stem, m=model_dir: vosk_transcribe(m, load_audio(_audio_for(args.recordings, stem)))
    for spec in args.hyp_dir:
        name, _, directory = spec.partition("=")
        engines[name] = lambda stem, d=Path(directory): (d / f"{stem}.txt").read_text(encoding="utf-8")
    if not engines:
        print("nothing to score: pass --vosk-model and/or --hyp-dir", file=sys.stderr)
        return 2

    totals = {name: [0, 0] for name in engines}
    print(f"{'recording':<24}" + "".join(f"{name[:28]:>30}" for name in engines))
    for ref_path in refs:
        stem = ref_path.stem
        reference = ref_path.read_text(encoding="utf-8")
        row = f"{stem:<24}"
        for name, run in engines.items():
            try:
                errors, n = wer(reference, run(stem))
            except (OSError, ValueError) as e:
                row += f"{'error: ' + str(e)[:21]:>30}"
                continue
            totals[name][0] += errors
            totals[name][1] += n
            row += f"{errors / max(n, 1):>29.1%} "
        print(row)

    print()
    for name, (errors, n) in totals.items():
        rate = errors / max(n, 1)
        verdict = "UNUSABLE for captures" if rate > UNUSABLE_WER else "usable"
        print(f"{name}: pooled WER {rate:.1%} over {n} words - {verdict} (bar {UNUSABLE_WER:.0%})")
    return 0


def _audio_for(directory: Path, stem: str) -> Path:
    for ext in (".adpcm", ".wav"):
        if (directory / f"{stem}{ext}").exists():
            return directory / f"{stem}{ext}"
    raise OSError(f"no {stem}.adpcm or {stem}.wav")


if __name__ == "__main__":
    sys.exit(main())
