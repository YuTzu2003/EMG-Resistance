import json
import tempfile
import unittest
from pathlib import Path

from emg_pipeline.site_export import export_static_data


class StaticSiteExportTests(unittest.TestCase):
    def test_exports_only_compact_visualization_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for recording in ("Recording_A", "Recording_B"):
                source = root / "output" / recording
                for relative in ("sync", "cadence", "analysis"):
                    (source / relative).mkdir(parents=True, exist_ok=True)
                (source / "sync" / "preview_10hz.csv").write_text("time_s,crankLeft\n0,1\n")
                (source / "cadence" / "stroke_events.csv").write_text("time_s,side\n0,Left\n")
                (source / "analysis" / "muscle_metrics.csv").write_text("channel\nL TEST: EMG 1\n")
                (source / "metadata.json").write_text(json.dumps({
                    "processing": {"rms": {"window_s": 0.1}},
                    "synchronization": {"time_start_s": 2.0, "time_end_s": 10.0},
                    "cadence": {"left": {}, "right": {}},
                    "source_hpf": "private.hpf",
                }))
            site_data = root / "docs" / "data"
            export_static_data(root / "output", site_data)

            manifest = json.loads((site_data / "manifest.json").read_text())
            self.assertEqual(2, len(manifest["recordings"]))
            public_metadata = json.loads((site_data / "Recording_A" / "metadata.json").read_text())
            self.assertEqual(8.0, public_metadata["duration_s"])
            self.assertNotIn("source_hpf", public_metadata)
            self.assertFalse((site_data / "Recording_A" / "rms_resistance.csv").exists())

