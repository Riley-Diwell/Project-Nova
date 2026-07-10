"""Ongoing CTC trainer: continuously retrains on the growing dataset store.

Run this ALONGSIDE the continuous app. Each cycle it loads the current data,
trains the phoneme CTC model for a batch of epochs (warm-started from the last
checkpoint), evaluates held-out PER/word-accuracy, and atomically writes the
checkpoint. The app hot-reloads that checkpoint, so silent-mode decoding keeps
improving as you collect more speech -- no restart, no manual --train.

    # terminal 1:  collect + decode
    python continuous.py
    # terminal 2:  keep training on the fresh data
    python train_daemon.py

Notes:
  * Separate process on purpose: training's CPU load stays off the app's render
    loop.
  * Warm-started continued training; saves only when validation PER improves, to
    avoid pushing a worse model to the app.
  * Needs enough data to be meaningful; it idles until MIN_SAMPLES exist.
"""
from __future__ import annotations

import os
import time

import torch
import torch.nn as nn
from torch.utils.data import DataLoader

import dataset_store
import phoneme_model as pm

CYCLE_EPOCHS = 25          # epochs of training per cycle
MIN_SAMPLES = 20           # don't train until at least this many utterances
POLL_SEC = 4.0             # wait between cycles / while idle


def main():
    model = pm.CTCNet()
    if os.path.exists(pm.MODEL):
        try:
            model.load_state_dict(torch.load(pm.MODEL, map_location="cpu")["state"])
            print("resumed from existing checkpoint")
        except Exception as e:
            print(f"could not resume ({e}); starting fresh")
    opt = torch.optim.Adam(model.parameters(), lr=1e-3)
    ctc = nn.CTCLoss(blank=0, zero_infinity=True)
    best_per = float("inf")
    last_n = -1

    print(f"trainer daemon up. min {MIN_SAMPLES} samples, {CYCLE_EPOCHS} ep/cycle.")
    while True:
        n = dataset_store.count()
        if n < MIN_SAMPLES:
            print(f"waiting for data ({n}/{MIN_SAMPLES}) ...", flush=True)
            time.sleep(POLL_SEC)
            continue

        tr, val = pm.load_splits()
        if len(tr) == 0:
            time.sleep(POLL_SEC)
            continue
        loader = DataLoader(tr, batch_size=16, shuffle=True,
                            collate_fn=pm.collate)
        model.train()
        tot = 0.0
        for _ in range(CYCLE_EPOCHS):
            for X, xl, targets, tl, _ in loader:
                logp = model(X).transpose(0, 1)
                loss = ctc(logp, targets, xl, tl)
                opt.zero_grad()
                loss.backward()
                opt.step()
                tot += loss.item()
        per, wacc, sil = pm.evaluate(model, val)
        improved = per < best_per
        sil_s = "n/a" if sil != sil else f"{sil:.0%}"      # NaN => no silent val
        msg = (f"[{time.strftime('%H:%M:%S')}] n={n} "
               f"loss {tot/(CYCLE_EPOCHS*max(len(loader),1)):.3f} "
               f"val PER {per:.2f} word-acc {wacc:.0%} silent-acc {sil_s}")
        if improved:
            best_per = per
            os.makedirs(os.path.dirname(pm.MODEL), exist_ok=True)
            tmp = pm.MODEL + ".tmp"
            torch.save({"state": model.state_dict(),
                        "phonemes": pm.PHONEMES}, tmp)
            os.replace(tmp, pm.MODEL)      # atomic swap the app can hot-reload
            msg += "  -> saved (improved)"
        print(msg, flush=True)
        last_n = n
        # If no new data has arrived, idle a bit longer to avoid overfitting.
        if dataset_store.count() == last_n:
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
