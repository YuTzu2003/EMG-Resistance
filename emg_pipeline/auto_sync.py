"""Estimate a phase alignment from bilateral muscle and crank patterns."""

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


EMG_CHANNELS = ("R VASTUS MEDIALIS: EMG 3", "L VASTUS MEDIALIS: EMG 4")
CHECK_CHANNELS = ("R VASTUS LATERALIS: EMG 7", "L VASTUS LATERALIS: EMG 8")
RESISTANCE_CHANNELS = ("CrankRight", "crankLeft")
SAMPLES_PER_SECOND = 10
MAX_SHIFT_S = 5


def _binned_activity(frame: pd.DataFrame, time_column: str, columns: tuple[str, str]) -> np.ndarray:
    time = frame[time_column].to_numpy(dtype=float)
    if not np.isfinite(time).all() or np.any(np.diff(time) <= 0):
        raise ValueError(f"{time_column} must be finite and strictly increasing")
    bins = np.floor((time - time[0]) * SAMPLES_PER_SECOND + 1e-8).astype(int)
    data = frame[list(columns)].abs().groupby(bins).mean()
    if len(data) < 900:
        raise ValueError("At least 90 seconds of EMG and Resistance are needed for automatic alignment")
    data = data.rolling(5, center=True, min_periods=1).mean()
    data = data - data.rolling(200, center=True, min_periods=1).mean()
    return data.to_numpy()


def _scores(emg: np.ndarray, resistance: np.ndarray, start: int, stop: int) -> np.ndarray:
    scores = np.full((2, 2 * MAX_SHIFT_S * SAMPLES_PER_SECOND + 1), np.nan)
    for position, shift in enumerate(range(-50, 51)):
        indices = np.arange(max(start, -shift), min(stop, len(emg), len(resistance) - shift))
        if len(indices) < 300:
            continue
        for side in range(2):
            x = emg[indices, side]
            y = resistance[indices + shift, side]
            finite = np.isfinite(x) & np.isfinite(y)
            if finite.sum() < 300 or np.std(x[finite]) == 0 or np.std(y[finite]) == 0:
                continue
            scores[side, position] = np.corrcoef(x[finite], y[finite])[0, 1]
    return scores


