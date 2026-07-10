"""Modality-agnostic Kalman smoother for silent-speech feature streams.

This is the transferable core: it smooths a stream of noisy multichannel
feature vectors and does not care whether the channels are lip-landmark
coordinates (webcam) or EMG amplitudes (future). Each channel is modelled as an
independent 1-D constant-velocity system:

    state per channel:   [position, velocity]
    we measure:          position (the raw feature value)

The constant-velocity model lets the filter ride through momentary dropouts /
jitter by coasting on estimated velocity, which is exactly the behaviour we want
for both a fast-moving lip contour and a noisy EMG envelope.

Tuning:
    q (process noise)      higher  -> trusts measurements more (less smoothing)
    r (measurement noise)  higher  -> trusts the model more   (more smoothing)
"""
from __future__ import annotations

import numpy as np


class KalmanSmoother:
    def __init__(self, n_channels: int, dt: float = 1.0,
                 q: float = 1e-2, r: float = 1.0, p0: float = 1.0):
        self.D = n_channels
        self.dt = dt
        self.r = float(r)
        # Constant-velocity transition and measurement matrices (shared, 2x2/1x2).
        self.F = np.array([[1.0, dt], [0.0, 1.0]])
        self.H = np.array([[1.0, 0.0]])
        # Continuous white-noise-acceleration process covariance.
        self.Q = q * np.array([[dt**3 / 3, dt**2 / 2],
                               [dt**2 / 2, dt]])
        self._p0 = p0
        self.x = None            # (D, 2) per-channel [pos, vel]
        self.P = None            # (D, 2, 2) per-channel covariance

    def reset(self, z0: np.ndarray) -> None:
        z0 = np.asarray(z0, dtype=np.float64).reshape(self.D)
        self.x = np.zeros((self.D, 2))
        self.x[:, 0] = z0
        self.P = np.tile(np.eye(2) * self._p0, (self.D, 1, 1))

    def update(self, z: np.ndarray) -> np.ndarray:
        """Feed one raw feature vector, return the smoothed estimate (D,)."""
        z = np.asarray(z, dtype=np.float64).reshape(self.D)
        if self.x is None:
            self.reset(z)
            return z.copy()

        # --- Predict (batched over the D channels) ---
        x = self.x @ self.F.T                                   # (D, 2)
        P = np.einsum("ij,djk,lk->dil", self.F, self.P, self.F) + self.Q

        # --- Update with measurement z (position only) ---
        y = z - x[:, 0]                                         # innovation (D,)
        S = P[:, 0, 0] + self.r                                 # (D,)
        K = P[:, :, 0] / S[:, None]                             # gain (D, 2)
        x = x + K * y[:, None]
        # P = (I - K H) P ; H picks column 0
        KH = np.zeros((self.D, 2, 2))
        KH[:, :, 0] = K
        P = P - np.einsum("dij,djk->dik", KH, P)

        self.x, self.P = x, P
        return x[:, 0].copy()

    def smooth(self, sequence: np.ndarray) -> np.ndarray:
        """Offline convenience: smooth a (T, D) array, return (T, D)."""
        sequence = np.asarray(sequence, dtype=np.float64)
        self.x = self.P = None
        out = np.empty_like(sequence)
        for t in range(sequence.shape[0]):
            out[t] = self.update(sequence[t])
        return out
