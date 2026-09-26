"""Offline EMG band-pass filtering and moving RMS, preserving missing samples."""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import butter, sosfiltfilt


LOW_CUT_HZ = 20.0
HIGH_CUT_HZ = 450.0
FILTER_ORDER = 4
RMS_WINDOW_S = 0.100


def _filter_finite_runs(values: np.ndarray, sos: np.ndarray) -> np.ndarray:
    """Never filter through a missing interval or invent a missing value."""
    filtered = np.full(values.shape, np.nan, dtype=np.float64)
    finite = np.isfinite(values)
    edges = np.flatnonzero(np.diff(np.r_[False, finite, False]))
    for start, stop in edges.reshape(-1, 2):
        # sosfiltfilt needs enough samples for edge padding. Tiny islands stay missing.
        if stop - start > 3 * (2 * len(sos) + 1):
            filtered[start:stop] = sosfiltfilt(sos, values[start:stop])
    return filtered


def filter_emg_csv(raw_csv: Path, bandpass_csv: Path, rms_csv: Path, rate_hz: float) -> dict:
    if not np.isfinite(rate_hz) or rate_hz <= 2 * HIGH_CUT_HZ:
        raise ValueError("EMG sampling rate must exceed twice the 450 Hz high cutoff")
    raw = pd.read_csv(raw_csv)
    names = list(raw.columns[1:])
    if raw.columns[0] != "time_s" or not names:
        raise ValueError("Raw EMG CSV needs time_s and muscle channels")
    sos = butter(
        FILTER_ORDER, [LOW_CUT_HZ, HIGH_CUT_HZ], btype="bandpass", fs=rate_hz, output="sos"
    )
    window_samples = round(RMS_WINDOW_S * rate_hz)
    bandpass_values = np.empty((len(raw), len(names)), dtype=np.float32)
    rms_values = np.empty_like(bandpass_values)
    for index, name in enumerate(names):
        filtered = _filter_finite_runs(raw[name].to_numpy(dtype=np.float64), sos)
        squared = pd.Series(filtered * filtered)
        rms = np.sqrt(
            squared.rolling(window_samples, center=True, min_periods=window_samples).mean()
        )
        bandpass_values[:, index] = filtered
        rms_values[:, index] = rms.to_numpy(dtype=np.float32)
    time = raw["time_s"].to_numpy()
    for destination, values in ((bandpass_csv, bandpass_values), (rms_csv, rms_values)):
        destination.parent.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(values, columns=names)
        frame.insert(0, "time_s", time)
        frame.to_csv(destination, index=False, float_format="%.10g")
    return {
        "bandpass": {
            "type": "Butterworth band-pass",
            "cutoff_hz": [LOW_CUT_HZ, HIGH_CUT_HZ],
            "design_order": FILTER_ORDER,
            "application": "forward-backward SOS, zero phase; finite runs processed separately",
        },
        "rms": {
            "definition": "sqrt(mean(bandpass_V ** 2))",
            "window_s": RMS_WINDOW_S,
            "window_samples": window_samples,
            "alignment": "centered; incomplete windows are NaN",
        },
        "notch_filter": "not applied",
        "output_unit": "V",
    }
