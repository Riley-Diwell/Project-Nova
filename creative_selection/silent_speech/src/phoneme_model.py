"""Phoneme CTC model (build #2): lip-feature sequence -> phonemes -> word.

Streaming-friendly CNN + BiGRU + CTC head. Trained on the phoneme-tagged .npz
data (falls back to CMUdict for older samples that only stored a word label).
Outputs a phoneme sequence over 39 ARPAbet classes + a CTC blank; phoneme_lexicon
turns that into candidate words.

Why this replaces the word-SVM:
  * 39 dense phoneme classes instead of 251 sparse word classes.
  * Frame-synchronous emission -> supports continuous streaming prediction
    ("predict at the end of each phoneme") for silent mode.

    python phoneme_model.py --train --epochs 300
    python phoneme_model.py --eval
"""
from __future__ import annotations

import argparse
import glob
import os

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

import dataset_store
from kalman import KalmanSmoother
from phoneme_lexicon import PhonemeLexicon, WORD_SEP

BASE = os.path.dirname(__file__)
DATA = os.path.join(BASE, "..", "data")
MODEL = os.path.join(BASE, "..", "models", "phoneme_ctc.pt")

PHONEMES = ["AA", "AE", "AH", "AO", "AW", "AY", "B", "CH", "D", "DH", "EH",
            "ER", "EY", "F", "G", "HH", "IH", "IY", "JH", "K", "L", "M", "N",
            "NG", "OW", "OY", "P", "R", "S", "SH", "T", "TH", "UH", "UW", "V",
            "W", "Y", "Z", "ZH"]
# The word-separator is a normal output class so CTC learns word boundaries.
SYMBOLS = PHONEMES + [WORD_SEP]
PH2I = {p: i + 1 for i, p in enumerate(SYMBOLS)}    # 0 = CTC blank
N_CLASSES = len(SYMBOLS) + 1

_lex = None


def lex():
    global _lex
    if _lex is None:
        _lex = PhonemeLexicon()
    return _lex


def featurize(seq: np.ndarray) -> np.ndarray:
    """(T, 80) smoothed lip coords -> (T, 160) with velocity appended."""
    seq = seq.astype(np.float32)
    vel = np.diff(seq, axis=0, prepend=seq[:1])
    return np.concatenate([seq, vel], axis=1)


def phones_for(sample) -> list[str]:
    if sample.get("phonemes"):
        return sample["phonemes"]
    prn = lex().word_to_phonemes(str(sample["label"]))
    return list(prn[0]) if prn else []


class LipPhonemeDataset(Dataset):
    def __init__(self, samples):
        self.items = []
        for s in samples:
            raw = s["raw"]
            smoothed = KalmanSmoother(raw.shape[1], q=5e-3, r=0.25).smooth(raw)
            x = featurize(smoothed)
            tgt = [PH2I[p] for p in phones_for(s) if p in PH2I]
            # CTC needs at least as many input frames as target labels.
            if len(tgt) >= 1 and len(x) >= len(tgt):
                self.items.append((x, np.array(tgt, dtype=np.int64),
                                   str(s["label"]),
                                   s.get("voicing", "voiced")))

    def __len__(self):
        return len(self.items)

    def __getitem__(self, i):
        return self.items[i]


def collate(batch):
    xs, tgts, labels, _voicings = zip(*batch)
    xl = torch.tensor([len(x) for x in xs], dtype=torch.long)
    tl = torch.tensor([len(t) for t in tgts], dtype=torch.long)
    Tmax = int(xl.max())
    F = xs[0].shape[1]
    X = torch.zeros(len(xs), Tmax, F)
    for i, x in enumerate(xs):
        X[i, :len(x)] = torch.from_numpy(x)
    targets = torch.from_numpy(np.concatenate(tgts))
    return X, xl, targets, tl, labels


