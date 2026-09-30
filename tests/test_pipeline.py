import json
from pathlib import Path
import tempfile
import unittest

import numpy as np
import pandas as pd

from emg_pipeline.auto_sync import estimate_offset
from emg_pipeline.aligned_analysis import detect_cadence
from emg_pipeline.comparison import build_comparison_summary, confirm_comparison_draft, write_comparison_draft
from emg_pipeline.config import AppConfig
from emg_pipeline.filtering import filter_emg_csv
from emg_pipeline.muscle_metrics import activation_at_time, cycle_metrics
from emg_pipeline.hpf import read_hpf_metadata
from emg_pipeline.pipeline import normalize_emg_csv, sync_to_resistance


class PipelineTests(unittest.TestCase):
    def setUp(self):
        Path("output").mkdir(exist_ok=True)

    def test_hpf_metadata_and_missing_fields(self):
        with tempfile.TemporaryDirectory(dir="output") as directory:
            path = Path(directory) / "sample.hpf"
            path.write_bytes(
                b"binary prefix"
                b"<HeaderInfoV1><RecordingDate>2026/09/18 17:38:18</RecordingDate></HeaderInfoV1>"
                b"<ChannelInformationData><ChannelInformation><Name>Left EMG</Name>"
                b"<Unit>V</Unit><PerChannelSampleRate>2</PerChannelSampleRate>"
                b"</ChannelInformation></ChannelInformationData>"
            )
            metadata = read_hpf_metadata(path)
            self.assertEqual(metadata["channel_count"], 1)
            self.assertEqual(metadata["channels"][0]["sampling_rate_hz"], 2)
            path.write_bytes(b"not an HPF")
            with self.assertRaisesRegex(ValueError, "missing HeaderInfoV1"):
                read_hpf_metadata(path)

    def test_exported_csv_keeps_channel_gaps_and_monotonic_time(self):
        metadata = {
            "channels": [
                {"name": "Right EMG", "unit": "V", "sampling_rate_hz": 2},
                {"name": "Left EMG", "unit": "V", "sampling_rate_hz": 2},
            ]
        }
        with tempfile.TemporaryDirectory(dir="output") as directory:
            source = Path(directory) / "official.csv"
            destination = Path(directory) / "emg.csv"
            source.write_text(
                'X[s],Right EMG,X[s],Left EMG\n'
                '0,1,0,2\n'
                '0.5,3,,0\n'
                '1,5,1,6\n'
                ',0,,0\n',
                encoding="utf-8",
            )
            summary, _ = normalize_emg_csv(source, destination, metadata)
            frame = pd.read_csv(destination)
            self.assertEqual(summary["sample_count"], 3)
            self.assertEqual(summary["missing_samples_by_channel"]["Left EMG"], 1)
            self.assertTrue(frame["time_s"].is_monotonic_increasing)
            self.assertTrue(np.isnan(frame["Left EMG"].iloc[1]))

    def test_event_sync_interpolates_only_overlap(self):
        with tempfile.TemporaryDirectory(dir="output") as directory:
            root = Path(directory)
            emg = root / "emg.csv"
            resistance = root / "resistance.csv"
            result = root / "synced.csv"
            pd.DataFrame({"time_s": [0, 1, 2], "Left EMG": [0, 2, 4]}).to_csv(emg, index=False)
            pd.DataFrame(
                {"Timestamp (s)": [9, 10, 10.5, 11, 12, 13], "crankLeft": [1] * 6}
            ).to_csv(resistance, index=False)
            details = sync_to_resistance(emg, resistance, result, 10)
            merged = pd.read_csv(result)
            self.assertEqual(details["overlap_sample_count"], 4)
            self.assertEqual(merged["time_s"].tolist(), [10, 10.5, 11, 12])
            self.assertEqual(merged["Left EMG"].tolist(), [0, 1, 2, 4])

    def test_bandpass_rms_and_missing_sample(self):
        rate = 2000
        t = np.arange(4000) / rate
        signal = np.sin(2 * np.pi * 100 * t) + np.sin(2 * np.pi * 10 * t)
        signal += np.sin(2 * np.pi * 600 * t)
        signal[2000:2020] = np.nan
        with tempfile.TemporaryDirectory(dir="output") as directory:
            root = Path(directory)
            raw, bandpass, rms = [root / name for name in ("raw.csv", "bandpass.csv", "rms.csv")]
            pd.DataFrame({"time_s": t, "EMG": signal}).to_csv(raw, index=False)
            details = filter_emg_csv(raw, bandpass, rms, rate)
            filtered = pd.read_csv(bandpass)["EMG"].to_numpy()
            envelope = pd.read_csv(rms)["EMG"].to_numpy()
            spectrum = np.abs(np.fft.rfft(filtered[300:1700]))
            frequencies = np.fft.rfftfreq(1400, 1 / rate)
            amplitude = lambda hz: spectrum[np.argmin(np.abs(frequencies - hz))]
            self.assertGreater(amplitude(100), 8 * amplitude(10))
            self.assertGreater(amplitude(100), 8 * amplitude(600))
            self.assertTrue(np.isnan(filtered[2000:2020]).all())
            self.assertTrue(np.isnan(envelope[2000:2020]).all())
            self.assertTrue((envelope[np.isfinite(envelope)] >= 0).all())
            self.assertEqual(details["rms"]["window_samples"], 200)

    def test_automatic_alignment_on_irregular_bilateral_activity(self):
        rng = np.random.default_rng(42)
        right = rng.uniform(0, 1, 1200)
        left = rng.uniform(0, 1, 1200)
        with tempfile.TemporaryDirectory(dir="output") as directory:
            root = Path(directory)
            emg = root / "emg.csv"
            resistance = root / "resistance.csv"
            pd.DataFrame(
                {
                    "time_s": np.arange(1200) / 10,
                    "R VASTUS MEDIALIS: EMG 3": right,
                    "L VASTUS MEDIALIS: EMG 4": left,
                }
            ).to_csv(emg, index=False)
            pd.DataFrame(
                {
                    "Timestamp (s)": 10 + np.arange(1200) / 10,
                    "CrankRight": np.r_[np.zeros(2), right[:-2]],
                    "crankLeft": np.r_[np.zeros(2), left[:-2]],
                }
            ).to_csv(resistance, index=False)
            alignment = estimate_offset(emg, resistance)
            self.assertAlmostEqual(alignment["emg_to_resistance_offset_s"], 10.2, places=1)
            pd.DataFrame(
                {
                    "Timestamp (s)": 10 + np.arange(1200) / 10,
                    "CrankRight": rng.uniform(0, 1, 1200),
                    "crankLeft": rng.uniform(0, 1, 1200),
                }
            ).to_csv(resistance, index=False)
            with self.assertRaisesRegex(ValueError, "do not support one alignment"):
                estimate_offset(emg, resistance)

    def test_left_reference_requires_two_matching_muscles(self):
        rng = np.random.default_rng(123)
        right = rng.uniform(0, 1, 1200)
        left = rng.uniform(0, 1, 1200)
        with tempfile.TemporaryDirectory(dir="output") as directory:
            root = Path(directory)
            emg = root / "rms.csv"
            resistance = root / "resistance.csv"
            pd.DataFrame({
                "time_s": np.arange(1200) / 10,
                "R VASTUS MEDIALIS: EMG 3": right,
                "L VASTUS MEDIALIS: EMG 4": left,
                "R VASTUS LATERALIS: EMG 7": right,
                "L VASTUS LATERALIS: EMG 8": left,
            }).to_csv(emg, index=False)
            pd.DataFrame({
                "Timestamp (s)": 10 + np.arange(1200) / 10,
                "CrankRight": np.r_[np.zeros(5), right[:-5]],
                "crankLeft": np.r_[np.zeros(2), left[:-2]],
            }).to_csv(resistance, index=False)
            alignment = estimate_offset(emg, resistance)
            self.assertEqual(alignment["alignment_source"], "estimated_signal_phase_left_reference")
            self.assertAlmostEqual(alignment["emg_to_resistance_offset_s"], 10.2, places=1)
            self.assertAlmostEqual(alignment["right_best_lag_s"], 0.5, places=1)

    def test_signed_peaks_count_one_stroke_per_side_per_revolution(self):
        time = np.arange(0, 120, 0.01)
        frame = pd.DataFrame({
            "time_s": time,
            "crankLeft": 10 * np.sin(2 * np.pi * time),
            "CrankRight": 10 * np.sin(2 * np.pi * time + np.pi),
        })
        events, summary = detect_cadence(frame)
        self.assertTrue(118 <= summary["left"]["detected_peak_count"] <= 120)
        self.assertTrue(118 <= summary["right"]["detected_peak_count"] <= 120)
        self.assertAlmostEqual(summary["left"]["median_strokes_per_min"], 60, places=1)
        self.assertAlmostEqual(summary["right"]["median_strokes_per_min"], 60, places=1)
        self.assertEqual(set(events["side"]), {"Left", "Right"})

    def test_cycle_consistency_describes_pattern_not_absolute_strength(self):
        rng = np.random.default_rng(5)
        time = np.arange(0, 120, 0.01)
        aligned = pd.DataFrame({
            "time_s": time,
            "L TEST": 1e-6 * (2 + np.sin(2 * np.pi * time)),
            "R TEST": 20e-6 * rng.uniform(0, 1, len(time)),
        })
        peaks = np.arange(1, 119, dtype=float)
        strokes = pd.DataFrame({
            "time_s": np.r_[peaks, peaks + 0.5],
            "side": ["Left"] * len(peaks) + ["Right"] * len(peaks),
            "valid_interval": [False] + [True] * (len(peaks) - 1)
                              + [False] + [True] * (len(peaks) - 1),
        })
        result = cycle_metrics(aligned, strokes, ["L TEST", "R TEST"]).set_index("channel")
        self.assertGreater(result.loc["L TEST", "cycle_consistency"], 0.95)
        self.assertLess(result.loc["R TEST", "cycle_consistency"], 0.3)
        self.assertLess(result.loc["L TEST", "median_rms_uv"],
                        result.loc["R TEST", "median_rms_uv"])
        row, instant = activation_at_time(aligned, ["L TEST", "R TEST"], 10.0)
        self.assertAlmostEqual(row["time_s"], 10.0)
        self.assertEqual(len(instant), 2)

    def test_comparison_creates_structured_draft_and_requires_confirmation(self):
        with tempfile.TemporaryDirectory(dir="output") as directory:
            root = Path(directory)
            channels = ["L TEST: EMG 1", "R TEST: EMG 2"]
            for name, scale in (("baseline", 1.0), ("after", 1.2)):
                recording = root / name
                (recording / "muscle_analysis").mkdir(parents=True)
                (recording / "synchronized_data").mkdir()
                (recording / "pedaling_cadence").mkdir()
                (recording / "recording_metadata.json").write_text(json.dumps({
                    "duration_s": 10,
                    "processing": {"bandpass": {"cutoff_hz": [20, 450]}, "rms": {"window_s": 0.1}},
                    "synchronization": {"left_correlation": 0.8, "right_correlation": 0.7},
                }))
                pd.DataFrame({
                    "channel": channels, "side": ["Left", "Right"],
                    "median_rms_uv": [10 * scale, 20 * scale],
                    "p90_rms_uv": [15 * scale, 30 * scale],
                    "cycle_consistency": [0.7, 0.8], "missing_percent": [0, 0],
                }).to_csv(recording / "muscle_analysis" / "muscle_activation_metrics.csv", index=False)
                pd.DataFrame({"time_s": [0, 5, 10], "crankLeft": [1, 2, 3], "CrankRight": [2, 3, 4]}).to_csv(
                    recording / "synchronized_data" / "frontend_preview_10hz.csv", index=False)
                pd.DataFrame({"time_s": [1, 6], "side": ["Left", "Right"], "strokes_per_min": [60, 62]}).to_csv(
                    recording / "pedaling_cadence" / "pedal_stroke_events.csv", index=False)
            summary = build_comparison_summary(root / "baseline", root / "after", side="Left")
            entry = summary["muscles"][0]["comparison"]
            self.assertEqual(entry["median_rms_uv"]["percent_change"], 20.0)
            self.assertEqual(entry["p90_rms_uv"]["percent_change"], 20.0)
            self.assertEqual(summary["settings"]["side"], "Left")
            self.assertIn("no cross-recording time alignment", summary["settings"]["analysis_scope"])
            paths = write_comparison_draft(root / "comparison", summary, AppConfig())
            self.assertTrue(paths["draft"].is_file())
            self.assertFalse((root / "comparison" / "comparison_report.md").exists())
            report = confirm_comparison_draft(root / "comparison")
            self.assertTrue(report.is_file())


if __name__ == "__main__":
    unittest.main()
