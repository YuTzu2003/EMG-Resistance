import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "emg_pipeline_matplotlib"))
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.signal import butter, find_peaks, sosfiltfilt


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
        subprocess.run([str(file_utility),"-nogui","-o","CSV","-i", str(hpf)],check=True,capture_output=True,text=True,timeout=180,)
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


def sync_to_resistance(emg_csv: Path,resistance_csv: Path,destination: Path,offset_s: float,) -> dict:
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


def process_recording(recording: Path,output_root: Path,file_utility: Path | None = None,use_exported_csv: bool = False,emg_event_s: float | None = None,resistance_event_s: float | None = None,no_auto_sync: bool = False,) -> dict:
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

# HPF metadata -------------------------------------------------------------
def _xml_block(header: bytes, tag: str) -> ET.Element:
    match = re.search( rb"<" + tag.encode() + rb">.*?</" + tag.encode() + rb">",header,re.DOTALL,)
    if match is None:
        raise ValueError(f"HPF header is missing {tag}; unsupported or invalid HPF file")
    try:
        return ET.fromstring(match.group())
    except ET.ParseError as exc:
        raise ValueError(f"HPF {tag} metadata is malformed") from exc

def read_hpf_metadata(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"HPF file not found: {path}")
    with path.open("rb") as source:
        header = source.read(256 * 1024)
    recording = _xml_block(header, "HeaderInfoV1")
    channel_info = _xml_block(header, "ChannelInformationData")
    recording_date = recording.findtext("RecordingDate")
    if not recording_date:
        raise ValueError("HPF header has no RecordingDate")
    channels = []
    for item in channel_info.findall("ChannelInformation"):
        name = item.findtext("Name")
        unit = item.findtext("Unit")
        rate_text = item.findtext("PerChannelSampleRate")
        if not name or not unit or not rate_text:
            raise ValueError("HPF channel is missing name, unit, or sampling rate")
        try:
            rate = float(rate_text)
        except ValueError as exc:
            raise ValueError(f"Invalid sampling rate for {name}: {rate_text}") from exc
        if not 0 < rate < float("inf"):
            raise ValueError(f"Invalid sampling rate for {name}: {rate_text}")
        channels.append({
            "name": name,
            "unit": unit,
            "sampling_rate_hz": rate,
            "start_time_in_hpf": item.findtext("StartTime"),
        })
    if not channels or len({item["name"] for item in channels}) != len(channels):
        raise ValueError("HPF has no channels or has duplicate channel names")
    return {
        "recording_date_in_hpf": recording_date,
        "channel_count": len(channels),
        "channels": channels,
    }


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
    sos = butter(FILTER_ORDER, [LOW_CUT_HZ, HIGH_CUT_HZ], btype="bandpass", fs=rate_hz, output="sos")
    window_samples = round(RMS_WINDOW_S * rate_hz)
    bandpass_values = np.empty((len(raw), len(names)), dtype=np.float32)
    rms_values = np.empty_like(bandpass_values)
    for index, name in enumerate(names):
        filtered = _filter_finite_runs(raw[name].to_numpy(dtype=np.float64), sos)
        squared = pd.Series(filtered * filtered)
        rms = np.sqrt(squared.rolling(window_samples, center=True, min_periods=window_samples).mean())
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

# Muscle metrics -----------------------------------------------------------
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


def activation_at_time(aligned: pd.DataFrame,channels: list[str],elapsed_s: float,) -> tuple[pd.Series, pd.DataFrame]:
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
        entries.append({
            "muscle": name,
            "rms_uv": value * 1e6,
            "within_channel_percentile": percentile,
        })
    return row, pd.DataFrame(entries)


# Automatic synchronization -----------------------------------------------

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
    resistance_frame = pd.read_csv(resistance_csv, usecols=["Timestamp (s)", *RESISTANCE_CHANNELS])
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


def plot_alignment_check(emg_csv: Path,resistance_csv: Path,offset_s: float,destination: Path,alignment_source: str,) -> None:
    """Plot the first 12 shared seconds for a visual phase check."""
    emg = pd.read_csv(emg_csv, usecols=["time_s", *EMG_CHANNELS])
    resistance = pd.read_csv(resistance_csv, usecols=["Timestamp (s)", *RESISTANCE_CHANNELS])
    first = max(emg["time_s"].iloc[0] + offset_s, resistance["Timestamp (s)"].iloc[0])
    last = first + 12
    figure, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)
    for axis, side, emg_name, resistance_name in zip(
        axes, ("Right", "Left"), EMG_CHANNELS, RESISTANCE_CHANNELS
    ):
        emg_slice = emg.loc[(emg["time_s"] + offset_s).between(first, last), ["time_s", emg_name]].copy()
        resistance_slice = resistance.loc[resistance["Timestamp (s)"].between(first, last),["Timestamp (s)", resistance_name],].copy()
        emg_slice["bin"] = np.floor((emg_slice["time_s"] + offset_s - first) * 10).astype(int)
        resistance_slice["bin"] = np.floor((resistance_slice["Timestamp (s)"] - first) * 10).astype(int)
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

# Aligned analysis and cadence --------------------------------------------
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
        (
            "Left VM RMS (V)", "Left crank (source unit)",
            "Right VM RMS (V)", "Right crank (source unit)",
        ),
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
    summary = {
        "method": "positive signed-crank peaks; one same-side stroke per revolution",
        "grid_step_s": GRID_STEP_S,
        "smoothing_s": GRID_STEP_S * SMOOTH_SAMPLES,
        "minimum_peak_separation_s": MIN_PEAK_SEPARATION_S,
        "prominence": "0.5 * interquartile range of smoothed signed crank",
        "interval_rule": "keep intervals from 0.65 to 1.5 times that side's median; first interval unavailable",
    }
    for side, column in CRANK.items():
        values = frame[column].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError(f"{column} contains missing or nonfinite values")
        sampled = np.interp(grid, time, values)
        smooth = pd.Series(sampled).rolling(
            SMOOTH_SAMPLES, center=True, min_periods=1
        ).mean().to_numpy()
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
            "time_s": peak_times,
            "side": side,
            "peak_crank": smooth[indices],
            "interval_s": intervals,
            "strokes_per_min": strokes_per_min,
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
        own["smoothed_spm"] = own["strokes_per_min"].rolling(
            7, center=True, min_periods=3
        ).median()
        axis.plot(
            own["time_s"] - origin,
            own["smoothed_spm"],
            label=f"{side} (7-stroke median)",
            color=color,
        )
    axis.set(
        xlabel="Seconds since first detected stroke",
        ylabel="Same-side strokes per minute",
        title="Left/right pedal-stroke cadence from signed crank peaks",
    )
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
    preview = full.drop(columns="Angle")
    bins = np.floor((preview["time_s"] - preview["time_s"].iloc[0]) * 10).astype(int)
    preview = preview.groupby(bins).median(numeric_only=True)
    preview.to_csv(output_dir / "sync" / "preview_10hz.csv", index=False)
    return summary