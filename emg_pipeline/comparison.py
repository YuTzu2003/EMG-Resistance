"""Structured comparisons of already processed EMG recordings.

The comparison layer never reads HPF files or asks an LLM to calculate a
number.  It consumes the compact, deterministic outputs created by the normal
pipeline, which makes a comparison reproducible and safe to review.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path

import pandas as pd

from .client import LLMError, generate_text
from .config import AppConfig


PROMPT_VERSION = "cycling-emg-comparison-v1"
METRICS_FILE = "muscle_analysis/muscle_activation_metrics.csv"
PREVIEW_FILE = "synchronized_data/frontend_preview_10hz.csv"
CADENCE_FILE = "pedaling_cadence/pedal_stroke_events.csv"
METADATA_FILE = "recording_metadata.json"


def _read_recording(path: Path) -> dict:
    path = path.expanduser().resolve()
    required = {
        "metadata": path / METADATA_FILE,
        "metrics": path / METRICS_FILE,
        "preview": path / PREVIEW_FILE,
        "cadence": path / CADENCE_FILE,
    }
    missing = [str(item) for item in required.values() if not item.is_file()]
    if missing:
        raise FileNotFoundError("Missing comparison input: " + ", ".join(missing))
    metadata = json.loads(required["metadata"].read_text(encoding="utf-8"))
    metrics = pd.read_csv(required["metrics"])
    preview = pd.read_csv(required["preview"])
    cadence = pd.read_csv(required["cadence"])
    expected = {"channel", "side", "median_rms_uv", "p90_rms_uv", "cycle_consistency", "missing_percent"}
    absent = sorted(expected.difference(metrics.columns))
    if absent:
        raise ValueError(f"{required['metrics']} is missing columns: {', '.join(absent)}")
    if "time_s" not in preview:
        raise ValueError(f"{required['preview']} needs a time_s column")
    return {"path": path, "name": path.name, "metadata": metadata, "metrics": metrics,
            "preview": preview, "cadence": cadence}


def _percentage_change(baseline: float, compared: float) -> float | None:
    if not math.isfinite(baseline) or not math.isfinite(compared) or baseline == 0:
        return None
    return round((compared - baseline) / abs(baseline) * 100, 3)


def _change(baseline: float, compared: float) -> dict:
    return {
        "baseline": round(baseline, 6) if math.isfinite(baseline) else None,
        "comparison": round(compared, 6) if math.isfinite(compared) else None,
        "difference": round(compared - baseline, 6) if math.isfinite(baseline) and math.isfinite(compared) else None,
        "percent_change": _percentage_change(baseline, compared),
    }


def _cadence_value(recording: dict, side: str) -> float:
    cadence = recording["cadence"]
    if cadence.empty or not {"time_s", "side", "strokes_per_min"}.issubset(cadence.columns):
        return float("nan")
    selected = cadence.loc[cadence["side"] == side]
    values = pd.to_numeric(selected["strokes_per_min"], errors="coerce").dropna()
    return float(values.median()) if not values.empty else float("nan")


def _resistance_value(recording: dict, side: str) -> float:
    column = "crankLeft" if side == "Left" else "CrankRight"
    if column not in recording["preview"]:
        return float("nan")
    values = pd.to_numeric(recording["preview"][column], errors="coerce").abs().dropna()
    return float(values.median()) if not values.empty else float("nan")


def _resistance_stats(recording: dict, side: str) -> dict:
    column = "crankLeft" if side == "Left" else "CrankRight"
    if column not in recording["preview"]:
        return {name: None for name in ("min", "median", "mean", "max")}
    values = pd.to_numeric(recording["preview"][column], errors="coerce").abs().dropna()
    if values.empty:
        return {name: None for name in ("min", "median", "mean", "max")}
    return {"min": round(float(values.min()), 6), "median": round(float(values.median()), 6),
            "mean": round(float(values.mean()), 6), "max": round(float(values.max()), 6)}


def _quality(recordings: list[dict]) -> dict:
    baseline = recordings[0]
    fields = ("processing.bandpass.cutoff_hz", "processing.rms.window_s")

    def value(recording: dict, field: str):
        item = recording["metadata"]
        for part in field.split("."):
            item = item.get(part) if isinstance(item, dict) else None
        return item

    warnings = []
    baseline_rate = (baseline["metadata"].get("channels") or [{}])[0].get("sampling_rate_hz")
    for candidate in recordings[1:]:
        for field in fields:
            if value(baseline, field) != value(candidate, field):
                warnings.append({"recording": candidate["name"], "field": field,
                                 "baseline": value(baseline, field), "comparison": value(candidate, field)})
        missing = sorted(set(baseline["metrics"]["channel"]).difference(candidate["metrics"]["channel"]))
        if missing:
            warnings.append({"recording": candidate["name"], "field": "muscle_channels",
                             "message": "Missing channels: " + ", ".join(missing)})
        candidate_rate = (candidate["metadata"].get("channels") or [{}])[0].get("sampling_rate_hz")
        if baseline_rate != candidate_rate:
            warnings.append({"recording": candidate["name"], "field": "sampling_rate_hz",
                             "baseline": baseline_rate, "comparison": candidate_rate})
    for recording in recordings:
        for field in ("electrode_position", "resistance_setting"):
            if field not in recording["metadata"]:
                warnings.append({"recording": recording["name"], "field": field,
                                 "message": "Not recorded; this condition cannot be assessed"})
    records = []
    for recording in recordings:
        processing = recording["metadata"].get("processing", {})
        records.append({
            "recording": recording["name"],
            "duration_s": round(float(recording["metadata"].get("duration_s", 0)), 3),
            "bandpass_hz": processing.get("bandpass", {}).get("cutoff_hz"),
            "rms_window_s": processing.get("rms", {}).get("window_s"),
            "sampling_rate_hz": (recording["metadata"].get("channels") or [{}])[0].get("sampling_rate_hz"),
            "channel_count": int(len(recording["metrics"])),
            "missing_percent_max": round(float(recording["metrics"]["missing_percent"].max()), 4),
        })
    return {"status": "warning" if warnings else "comparable", "warnings": warnings, "records": records}


def build_comparison_summary(
    baseline_output: Path,
    comparison_output: Path,
    muscles: list[str] | None = None,
    side: str = "Both",
) -> dict:
    """Calculate all comparison values before any narrative generation."""
    if side not in {"Both", "Left", "Right"}:
        raise ValueError("side must be Both, Left, or Right")
    recordings = [_read_recording(baseline_output), _read_recording(comparison_output)]
    names = [recording["name"] for recording in recordings]
    if len(set(names)) != len(names):
        raise ValueError("Baseline and comparison recordings must be different")
    baseline = recordings[0]
    allowed_sides = {"Left", "Right"} if side == "Both" else {side}
    baseline_metrics = baseline["metrics"].loc[baseline["metrics"]["side"].isin(allowed_sides)].copy()
    if muscles:
        baseline_metrics = baseline_metrics.loc[baseline_metrics["channel"].isin(muscles)]
    if baseline_metrics.empty:
        raise ValueError("No baseline muscles match the requested selection")

    muscle_results = []
    candidate = recordings[1]
    for row in baseline_metrics.sort_values(["side", "channel"]).itertuples():
        match = candidate["metrics"].loc[candidate["metrics"]["channel"] == row.channel]
        comparison = {"recording": candidate["name"], "available": False} if match.empty else {
            "recording": candidate["name"], "available": True,
            "median_rms_uv": _change(float(row.median_rms_uv), float(match.iloc[0].median_rms_uv)),
            "p90_rms_uv": _change(float(row.p90_rms_uv), float(match.iloc[0].p90_rms_uv)),
            "cycle_consistency": _change(float(row.cycle_consistency), float(match.iloc[0].cycle_consistency)),
            "missing_percent": _change(float(row.missing_percent), float(match.iloc[0].missing_percent)),
        }
        muscle_results.append({"channel": row.channel, "side": row.side, "comparison": comparison})

    side_results = []
    for current_side in sorted(allowed_sides):
        side_results.append({"side": current_side, "comparison": {
            "recording": candidate["name"],
            "cadence_strokes_per_min": _change(_cadence_value(baseline, current_side), _cadence_value(candidate, current_side)),
            "median_abs_resistance": _change(_resistance_value(baseline, current_side), _resistance_value(candidate, current_side)),
            "resistance_stats": {"baseline": _resistance_stats(baseline, current_side),
                                 "comparison": _resistance_stats(candidate, current_side)},
        }})

    return {
        "schema_version": "1.0",
        "calculated_by": "emg_pipeline Python comparison; LLM did not calculate these values",
        "settings": {"baseline": baseline["name"], "comparison": names[1], "side": side,
                     "muscles": list(baseline_metrics["channel"]),
                     "analysis_scope": "whole recording summary; no cross-recording time alignment"},
        "muscles": muscle_results,
        "side_metrics": side_results,
        "quality": _quality(recordings),
    }


def _draft_markdown(summary: dict) -> str:
    settings = summary["settings"]
    quality = summary["quality"]
    lines = [
        "# EMG comparison draft", "",
        f"Baseline: **{settings['baseline']}**", f"Compared: **{settings['comparison']}**", "",
        "## Calculated highlights", "",
    ]
    for muscle in summary["muscles"][:3]:
        comparison = muscle["comparison"]
        if comparison.get("available"):
            rms = comparison["median_rms_uv"]["percent_change"]
            consistency = comparison["cycle_consistency"]["difference"]
            lines.append(f"- {muscle['channel']} vs {comparison['recording']}: RMS {rms if rms is not None else 'N/A'}%; consistency change {consistency if consistency is not None else 'N/A'}.")
    if quality["warnings"]:
        lines.extend(["", "## Review before interpreting", ""])
        lines.extend(f"- {warning['recording']}: {warning.get('field')} differs or is unavailable." for warning in quality["warnings"])
    return "\n".join(lines) + "\n"


def write_comparison_draft(output: Path, summary: dict, config: AppConfig) -> dict:
    """Persist a reviewable draft.  A separate confirmation step creates the final report."""
    output = output.expanduser().resolve()
    if (output / "comparison_report.md").is_file():
        raise FileExistsError("Comparison is already confirmed; choose a new --output folder to create another draft")
    output.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    narrative = _draft_markdown(summary)
    trace = {"prompt_version": PROMPT_VERSION, "generated_at": generated_at,
             "data_scope": "structured_comparison_summary", "confirmation": "draft",
             "llm_status": "disabled", "provider": "none", "model": ""}
    if config.llm_enabled:
        system_prompt = (
            "Write concise Traditional Chinese performance feedback for an athlete. "
            "Use only the supplied calculated comparison JSON. Focus on changes, improvements, "
            "items to monitor, and practical next steps. Do not invent values or generic disclaimers."
        )
        try:
            response = generate_text(config, system_prompt, json.dumps(summary, ensure_ascii=False))
            narrative = response.content
            trace.update({"llm_status": "success", "provider": response.provider,
                          "model": response.model, "endpoint": response.endpoint})
        except LLMError as exc:
            trace.update({"llm_status": "fallback", "llm_error": str(exc)})
    summary["llm_narrative"] = {"status": trace["llm_status"], "text": narrative}
    paths = {"summary": output / "comparison_summary.json", "draft": output / "comparison_draft.md",
             "trace": output / "comparison_trace.json"}
    paths["summary"].write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    paths["draft"].write_text(narrative.rstrip() + "\n", encoding="utf-8")
    paths["trace"].write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return paths


def confirm_comparison_draft(output: Path, text_file: Path | None = None) -> Path:
    """Create the final report only after the reviewer approves or edits the draft."""
    output = output.expanduser().resolve()
    draft = output / "comparison_draft.md"
    trace_path = output / "comparison_trace.json"
    if not draft.is_file() or not trace_path.is_file():
        raise FileNotFoundError("comparison_draft.md and comparison_trace.json are required before confirmation")
    text = text_file.expanduser().read_text(encoding="utf-8") if text_file else draft.read_text(encoding="utf-8")
    if not text.strip():
        raise ValueError("Approved comparison text cannot be empty")
    report = output / "comparison_report.md"
    report.write_text(text.rstrip() + "\n", encoding="utf-8")
    trace = json.loads(trace_path.read_text(encoding="utf-8"))
    trace.update({"confirmation": "approved", "confirmed_at": datetime.now(timezone.utc).isoformat(),
                  "approved_text_source": str(text_file) if text_file else "comparison_draft.md"})
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report
