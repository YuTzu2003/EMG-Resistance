import argparse
import json
import shutil
from pathlib import Path

DATA_FILES = (
    "synchronized_data/frontend_preview_10hz.csv",
    "pedaling_cadence/pedal_stroke_events.csv",
    "muscle_analysis/muscle_activation_metrics.csv",
)
REPORT_FILES = (
    "analysis_report/analysis_summary.json",
    "analysis_report/analysis_report.md",
    "analysis_report/analysis_trace.json",
)
LEGACY_FILES = (
    "metadata.json",
    "preview_10hz.csv",
    "stroke_events.csv",
    "muscle_metrics.csv",
    "analysis_summary.json",
    "report.md",
    "trace.json",
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

def export_static_data(output_root: Path,site_data_root: Path,recordings: tuple[str, ...] | None = None,) -> None:
    output_root = output_root.expanduser().resolve()
    site_data_root = site_data_root.expanduser().resolve()
    if output_root == site_data_root:
        raise ValueError("Analysis output and frontend data folders must be different")
    incremental = recordings is not None
    if not incremental:
        recordings = tuple(
            path.name for path in sorted(output_root.iterdir())
            if path.is_dir()
            and all((path / relative).is_file() for relative in DATA_FILES)
            and (path / "recording_metadata.json").is_file()
        )
    if not recordings:
        raise FileNotFoundError(f"No complete analysis output found under {output_root}")
    
    manifest_path = site_data_root / "manifest.json"
    manifest_entries = {}
    if incremental and manifest_path.is_file():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest_entries = {
            item["id"]: item
            for item in existing.get("recordings", [])
            if (site_data_root / item["id"]).is_dir()
        }
    for recording in recordings:
        source = output_root / recording
        destination = site_data_root / recording
        required = [source / relative for relative in DATA_FILES] + [source / "recording_metadata.json"]
        missing = [str(path) for path in required if not path.is_file()]

        if missing:
            raise FileNotFoundError(f"Missing analysis output for {recording}: {', '.join(missing)}")
        
        destination.mkdir(parents=True, exist_ok=True)
        for relative in DATA_FILES:
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / relative, target)

        metadata = _public_metadata(source / "recording_metadata.json")
        (destination / "recording_metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        report_paths = [source / relative for relative in REPORT_FILES]
        report_available = all(path.is_file() for path in report_paths)
        
        if any(path.is_file() for path in report_paths) and not report_available:
            raise FileNotFoundError(f"Incomplete report output for {recording}: {', '.join(map(str, report_paths))}")
        if report_available:
            for relative in REPORT_FILES:
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / relative, target)
        for name in LEGACY_FILES:
            (destination / name).unlink(missing_ok=True)
        manifest_entries[recording] = {
            "id": recording,
            "label": recording.replace("_", " "),
            "report_available": report_available,
        }
    site_data_root.mkdir(parents=True, exist_ok=True)
    manifest = {"recordings": [manifest_entries[key] for key in sorted(manifest_entries)]}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

def main() -> int:
    parser = argparse.ArgumentParser(description="Export compact analysis data for docs/ GitHub Pages")
    parser.add_argument("--output", type=Path, default=Path("output"))
    parser.add_argument("--site-data-root", type=Path, default=Path("docs/data"))
    args = parser.parse_args()
    export_static_data(args.output, args.site_data_root)
    print(f"Exported static-site data to {args.site_data_root}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
