import argparse
import json
import shutil
from pathlib import Path

FILES = (
    ("sync/preview_10hz.csv", "preview_10hz.csv"),
    ("cadence/stroke_events.csv", "stroke_events.csv"),
    ("analysis/muscle_metrics.csv", "muscle_metrics.csv"),
)
REPORT_FILES = (
    ("report/analysis_summary.json", "analysis_summary.json"),
    ("report/report.md", "report.md"),
    ("report/trace.json", "trace.json"),
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
    if recordings is None:
        recordings = tuple(
            path.name for path in sorted(output_root.iterdir())
            if path.is_dir()
            and all((path / relative).is_file() for relative, _ in FILES)
            and (path / "metadata.json").is_file()
        )
    if not recordings:
        raise FileNotFoundError(f"No complete analysis output found under {output_root}")
    
    manifest = {"recordings": []}
    for recording in recordings:
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
        (destination/"metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        report_paths = [source / relative for relative, _ in REPORT_FILES]
        report_available = all(path.is_file() for path in report_paths)
        
        if any(path.is_file() for path in report_paths) and not report_available:
            raise FileNotFoundError(f"Incomplete report output for {recording}: {', '.join(map(str, report_paths))}")
        if report_available:
            for relative, name in REPORT_FILES:
                shutil.copyfile(source / relative, destination / name)
        manifest["recordings"].append({
            "id": recording,
            "label": recording.replace("_", " "),
            "report_available": report_available,
        })
    site_data_root.mkdir(parents=True, exist_ok=True)
    (site_data_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

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
