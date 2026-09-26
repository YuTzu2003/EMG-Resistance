"""Official HPF export, normalized EMG data, plotting, and event-based sync."""

import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "emg_pipeline_matplotlib"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .hpf import read_hpf_metadata
from .auto_sync import estimate_offset, plot_alignment_check
from .aligned_analysis import analyze_aligned_csv
from .filtering import filter_emg_csv


def find_file_utility(explicit: Path | None = None) -> Path:
    candidates = [explicit] if explicit else [
        Path(r"C:\Program Files (x86)\Delsys, Inc\Delsys File Utility\DelsysFileUtil.exe"),
        Path(r"C:\Program Files\Delsys, Inc\Delsys File Utility\DelsysFileUtil.exe"),
    ]
    command = shutil.which("DelsysFileUtil.exe")
    if command and not explicit:
        candidates.append(Path(command))
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate
    raise FileNotFoundError("DelsysFileUtil.exe not found; pass --file-utility PATH")


def export_hpf(hpf: Path, file_utility: Path) -> Path:
    exported = hpf.with_suffix(".csv")
    try:
        subprocess.run(
            [str(file_utility), "-nogui", "-o", "CSV", "-i", str(hpf)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Delsys File Utility failed to export {hpf}: {exc}") from exc
    if not exported.is_file() or exported.stat().st_size == 0:
        raise RuntimeError(f"Delsys File Utility produced no CSV: {exported}")
    return exported


def normalize_emg_csv(source: Path, destination: Path, metadata: dict) -> tuple[dict, tuple]:
    with source.open("r", encoding="utf-8-sig", newline="") as stream:
        header = next(csv.reader(stream), None)
    names = [item["name"] for item in metadata["channels"]]
    if not header or len(header) != 2 * len(names):
        raise ValueError("Delsys CSV does not have one time/value pair per HPF channel")
    if header[::2] != ["X[s]"] * len(names) or header[1::2] != names:
        raise ValueError("Delsys CSV time columns or channel names disagree with the HPF header")

    destination.parent.mkdir(parents=True, exist_ok=True)
    plots = []
    count = 0
    previous_time = -np.inf
    first_time = None
    last_time = None
    sample_steps = []
    missing_by_channel = np.zeros(len(names), dtype=int)
    saw_untimed_row = False
    with destination.open("w", encoding="utf-8", newline="") as target:
        for chunk in pd.read_csv(source, chunksize=100_000, dtype=float):
            values = chunk.to_numpy()
            valid = np.isfinite(values[:, 0])
            if np.any(~valid & np.isfinite(values[:, 2::2]).any(axis=1)):
                raise ValueError("Delsys CSV has channel data without a reference time")
            if saw_untimed_row and valid.any() or (valid.any() and (~valid[: np.flatnonzero(valid)[-1] + 1]).any()):
                raise ValueError("Delsys CSV has timed samples after untimed rows")
            saw_untimed_row |= (~valid).any()
            if not valid.any():
                continue
            values = values[valid]
            times = values[:, 0]
            channel_times = values[:, ::2]
            has_channel_time = np.isfinite(channel_times)
            if not np.allclose(
                channel_times[has_channel_time],
                np.broadcast_to(times[:, None], channel_times.shape)[has_channel_time],
                rtol=0,
                atol=1e-6,
            ):
                raise ValueError("Delsys CSV channel time axes disagree")
            channel_values = values[:, 1::2].copy()
            if not np.isfinite(channel_values[has_channel_time]).all():
                raise ValueError("Delsys CSV has nonnumeric channel samples")
            missing_by_channel += (~has_channel_time).sum(axis=0)
            channel_values[~has_channel_time] = np.nan
            if times[0] <= previous_time or np.any(np.diff(times) <= 0):
                raise ValueError("Delsys CSV time axis is not strictly increasing")
            previous_time = float(times[-1])
            first_time = float(times[0]) if first_time is None else first_time
            last_time = previous_time
            if len(sample_steps) < 10_000:
                sample_steps.extend(np.diff(times[: min(len(times), 10_001)]).tolist())
            normalized = pd.DataFrame(channel_values, columns=names)
            normalized.insert(0, "time_s", times)
            normalized.to_csv(target, index=False, header=count == 0)

            groups = normalized.groupby((np.arange(len(normalized)) + count) // 300)
            plots.append((groups["time_s"].mean(), groups[names].min(), groups[names].max()))
            count += len(normalized)
    if count < 2:
        raise ValueError("Delsys CSV contains too few timed EMG samples")
    observed_rate = 1 / float(np.median(sample_steps))
    rates = [item["sampling_rate_hz"] for item in metadata["channels"]]
    if any(abs(observed_rate - rate) / rate > 0.01 for rate in rates):
        raise ValueError("Delsys CSV time interval disagrees with HPF sampling rate")
    summary = {
        "sample_count": count,
        "time_start_s": first_time,
        "time_end_s": last_time,
        "observed_sampling_rate_hz": observed_rate,
        "missing_samples_by_channel": dict(zip(names, missing_by_channel.tolist())),
        "time_basis": "seconds relative to the start of the EMG export",
    }
    return summary, tuple(plots)


def plot_channels(plot_parts: tuple, metadata: dict, destination: Path, title: str) -> None:
    names = [item["name"] for item in metadata["channels"]]
    times = np.concatenate([part[0].to_numpy() for part in plot_parts])
    minimums = pd.concat([part[1] for part in plot_parts], ignore_index=True)
    maximums = pd.concat([part[2] for part in plot_parts], ignore_index=True)
    figure, axes = plt.subplots(len(names), 1, figsize=(15, 2.3 * len(names)), sharex=True)
    for index, (axis, item) in enumerate(zip(axes, metadata["channels"])):
        axis.fill_between(times, minimums.iloc[:, index], maximums.iloc[:, index], color="#175f87")
        axis.set_ylabel(f"{item['name']}\n({item['unit']})", fontsize=8)
        axis.grid(alpha=0.2)
    axes[-1].set_xlabel("EMG time (s)")
    figure.suptitle(title)
    figure.tight_layout()
    figure.savefig(destination, dpi=150)
    plt.close(figure)


def plot_csv_channels(source: Path, metadata: dict, destination: Path, title: str) -> None:
    """Plot long CSVs as per-block min/max envelopes without dropping transients."""
    parts = []
    count = 0
    names = [item["name"] for item in metadata["channels"]]
    for chunk in pd.read_csv(source, chunksize=100_000):
        groups = chunk.groupby((np.arange(len(chunk)) + count) // 300)
        parts.append((groups["time_s"].mean(), groups[names].min(), groups[names].max()))
        count += len(chunk)
    plot_channels(tuple(parts), metadata, destination, title)


def sync_to_resistance(
    emg_csv: Path,
    resistance_csv: Path,
    destination: Path,
    offset_s: float,
) -> dict:
    emg = pd.read_csv(emg_csv)
    resistance = pd.read_csv(resistance_csv)
    if "Timestamp (s)" not in resistance or len(resistance) < 2:
        raise ValueError("Resistance CSV needs a Timestamp (s) column and at least two rows")
    emg_t = emg["time_s"].to_numpy(dtype=float)
    resistance_t = resistance["Timestamp (s)"].to_numpy(dtype=float)
    if not np.isfinite(emg_t).all() or np.any(np.diff(emg_t) <= 0):
        raise ValueError("EMG time axis must be finite and strictly increasing")
    if not np.isfinite(resistance_t).all() or np.any(np.diff(resistance_t) <= 0):
        raise ValueError("Resistance time axis must be finite and strictly increasing")
    aligned_emg_t = emg_t + offset_s
    overlap = (resistance_t >= aligned_emg_t[0]) & (resistance_t <= aligned_emg_t[-1])
    if overlap.sum() < 2:
        raise ValueError("EMG and Resistance have fewer than two overlapping samples")
    result = resistance.loc[overlap].copy()
    result = result.rename(columns={"Timestamp (s)": "time_s"})
    for name in emg.columns[1:]:
        values = emg[name].to_numpy(dtype=float)
        # np.interp assumes a fully valid y-axis; preserve gaps instead of bridging them.
        result[name] = np.interp(result["time_s"], aligned_emg_t, values)
    destination.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(destination, index=False)
    return {
        "status": "aligned",
        "basis": "Resistance Timestamp (s)",
        "emg_to_resistance_offset_s": offset_s,
        "interpolation": "linear RMS interpolation at Resistance timestamps; no extrapolation; missing windows remain blank",
        "overlap_sample_count": int(len(result)),
        "time_start_s": float(result["time_s"].iloc[0]),
        "time_end_s": float(result["time_s"].iloc[-1]),
    }


def process_recording(
    recording: Path,
    output_root: Path,
    file_utility: Path | None = None,
    use_exported_csv: bool = False,
    emg_event_s: float | None = None,
    resistance_event_s: float | None = None,
    no_auto_sync: bool = False,
) -> dict:
    hpf = recording / "EMG" / "EMG_Raw.hpf"
    resistance = recording / "Resistance" / "Resistance.csv"
    metadata = read_hpf_metadata(hpf)
    exported = hpf.with_suffix(".csv") if use_exported_csv else export_hpf(hpf, find_file_utility(file_utility))
    if not exported.is_file():
        raise FileNotFoundError(f"Officially exported CSV not found: {exported}")
    output = output_root / recording.name
    output.mkdir(parents=True, exist_ok=True)
    emg_dir = output / "emg"
    plot_dir = output / "plots"
    sync_dir = output / "sync"
    for directory in (emg_dir, plot_dir, sync_dir):
        directory.mkdir(parents=True, exist_ok=True)
    (sync_dir / "rms_resistance.csv").unlink(missing_ok=True)
    (sync_dir / "preview_10hz.csv").unlink(missing_ok=True)
    (plot_dir / "alignment_check.png").unlink(missing_ok=True)
    for generated in (plot_dir / "aligned_detail.png", plot_dir / "aligned_overview.png",
                      plot_dir / "cadence.png", output / "cadence" / "stroke_events.csv",
                      output / "analysis" / "muscle_metrics.csv"):
        generated.unlink(missing_ok=True)
    emg_csv = emg_dir / "raw.csv"
    bandpass_csv = emg_dir / "bandpass.csv"
    rms_csv = emg_dir / "rms.csv"
    summary, plots = normalize_emg_csv(exported, emg_csv, metadata)
    plot_channels(plots, metadata, plot_dir / "raw_channels.png", "Raw EMG (V; min/max per 300 samples)")
    rate_hz = metadata["channels"][0]["sampling_rate_hz"]
    processing = filter_emg_csv(emg_csv, bandpass_csv, rms_csv, rate_hz)
    plot_csv_channels(
        bandpass_csv, metadata, plot_dir / "bandpass_channels.png",
        "Band-pass EMG (20-450 Hz, zero phase; V; min/max per 300 samples)",
    )
    plot_csv_channels(
        rms_csv, metadata, plot_dir / "rms_channels.png",
        "EMG RMS (100 ms centered window; V; min/max per 300 samples)",
    )
    metadata.update(summary)
    metadata["source_hpf"] = str(hpf)
    metadata["file_utility_csv"] = str(exported)
    metadata["processing"] = processing
    metadata["normalized_emg_csv"] = str(emg_csv)
    metadata["bandpass_emg_csv"] = str(bandpass_csv)
    metadata["rms_emg_csv"] = str(rms_csv)

    if (emg_event_s is None) != (resistance_event_s is None):
        raise ValueError("Both --emg-event-s and --resistance-event-s are required")
    if emg_event_s is not None:
        if not np.isfinite([emg_event_s, resistance_event_s]).all():
            raise ValueError("Synchronization event times must be finite")
        if not summary["time_start_s"] <= emg_event_s <= summary["time_end_s"]:
            raise ValueError("EMG event time lies outside the recording")
        resistance_times = pd.read_csv(resistance, usecols=["Timestamp (s)"])["Timestamp (s)"]
        if not resistance_times.iloc[0] <= resistance_event_s <= resistance_times.iloc[-1]:
            raise ValueError("Resistance event time lies outside the recording")
        alignment = {
            "alignment_source": "observed_event",
            "method": "same observed event in both recordings",
            "event_emg_time_s": emg_event_s,
            "event_resistance_time_s": resistance_event_s,
            "emg_to_resistance_offset_s": resistance_event_s - emg_event_s,
        }
    elif no_auto_sync:
        alignment = None
    else:
        try:
            alignment = estimate_offset(rms_csv, resistance)
        except ValueError as exc:
            metadata["synchronization"] = {"status": "failed", "reason": str(exc)}
            (output / "metadata.json").write_text(
                json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            raise
    if alignment is None:
        metadata["synchronization"] = {"status": "not_requested"}
    else:
        synchronization = sync_to_resistance(
            rms_csv,
            resistance,
            sync_dir / "rms_resistance.csv",
            alignment["emg_to_resistance_offset_s"],
        )
        plot_alignment_check(
            rms_csv,
            resistance,
            alignment["emg_to_resistance_offset_s"],
            plot_dir / "alignment_check.png",
            alignment["alignment_source"],
        )
        metadata["synchronization"] = {**alignment, **synchronization}
        metadata["cadence"] = analyze_aligned_csv(sync_dir / "rms_resistance.csv", output)
    (output / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return metadata
