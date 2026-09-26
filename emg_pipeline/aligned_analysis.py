"""Aligned EMG/Resistance figures and left/right pedal-stroke cadence."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import find_peaks

from .muscle_metrics import cycle_metrics


LEFT_EMG = "L VASTUS MEDIALIS: EMG 4"
RIGHT_EMG = "R VASTUS MEDIALIS: EMG 3"
CRANK = {"Left": "crankLeft", "Right": "CrankRight"}
GRID_STEP_S = 0.05
SMOOTH_SAMPLES = 5
MIN_PEAK_SEPARATION_S = 0.6
PROMINENCE_IQR_FRACTION = 0.5


def _binned_view(frame: pd.DataFrame, duration_s: float | None, bin_s: float) -> pd.DataFrame:
    origin = float(frame["time_s"].iloc[0])
    view = frame if duration_s is None else frame.loc[frame["time_s"] <= origin + duration_s]
    bins = np.floor((view["time_s"] - origin) / bin_s).astype(int)
    reduced = view.groupby(bins).median(numeric_only=True)
    reduced["elapsed_s"] = reduced["time_s"] - origin
    return reduced


def plot_aligned_signals(frame: pd.DataFrame, destination: Path, duration_s: float | None) -> None:
    bin_s = 0.05 if duration_s is not None else 0.2
    view = _binned_view(frame, duration_s, bin_s)
    title = "Aligned EMG RMS and signed crank signals"
    title += " (first 30 s)" if duration_s is not None else " (full overlap)"
    figure, axes = plt.subplots(4, 1, figsize=(16, 10), sharex=True)
    for axis, column, label, color in zip(
        axes,
        (LEFT_EMG, "crankLeft", RIGHT_EMG, "CrankRight"),
        ("Left VM RMS (V)", "Left crank (source unit)", "Right VM RMS (V)", "Right crank (source unit)"),
        ("#176a8a", "#bd6b1b", "#176a8a", "#bd6b1b"),
    ):
        axis.plot(view["elapsed_s"], view[column], color=color, linewidth=0.8)
        axis.set_ylabel(label)
        axis.grid(alpha=0.2)
    axes[-1].set_xlabel("Seconds since aligned overlap start")
    figure.suptitle(title + f"; {bin_s:g} s display bins")
    figure.tight_layout()
    destination.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(destination, dpi=150)
    plt.close(figure)


def detect_cadence(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One positive signed-crank peak per side per revolution, in strokes/min."""
    time = frame["time_s"].to_numpy(dtype=float)
    if not np.isfinite(time).all() or np.any(np.diff(time) <= 0):
        raise ValueError("Aligned timestamps must be finite and strictly increasing")
    grid = np.arange(time[0], time[-1], GRID_STEP_S)
    events = []
    summary = {"method": "positive signed-crank peaks; one same-side stroke per revolution",
               "grid_step_s": GRID_STEP_S, "smoothing_s": GRID_STEP_S * SMOOTH_SAMPLES,
               "minimum_peak_separation_s": MIN_PEAK_SEPARATION_S,
               "prominence": "0.5 * interquartile range of smoothed signed crank",
               "interval_rule": "keep intervals from 0.65 to 1.5 times that side's median; first interval unavailable"}
    for side, column in CRANK.items():
        values = frame[column].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{column} contains missing or nonfinite values")
        sampled = np.interp(grid, time, values)
        smooth = pd.Series(sampled).rolling(SMOOTH_SAMPLES, center=True, min_periods=1).mean().to_numpy()
        iqr = float(np.percentile(smooth, 75) - np.percentile(smooth, 25))
        if iqr <= 0:
            raise ValueError(f"{column} has no variation for cadence detection")
        indices, _ = find_peaks(
            smooth,
            distance=round(MIN_PEAK_SEPARATION_S / GRID_STEP_S),
            prominence=PROMINENCE_IQR_FRACTION * iqr,
        )
        if len(indices) < 3:
            raise ValueError(f"{column} has too few pedal peaks")
        peak_times = grid[indices]
        intervals = np.r_[np.nan, np.diff(peak_times)]
        median_interval = float(np.median(intervals[1:]))
        valid = (intervals >= 0.65 * median_interval) & (intervals <= 1.5 * median_interval)
        strokes_per_min = np.where(valid, 60 / intervals, np.nan)
        events.append(pd.DataFrame({
            "time_s": peak_times, "side": side, "peak_crank": smooth[indices],
            "interval_s": intervals, "strokes_per_min": strokes_per_min,
            "valid_interval": valid,
        }))
        usable = strokes_per_min[np.isfinite(strokes_per_min)]
        summary[side.lower()] = {
            "detected_peak_count": int(len(indices)),
            "valid_interval_count": int(valid.sum()),
            "median_strokes_per_min": float(np.median(usable)),
            "p10_strokes_per_min": float(np.percentile(usable, 10)),
            "p90_strokes_per_min": float(np.percentile(usable, 90)),
        }
    return pd.concat(events, ignore_index=True).sort_values("time_s"), summary


def plot_cadence(events: pd.DataFrame, destination: Path) -> None:
    origin = float(events["time_s"].min())
    figure, axis = plt.subplots(figsize=(16, 5))
    for side, color in (("Left", "#176a8a"), ("Right", "#bd6b1b")):
        own = events.loc[events["side"] == side].copy()
        own["smoothed_spm"] = own["strokes_per_min"].rolling(7, center=True, min_periods=3).median()
        axis.plot(own["time_s"] - origin, own["smoothed_spm"], label=f"{side} (7-stroke median)", color=color)
    axis.set(xlabel="Seconds since first detected stroke", ylabel="Same-side strokes per minute",
             title="Left/right pedal-stroke cadence from signed crank peaks")
    axis.grid(alpha=0.2)
    axis.legend()
    figure.tight_layout()
    figure.savefig(destination, dpi=150)
    plt.close(figure)


def analyze_aligned_csv(synced_csv: Path, output_dir: Path) -> dict:
    columns = ["time_s", LEFT_EMG, RIGHT_EMG, *CRANK.values()]
    full = pd.read_csv(synced_csv)
    frame = full[columns]
    plots = output_dir / "plots"
    plot_aligned_signals(frame, plots / "aligned_detail.png", 30)
    plot_aligned_signals(frame, plots / "aligned_overview.png", None)
    events, summary = detect_cadence(frame)
    cadence_dir = output_dir / "cadence"
    cadence_dir.mkdir(parents=True, exist_ok=True)
    events.to_csv(cadence_dir / "stroke_events.csv", index=False)
    plot_cadence(events, plots / "cadence.png")
    analysis_dir = output_dir / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    muscle_channels = [name for name in full if ": EMG " in name]
    cycle_metrics(full, events, muscle_channels).to_csv(
        analysis_dir / "muscle_metrics.csv", index=False
    )
    # A compact viewing table; the full-resolution synchronized CSV remains authoritative.
    preview = full.drop(columns="Angle")
    bins = np.floor((preview["time_s"] - preview["time_s"].iloc[0]) * 10).astype(int)
    preview = preview.groupby(bins).median(numeric_only=True)
    preview.to_csv(output_dir / "sync" / "preview_10hz.csv", index=False)
    return summary