class CTCNet(nn.Module):
    def __init__(self, in_dim=160, hidden=128, classes=N_CLASSES):
        super().__init__()
        self.conv = nn.Sequential(
            nn.Conv1d(in_dim, 128, 5, padding=2), nn.ReLU(),
            nn.Conv1d(128, 128, 5, padding=2), nn.ReLU())
        self.gru = nn.GRU(128, hidden, num_layers=2, batch_first=True,
                          bidirectional=True, dropout=0.2)
        self.fc = nn.Linear(hidden * 2, classes)

    def forward(self, x):                 # x: (B, T, F)
        x = self.conv(x.transpose(1, 2))  # (B, 128, T)
        x, _ = self.gru(x.transpose(1, 2))
        return self.fc(x).log_softmax(-1)  # (B, T, C)


def greedy_decode(logp_row) -> list[str]:
    """(T, C) log-probs -> collapsed phoneme list (CTC greedy)."""
    idx = logp_row.argmax(-1).tolist()
    out, prev = [], 0
    for i in idx:
        if i != prev and i != 0:
            out.append(SYMBOLS[i - 1])
        prev = i
    return out


def _edit(a, b):
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return prev[-1]


def load_splits(val_frac=0.15, seed=0):
    samples = dataset_store.load()
    rng = np.random.default_rng(seed)
    idx = rng.permutation(len(samples))
    n_val = max(1, int(len(samples) * val_frac))
    val = [samples[i] for i in idx[:n_val]]
    tr = [samples[i] for i in idx[n_val:]]
    return LipPhonemeDataset(tr), LipPhonemeDataset(val)


def evaluate(model, ds):
    """Return (PER, word-acc, silent-word-acc).

    silent-word-acc is over just the voicing=="silent" val samples -- the number
    that actually matters for silent mode, tracked separately because the model
    still sees mostly voiced data (the domain gap).
    """
    model.eval()
    L = lex()
    tot_per = tot_ph = word_hit = 0
    sil_hit = sil_n = 0
    with torch.no_grad():
        for x, tgt, label, voicing in ds.items:
            logp = model(torch.from_numpy(x)[None])[0]
            pred = greedy_decode(logp)
            truth = [SYMBOLS[i - 1] for i in tgt]
            tot_per += _edit(pred, truth)
            tot_ph += len(truth)
            hit = L.decode_words(pred) == str(label).split()
            word_hit += hit
            if voicing == "silent":
                sil_n += 1
                sil_hit += hit
    per = tot_per / max(tot_ph, 1)
    wacc = word_hit / max(len(ds.items), 1)
    sil_wacc = sil_hit / sil_n if sil_n else float("nan")
    return per, wacc, sil_wacc


def train(epochs=300, batch=16, lr=1e-3):
    tr, val = load_splits()
    print(f"train {len(tr)} / val {len(val)} samples, {N_CLASSES} classes")
    loader = DataLoader(tr, batch_size=batch, shuffle=True, collate_fn=collate)
    model = CTCNet()
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    best = 1e9
    for ep in range(1, epochs + 1):
        model.train()
        tot = 0.0
        for X, xl, targets, tl, _ in loader:
            logp = model(X).transpose(0, 1)      # (T, B, C)
            loss = ctc(logp, targets, xl, tl)
            opt.zero_grad()
            loss.backward()
            opt.step()
            tot += loss.item()
        if ep % 20 == 0 or ep == 1:
            per, wacc, sil = evaluate(model, val)
            sil_s = "n/a" if sil != sil else f"{sil:.1%}"   # NaN when no silent
            print(f"epoch {ep:3d}  loss {tot/len(loader):.3f}  "
                  f"val PER {per:.2f}  val word-acc {wacc:.1%}  "
                  f"silent-acc {sil_s}")
            if per < best:
                best = per
                os.makedirs(os.path.dirname(MODEL), exist_ok=True)
                torch.save({"state": model.state_dict(),
                            "phonemes": PHONEMES}, MODEL)
    print(f"saved best model -> {MODEL}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", action="store_true")
    ap.add_argument("--eval", action="store_true")
    ap.add_argument("--epochs", type=int, default=300)
    args = ap.parse_args()
    if args.train:
        train(epochs=args.epochs)
    if args.eval:
        _, val = load_splits()
        m = CTCNet()
        m.load_state_dict(torch.load(MODEL)["state"])
        per, wacc, sil = evaluate(m, val)
        sil_s = "n/a" if sil != sil else f"{sil:.1%}"
        print(f"val PER {per:.2f}  val word-acc {wacc:.1%}  silent-acc {sil_s}")
