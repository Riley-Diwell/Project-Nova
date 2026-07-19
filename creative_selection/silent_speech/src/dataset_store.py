"""Single-file, space-efficient dataset store (HDF5) for utterance samples.

Replaces one-.npz-per-utterance (which stored float64 + redundant smoothed +
per-file feature_names across thousands of tiny files). Here everything lives in
one growing file:

    data/dataset.h5
      attrs: count, feature_names, modality
      /s0000000  dataset 'feat' (T, F) float32, lzf-compressed
                 attrs: label, phonemes ("TH R IY"), modality

Only RAW features are stored (float32); Kalman smoothing is applied on load, so
smoothing params can change without re-collecting. ~5-8x smaller than the npz
scheme and a single file instead of thousands.

    from dataset_store import append, load, count
    append(raw, "three", ["TH","R","IY"], feature_names=source.feature_names)
    for s in load():   # s = {"raw","label","phonemes","modality"}
        ...
"""
from __future__ import annotations

import os
import time
from collections import Counter

import numpy as np
import h5py

BASE = os.path.dirname(__file__)
STORE = os.path.join(BASE, "..", "data", "dataset.h5")


def _open(path, mode, retries=40, delay=0.05):
    """Open an HDF5 file, retrying past transient lock conflicts.

    Lets the collector (append) and the training daemon (read) share one file:
    HDF5 locks the file, so a reader/writer that briefly collides just retries.
    """
    last = None
    for _ in range(retries):
        try:
            return h5py.File(path, mode)
        except (OSError, BlockingIOError) as e:
            last = e
            time.sleep(delay)
    raise last if last else OSError(f"could not open {path}")


def append(raw, label, phonemes=(), modality="webcam_lips",
           feature_names=None, voicing="voiced", path=STORE) -> int:
    """Append one utterance. Returns its integer index.

    `voicing` is "voiced" (spoken aloud) or "silent" (mouthed) so training can
    mix both domains and silent-mode accuracy can be measured separately -- the
    voiced/silent domain gap is the core research problem.
    """
    raw = np.asarray(raw, dtype=np.float32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _open(path, "a") as f:
        n = int(f.attrs.get("count", 0))
        g = f.create_group(f"s{n:07d}")
        g.create_dataset("feat", data=raw, compression="lzf")
        g.attrs["label"] = str(label)
        g.attrs["phonemes"] = " ".join(phonemes) if len(phonemes) else ""
        g.attrs["modality"] = str(modality)
        g.attrs["voicing"] = str(voicing)
        if feature_names is not None and "feature_names" not in f.attrs:
            f.attrs["feature_names"] = np.array(list(feature_names),
                                                dtype=h5py.string_dtype())
        f.attrs["count"] = n + 1
    return n


def load(path=STORE) -> list[dict]:
    """Return all samples as dicts: {'raw','label','phonemes','modality'}."""
    out = []
    if not os.path.exists(path):
        return out
    with _open(path, "r") as f:
        for k in sorted(k for k in f.keys() if k.startswith("s")):
            g = f[k]
            ph = g.attrs.get("phonemes", "")
            out.append({
                "raw": g["feat"][:],
                "label": g.attrs.get("label", ""),
                "phonemes": ph.split() if ph else [],
                "modality": g.attrs.get("modality", ""),
                "voicing": g.attrs.get("voicing", "voiced"),
            })
    return out


def count(path=STORE) -> int:
    if not os.path.exists(path):
        return 0
    with _open(path, "r") as f:
        return int(f.attrs.get("count", 0))


def label_counts(path=STORE) -> Counter:
    return Counter(s["label"] for s in load(path))


if __name__ == "__main__":
    n = count()
    print(f"{n} samples in {STORE}")
    if n:
        c = label_counts()
        print(f"{len(c)} distinct labels; most common:")
        for lab, k in c.most_common(15):
            print(f"  {lab:<12} {k}")
