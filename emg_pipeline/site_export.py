"""Export the compact, non-proprietary analysis views used by the static site."""

import argparse
import json
import shutil
from pathlib import Path


RECORDINGS = ("Recording_A", "Recording_B")
FILES = (
    ("sync/preview_10hz.csv", "preview_10hz.csv"),
    ("cadence/stroke_events.csv", "stroke_events.csv"),
    ("analysis/muscle_metrics.csv", "muscle_metrics.csv"),
)


def _public_metadata(source: Path) -> dict:
    metadata = json.loads(source.read_text(encoding="utf-8"))
    return {
        "recording": source.parent.name,
        "duration_s": metadata["synchronization"]["time_end_s"] - metadata["synchronization"]["time_start_s"],
        "processing": metadata["processing"],
        "synchronization": metadata["synchronization"],
        "cadence": metadata["cadence"],
    }


def export_static_data(output_root: Path, site_data_root: Path) -> None:
    """Copy only compact visualization data; never publish HPF or full-resolution EMG."""
    manifest = {"recordings": []}
    for recording in RECORDINGS:
        source = output_root / recording
        destination = site_data_root / recording
        required = [source / relative for relative, _ in FILES] + [source / "metadata.json"]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise FileNotFoundError(f"Missing analysis output for {recording}: {', '.join(missing)}")
        destination.mkdir(parents=True, exist_ok=True)
        for relative, name in FILES:
            shutil.copyfile(source / relative, destination / name)
        metadata = _public_metadata(source / "metadata.json")
        (destination / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest["recordings"].append({"id": recording, "label": recording.replace("_", " ")})
    site_data_root.mkdir(parents=True, exist_ok=True)
    (site_data_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Export compact analysis data for docs/ GitHub Pages")
    parser.add_argument("--output-root", type=Path, default=Path("output"))
    parser.add_argument("--site-data-root", type=Path, default=Path("docs/data"))
    args = parser.parse_args()
    export_static_data(args.output_root, args.site_data_root)
    print(f"Exported static-site data to {args.site_data_root}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
