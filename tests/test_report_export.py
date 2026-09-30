import json
import tempfile
import unittest
from pathlib import Path

from emg_pipeline.report_export import _public_metadata


class ReportExportTests(unittest.TestCase):
    def test_public_metadata_keeps_comparison_conditions(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "recording_metadata.json"
            source.write_text(json.dumps({
                "channels": [{"sampling_rate_hz": 2150}],
                "processing": {"bandpass": {"cutoff_hz": [20, 450]}, "rms": {"window_s": 0.1}},
                "synchronization": {"time_start_s": 0, "time_end_s": 10},
                "cadence": {"left": {}, "right": {}},
            }), encoding="utf-8")
            result = _public_metadata(source)
            self.assertEqual(result["comparison_conditions"]["sampling_rate_hz"], 2150)
            self.assertFalse(result["comparison_conditions"]["electrode_position_recorded"])
            self.assertFalse(result["comparison_conditions"]["resistance_setting_recorded"])


if __name__ == "__main__":
    unittest.main()