def estimate_offset(emg_csv: Path, resistance_csv: Path) -> dict:
    available = set(pd.read_csv(emg_csv, nrows=0).columns)
    selected = ["time_s", *EMG_CHANNELS]
    selected += [name for name in CHECK_CHANNELS if name in available]
    emg_frame = pd.read_csv(emg_csv, usecols=selected)
    resistance_frame = pd.read_csv(
        resistance_csv, usecols=["Timestamp (s)", *RESISTANCE_CHANNELS]
    )
    emg = _binned_activity(emg_frame, "time_s", EMG_CHANNELS)
    resistance = _binned_activity(resistance_frame, "Timestamp (s)", RESISTANCE_CHANNELS)
    total = min(len(emg), len(resistance))
    full = _scores(emg, resistance, 0, total)
    if not np.isfinite(full).all():
        raise ValueError("Automatic alignment lacks enough valid bilateral signal")
    combined = full.mean(axis=0)
    best = int(np.argmax(combined))
    side_peaks = np.argmax(full, axis=1)
    bilateral = np.all(np.abs(side_peaks - best) <= 1) and np.all(full[:, best] >= 0.15)
    lateralis_scores = None
    if bilateral:
        selection = combined
        source = "estimated_signal_phase_bilateral"
        method = "bilateral Vastus Medialis RMS vs absolute crank correlation"
    else:
        # A muscle can lead its crank signal differently on each side. A strong,
        # independently corroborated left-side phase can still be reported, with
        # the right-side disagreement exposed rather than hidden.
        if not all(name in emg_frame for name in CHECK_CHANNELS):
            raise ValueError("Left and right muscle/torque patterns do not support one alignment")
        lateralis = _binned_activity(emg_frame, "time_s", CHECK_CHANNELS)
        lateralis_scores = _scores(lateralis, resistance, 0, total)
        if not np.isfinite(lateralis_scores).all():
            raise ValueError("Automatic alignment lacks enough valid Vastus Lateralis signal")
        selection = (full[1] + lateralis_scores[1]) / 2
        best = int(np.argmax(selection))
        right_peaks = np.argmax(np.vstack((full[0], lateralis_scores[0])), axis=1)
        left_peaks = np.argmax(np.vstack((full[1], lateralis_scores[1])), axis=1)
        if (
            np.any(np.abs(left_peaks - best) > 2)
            or np.any(np.abs(right_peaks - best) > 4)
            or min(full[1, best], lateralis_scores[1, best]) < 0.75
            or min(full[0, right_peaks[0]], lateralis_scores[0, right_peaks[1]]) < 0.4
        ):
            raise ValueError("Neither bilateral nor cross-checked left-side phase is reliable")
        source = "estimated_signal_phase_left_reference"
        method = "left Vastus Medialis and Lateralis RMS phase, right-side phase checked separately"
    other = selection.copy()
    other[max(0, best - 5) : best + 6] = -np.inf
    gap = float(selection[best] - np.max(other))
    if gap < 0.02:
        raise ValueError("Repeated cycles give ambiguous alignment peaks")
    thirds = []
    segment_support = []
    for part in range(3):
        segment = _scores(emg, resistance, part * total // 3, (part + 1) * total // 3)
        if not np.isfinite(segment).all():
            raise ValueError("Automatic alignment failed in a recording segment")
        if bilateral:
            segment_selection = segment.mean(axis=0)
        else:
            segment_lateralis = _scores(
                lateralis, resistance, part * total // 3, (part + 1) * total // 3
            )
            segment_selection = (segment[1] + segment_lateralis[1]) / 2
        thirds.append(int(np.argmax(segment_selection)))
        segment_support.append(float(segment_selection[best]))
    if bilateral and any(abs(index - best) > 2 for index in thirds):
        raise ValueError("Estimated alignment changes across the recording")
    if not bilateral and min(segment_support) < 0.7:
        raise ValueError("Left-side phase does not persist across the recording")

    emg_start = float(emg_frame["time_s"].iloc[0])
    resistance_start = float(resistance_frame["Timestamp (s)"].iloc[0])
    lag_s = (best - MAX_SHIFT_S * SAMPLES_PER_SECOND) / SAMPLES_PER_SECOND
    result = {
        "alignment_source": source,
        "method": method,
        "assumption": "Both recording applications were started together, within five seconds",
        "emg_envelope": "20-450 Hz band-pass then 100 ms RMS; 0.1 s bins, 0.5 s smoothing, 20 s trend removal",
        "search_range_s": [-MAX_SHIFT_S, MAX_SHIFT_S],
        "time_resolution_s": 0.1,
        "resistance_start_minus_emg_start_s": resistance_start - emg_start,
        "estimated_lag_s": lag_s,
        "emg_to_resistance_offset_s": resistance_start - emg_start + lag_s,
        "left_correlation": float(full[1, best]),
        "right_correlation": float(full[0, best]),
        "gap_to_next_cycle_peak": gap,
        "segment_lags_s": [(index - 50) / 10 for index in thirds],
        "segment_correlation_at_selected_lag": segment_support,
        "limitation": "Signal phase alignment is not a hardware clock measurement; muscle-to-force delay may remain",
    }
    if lateralis_scores is not None:
        result["left_lateralis_correlation"] = float(lateralis_scores[1, best])
        result["right_best_lag_s"] = float((np.argmax(full[0]) - 50) / 10)
        result["right_best_correlation"] = float(np.max(full[0]))
        result["right_lateralis_best_lag_s"] = float((np.argmax(lateralis_scores[0]) - 50) / 10)
        result["right_lateralis_best_correlation"] = float(np.max(lateralis_scores[0]))
        result["limitation"] += "; right-side preferred phase differs from the left reference"
    return result


def plot_alignment_check(
    emg_csv: Path,
    resistance_csv: Path,
    offset_s: float,
    destination: Path,
    alignment_source: str,
) -> None:
    """Plot the first 12 shared seconds for a visual phase check."""
    emg = pd.read_csv(emg_csv, usecols=["time_s", *EMG_CHANNELS])
    resistance = pd.read_csv(
        resistance_csv, usecols=["Timestamp (s)", *RESISTANCE_CHANNELS]
    )
    first = max(emg["time_s"].iloc[0] + offset_s, resistance["Timestamp (s)"].iloc[0])
    last = first + 12
    figure, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    for axis, side, emg_name, resistance_name in zip(
        axes, ("Right", "Left"), EMG_CHANNELS, RESISTANCE_CHANNELS
    ):
        emg_slice = emg.loc[
            (emg["time_s"] + offset_s).between(first, last), ["time_s", emg_name]
        ].copy()
        resistance_slice = resistance.loc[
            resistance["Timestamp (s)"].between(first, last),
            ["Timestamp (s)", resistance_name],
        ].copy()
        emg_slice["bin"] = np.floor((emg_slice["time_s"] + offset_s - first) * 10).astype(int)
        resistance_slice["bin"] = np.floor(
            (resistance_slice["Timestamp (s)"] - first) * 10
        ).astype(int)
        emg_envelope = emg_slice[emg_name].groupby(emg_slice["bin"]).mean()
        emg_envelope = emg_envelope.rolling(5, center=True, min_periods=1).mean()
        crank = resistance_slice[resistance_name].abs().groupby(resistance_slice["bin"]).mean()
        crank = crank.rolling(5, center=True, min_periods=1).mean()
        axis.plot(
            emg_envelope.index / 10,
            emg_envelope / max(float(emg_envelope.max()), 1e-12),
            label="EMG RMS (20-450 Hz; 100 ms)",
        )
        axis.plot(
            crank.index / 10,
            crank / max(float(crank.max()), 1e-12),
            label="Absolute crank signal",
        )
        axis.set_ylabel(f"{side} (scaled 0–1)")
        axis.legend(loc="upper right")
        axis.grid(alpha=0.2)
    axes[-1].set_xlabel("Seconds since first shared sample")
    title = "EMG RMS and Resistance phase check (amplitudes scaled separately)"
    if alignment_source == "estimated_signal_phase_left_reference":
        title += "\nLeft-reference estimate; right phase differs"
    figure.suptitle(title)
    figure.tight_layout()
    figure.savefig(destination, dpi=150)
    plt.close(figure)
