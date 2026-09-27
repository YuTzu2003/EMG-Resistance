"""Run with: uv run python -m emg_pipeline Recording_A."""

import argparse
from pathlib import Path

from .pipeline import process_recording


def main() -> int:
    parser = argparse.ArgumentParser(description="Export and inspect one EMGworks recording")
    parser.add_argument("recording", choices=["Recording_A", "Recording_B"])
    parser.add_argument("--practice-root", type=Path, default=Path("practice"))
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--file-utility", type=Path)
    parser.add_argument("--use-exported-csv", action="store_true")
    parser.add_argument("--emg-event-s", type=float)
    parser.add_argument("--resistance-event-s", type=float)
    parser.add_argument("--no-auto-sync", action="store_true", help="Only export and plot EMG")
    args = parser.parse_args()
    try:
        result = process_recording(
            args.practice_root / args.recording,
            args.output_root,
            args.file_utility,
            args.use_exported_csv,
            args.emg_event_s,
            args.resistance_event_s,
            args.no_auto_sync,
        )
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")
    print(f"Processed {args.recording}: {result['sample_count']} EMG samples, {result['channel_count']} channels")
    print(f"Files: {args.output_root / args.recording}")
    if result["synchronization"].get("status") == "not_requested":
        print("Synchronization skipped by request.")
    else:
        print(f"Synchronized rows: {result['synchronization']['overlap_sample_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
