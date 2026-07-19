"""Decoder (the AI stage): lip-feature sequence -> digit + confidence.

Design notes for a tiny dataset (5 samples/class):
  * Each utterance is a variable-length (T, 80) trajectory. We resample it to a
    fixed length L so every sample is the same size, preserving the *shape* of
    the motion over time (which is what distinguishes spoken words).
  * StandardScaler -> PCA compresses the ~L*80 dims down to a handful of
    components so a classifier can generalize from very few samples.
  * SVM (RBF) with probability=True gives a calibrated per-class confidence,
    which is what the downstream LLM-validation layer will gate on.

Modality-agnostic: it consumes feature sequences, not pixels, so the same
decoder design applies when the source becomes EMG.

    python decoder.py --eval     # leave-one-out cross-validated accuracy
    python decoder.py --train    # fit on all data, save model to models/
"""
from __future__ import annotations

import argparse
import glob
import os
import warnings

import numpy as np
import joblib

# SVC(probability=True) is deprecated in sklearn 1.9 but still the simplest way
# to get calibrated per-class confidence; silence the churn for now.
warnings.filterwarnings("ignore", category=FutureWarning)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.decomposition import PCA
from sklearn.svm import SVC
from sklearn.model_selection import LeaveOneOut
from sklearn.metrics import accuracy_score, confusion_matrix

BASE_DIR = os.path.dirname(__file__)
DATA_DIR = os.path.join(BASE_DIR, "..", "data")
MODEL_PATH = os.path.join(BASE_DIR, "..", "models", "digit_decoder.joblib")

RESAMPLE_L = 12  # fixed trajectory length


def resample_seq(seq: np.ndarray, L: int = RESAMPLE_L) -> np.ndarray:
    """Resample a (T, D) sequence to (L, D) by per-channel linear interpolation."""
    T, D = seq.shape
    if T == L:
        return seq
    src = np.linspace(0.0, 1.0, T)
    dst = np.linspace(0.0, 1.0, L)
    return np.stack([np.interp(dst, src, seq[:, d]) for d in range(D)], axis=1)


def featurize(seq: np.ndarray) -> np.ndarray:
    """Turn a (T, D) utterance into a flat fixed-length feature vector.

    We append per-frame velocity (frame-to-frame delta) to the resampled
    positions. The motion is what carries word identity here — since features
    are per-frame lip-box normalized, static openness is largely removed, so the
    velocity channels roughly double LOO accuracy (46% -> 68% on the digit set).
    """
    r = resample_seq(seq)
    v = np.diff(r, axis=0, prepend=r[:1])
    return np.concatenate([r, v], axis=1).flatten()


def load_dataset(data_dir: str = DATA_DIR):
    import dataset_store
    from kalman import KalmanSmoother
    X, y = [], []
    for s in dataset_store.load():
        raw = s["raw"]
        smoothed = KalmanSmoother(raw.shape[1], q=5e-3, r=0.25).smooth(raw)
        X.append(featurize(smoothed))
        y.append(s["label"])
    if not X:
        raise RuntimeError("No data found in dataset store")
    return np.array(X), np.array(y)


def build_pipeline(n_samples: int) -> Pipeline:
    # Keep PCA comps safely below the sample count to avoid overfitting.
    n_comp = min(25, n_samples - 1)
    return Pipeline([
        ("scale", StandardScaler()),
        ("pca", PCA(n_components=n_comp, random_state=0)),
        ("svm", SVC(kernel="rbf", C=10.0, gamma="scale", probability=True,
                    random_state=0)),
    ])


def evaluate():
    X, y = load_dataset()
    print(f"Loaded {len(X)} samples, {len(set(y))} classes, "
          f"feature dim {X.shape[1]}")
    loo = LeaveOneOut()
    preds, truth = [], []
    for tr, te in loo.split(X):
        pipe = build_pipeline(len(tr))
        pipe.fit(X[tr], y[tr])
        preds.append(pipe.predict(X[te])[0])
        truth.append(y[te][0])
    acc = accuracy_score(truth, preds)
    labels = sorted(set(y))
    cm = confusion_matrix(truth, preds, labels=labels)
    print(f"\nLeave-one-out accuracy: {acc:.1%}  "
          f"(chance = {1/len(labels):.0%})\n")
    print("Confusion matrix (rows=true, cols=pred):")
    print("      " + " ".join(f"{l:>5}" for l in labels))
    for i, l in enumerate(labels):
        print(f"{l:>5} " + " ".join(f"{v:>5}" for v in cm[i]))
    return acc


def train_and_save():
    X, y = load_dataset()
    pipe = build_pipeline(len(X))
    pipe.fit(X, y)
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    joblib.dump({"pipeline": pipe, "resample_l": RESAMPLE_L,
                 "classes": sorted(set(y))}, MODEL_PATH)
    print(f"Trained on {len(X)} samples -> {MODEL_PATH}")


class DigitDecoder:
    """Load a trained model and predict (label, confidence) for a sequence."""

    def __init__(self, path: str = MODEL_PATH):
        blob = joblib.load(path)
        self.pipe = blob["pipeline"]
        self.classes = blob["classes"]

    def predict(self, seq: np.ndarray):
        x = featurize(seq)[None, :]
        proba = self.pipe.predict_proba(x)[0]
        order = np.argsort(proba)[::-1]
        ranked = [(self.pipe.classes_[i], float(proba[i])) for i in order]
        return ranked[0][0], ranked[0][1], ranked


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--train", action="store_true")
    args = ap.parse_args()
    if args.eval or not args.train:
        evaluate()
    if args.train:
        train_and_save()
