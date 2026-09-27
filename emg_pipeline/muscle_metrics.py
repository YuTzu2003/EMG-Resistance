"""Descriptive EMG metrics; these do not estimate muscle strength or fatigue."""

import numpy as np
import pandas as pd


PHASE_POINTS = np.linspace(0.05, 0.95, 20)


def cycle_metrics(aligned: pd.DataFrame, strokes: pd.DataFrame, channels: list[str]) -> pd.DataFrame:
    """Measure how consistently each channel's RMS waveform repeats by pedal stroke."""
    time = aligned["time_s"].to_numpy(dtype=float)
    if not np.isfinite(time).all() or np.any(np.diff(time) <= 0):
        raise ValueError("Aligned EMG timestamps must be finite and strictly increasing")
    records = []
    for name in channels:
        side = "Right" if name.startswith("R ") else "Left"
        own = strokes.loc[strokes["side"] == side].sort_values("time_s")
        peaks = own["time_s"].to_numpy(dtype=float)
        valid = own["valid_interval"].to_numpy(dtype=bool)[1:]
        starts, ends = peaks[:-1][valid], peaks[1:][valid]
        if len(starts) < 5:
            raise ValueError(f"Too few valid {side.lower()} cycles for {name}")
        phase_times = starts[:, None] + (ends - starts)[:, None] * PHASE_POINTS
        signal = aligned[name].to_numpy(dtype=float)
        cycles = np.interp(phase_times.ravel(), time, signal).reshape(-1, len(PHASE_POINTS))
        complete = np.isfinite(cycles).all(axis=1)
        cycles = cycles[complete]
        deviations = cycles - cycles.mean(axis=1, keepdims=True)
        scales = np.sqrt(np.mean(deviations**2, axis=1))
        shaped = deviations[scales > 0] / scales[scales > 0, None]
        if len(shaped) < 5:
            raise ValueError(f"Too few usable EMG cycles for {name}")
        template = np.median(shaped, axis=0)
        template -= template.mean()
        template_scale = np.sqrt(np.mean(template**2))
        consistency = float(np.median(np.mean(shaped * (template / template_scale), axis=1)))
        finite = signal[np.isfinite(signal)]
        records.append({
            "channel": name,
            "side": side,
            "median_rms_uv": float(np.median(finite) * 1e6),
            "p90_rms_uv": float(np.percentile(finite, 90) * 1e6),
            "cycle_consistency": consistency,
            "usable_cycles": int(len(shaped)),
            "missing_percent": float((~np.isfinite(signal)).mean() * 100),
        })
    return pd.DataFrame(records).sort_values("cycle_consistency", ignore_index=True)


def activation_at_time(aligned: pd.DataFrame, channels: list[str], elapsed_s: float) -> tuple[pd.Series, pd.DataFrame]:
    """Return nearest recorded row and each muscle's within-recording RMS percentile."""
    time = aligned["time_s"].to_numpy(dtype=float)
    target = time[0] + elapsed_s
    index = int(np.argmin(np.abs(time - target)))
    row = aligned.iloc[index]
    entries = []
    for name in channels:
        value = float(row[name])
        finite = aligned[name].dropna().to_numpy(dtype=float)
        percentile = float(100 * np.mean(finite <= value)) if np.isfinite(value) else np.nan
        entries.append({"muscle": name, "rms_uv": value * 1e6, "within_channel_percentile": percentile})
    return row, pd.DataFrame(entries)
