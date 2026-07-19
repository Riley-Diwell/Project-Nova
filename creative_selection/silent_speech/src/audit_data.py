"""Quick quality audit of recorded utterances in the HDF5 dataset store."""
from collections import defaultdict

import numpy as np

import dataset_store


def audit():
    samples = dataset_store.load()
    if not samples:
        print("Dataset store is empty.")
        return
    by_label = defaultdict(list)
    for s in samples:
        by_label[s["label"]].append(s["raw"].shape[0])
    print(f"{len(samples)} utterances, {len(by_label)} labels\n")
    print(f"{'label':<12}{'n':>4}{'min_T':>7}{'max_T':>7}{'mean_T':>8}")
    for label in sorted(by_label):
        Ts = by_label[label]
        print(f"{label:<12}{len(Ts):>4}{min(Ts):>7}{max(Ts):>7}"
              f"{np.mean(Ts):>8.1f}")
    dims = {s["raw"].shape[1] for s in samples}
    print(f"\nFeature dims present: {dims} (expect one value = 80)")


if __name__ == "__main__":
    audit()
