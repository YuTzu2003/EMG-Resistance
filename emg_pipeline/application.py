import argparse
from dataclasses import replace
import json
from pathlib import Path
from .analysis_reporting import generate_report
from .config import DEFAULT_ENV_FILE, DEFAULT_OUTPUT_ROOT, DEFAULT_SITE_ROOT, load_config
from .signal_processing import process_recording
from .report_export import export_static_data

def _config(args: argparse.Namespace):
    config = load_config(args.env_file)
    if args.no_llm:
        return replace(config, llm_provider="none", llm_base_url="", llm_model="", llm_api_key=None)
    return config

def _export_site(output_root: Path, site_root: Path, recording_name: str) -> None:
    export_static_data(output_root, site_root / "data", (recording_name,))
    print(f"Frontend data: {site_root / 'data'}")

def _run_sources(args: argparse.Namespace) -> tuple[Path, Path, Path | None, str]:
    if args.recording_path is not None:
        if args.hpf_file is not None or args.resistance_file is not None or args.emg_csv is not None:
            raise ValueError(
                "Use either RECORDING_PATH or --hpf-file/--resistance-file, not both"
            )
        recording = args.recording_path.expanduser().resolve()
        hpf = recording / "EMG" / "EMG_Raw.hpf"
        resistance = recording / "Resistance" / "Resistance.csv"
        exported_csv = hpf.with_suffix(".csv") if args.use_exported_csv else None
        recording_name = args.recording_name or recording.name
    else:
        if args.hpf_file is None or args.resistance_file is None:
            raise ValueError(
                "Direct-file mode requires both --hpf-file and --resistance-file"
            )
        hpf = args.hpf_file.expanduser().resolve()
        resistance = args.resistance_file.expanduser().resolve()
        exported_csv = args.emg_csv.expanduser().resolve() if args.emg_csv else None
        if args.use_exported_csv and exported_csv is None:
            exported_csv = hpf.with_suffix(".csv")
        recording_name = args.recording_name or hpf.stem
    if not recording_name or recording_name in {".", ".."} or Path(recording_name).name != recording_name:
        raise ValueError("--recording-name must be one folder name without path separators")
    return hpf, resistance, exported_csv, recording_name

def _run(args: argparse.Namespace) -> int:
    hpf, resistance, exported_csv, recording_name = _run_sources(args)
    output_root = args.output_root.expanduser().resolve()
    site_root = args.site_root.expanduser().resolve()
    result = process_recording(
        hpf=hpf,
        resistance=resistance,
        output_root=output_root,
        recording_name=recording_name,
        file_utility=args.file_utility,
        exported_csv=exported_csv,
        emg_event_s=args.emg_event_s,
        resistance_event_s=args.resistance_event_s,
        no_auto_sync=args.no_auto_sync,
    )
    print(f"Processed {recording_name}: {result['sample_count']} EMG samples, {result['channel_count']} channels")
    if result["synchronization"].get("status") == "not_requested":
        print("Synchronization skipped; report and frontend export were not generated.")
        return 0
    print(f"Synchronized rows: {result['synchronization']['overlap_sample_count']}")
    report = generate_report(output_root / recording_name, _config(args))
    print(f"Report: {report['report']}")
    print(f"LLM: {report['trace_data']['llm_status']} ({report['trace_data']['provider']})")
    _export_site(output_root, site_root, recording_name)
    return 0

def _report(args: argparse.Namespace) -> int:
    output = args.recording_output.expanduser().resolve()
    if not output.is_dir():
        raise FileNotFoundError(f"Existing analysis output not found: {output}")
    report = generate_report(output, _config(args))
    print(f"Report: {report['report']}")
    print(f"LLM: {report['trace_data']['llm_status']} ({report['trace_data']['provider']})")
    _export_site(output.parent, args.site_root, output.name)
    return 0

def _verify(args: argparse.Namespace) -> int:
    output = args.recording_output.expanduser().resolve()
    recording_name = output.name
    site_data = args.site_root / "data" / recording_name
    expected = (
        output / "metadata.json",
        output / "sync" / "preview_10hz.csv",
        output / "cadence" / "stroke_events.csv",
        output / "analysis" / "muscle_metrics.csv",
        output / "report" / "analysis_summary.json",
        output / "report" / "report.md",
        output / "report" / "trace.json",
        site_data / "metadata.json",
        site_data / "preview_10hz.csv",
        site_data / "stroke_events.csv",
        site_data / "muscle_metrics.csv",
        site_data / "analysis_summary.json",
        site_data / "report.md",
        site_data / "trace.json",)
    missing = [str(path) for path in expected if not path.is_file()]
    if missing:
        raise FileNotFoundError("Verification failed; missing files: " + ", ".join(missing))
    summary = json.loads((output / "report" / "analysis_summary.json").read_text(encoding="utf-8"))
    trace = json.loads((output / "report" / "trace.json").read_text(encoding="utf-8"))
    manifest = json.loads((args.site_root / "data" / "manifest.json").read_text(encoding="utf-8"))
    manifest_ids = {item["id"] for item in manifest.get("recordings", [])}
    checks = {
        "summary recording": summary.get("recording") == recording_name,
        "Python/LLM boundary": summary.get("calculated_by", "").startswith("emg_pipeline Python"),
        "trace data scope": trace.get("data_scope") in {"summary", "aligned_sample"},
        "API key not recorded": trace.get("api_key_recorded") is False,
        "frontend manifest": recording_name in manifest_ids,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ValueError("Verification failed: " + ", ".join(failed))
    print(f"Verification passed: {recording_name}")
    for name in checks:
        print(f"  OK  {name}")
    return 0

def _add_storage_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--site-root", type=Path, default=DEFAULT_SITE_ROOT)

def _add_site_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--site-root", type=Path, default=DEFAULT_SITE_ROOT)

def _add_report_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--no-llm", action="store_true", help="Generate the deterministic report without an LLM")

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EMG analysis, report generation, and frontend export")
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Process one recording, generate its report, and refresh frontend data")
    run.add_argument(
        "recording_path",
        type=Path,
        nargs="?",
        help="Recording folder containing EMG/ and Resistance/ (omit in direct-file mode)",
    )
    run.add_argument("--hpf-file", type=Path, help="Direct path to the source .hpf file")
    run.add_argument("--resistance-file", type=Path, help="Direct path to the Resistance CSV")
    run.add_argument(
        "--emg-csv",
        type=Path,
        help="Existing Delsys File Utility CSV; omit to export the HPF automatically",
    )
    run.add_argument(
        "--recording-name",
        help="Output folder/report name (defaults to the input folder name or HPF filename)",
    )
    run.add_argument("--file-utility", type=Path, help="Path to DelsysFileUtil.exe")
    run.add_argument(
        "--use-exported-csv",
        action="store_true",
        help="Folder mode: use EMG/EMG_Raw.csv; direct mode: use the CSV beside the HPF",
    )
    run.add_argument("--emg-event-s", type=float)
    run.add_argument("--resistance-event-s", type=float)
    run.add_argument("--no-auto-sync", action="store_true", help="Only export and plot EMG")
    _add_storage_options(run)
    _add_report_options(run)
    run.set_defaults(handler=_run)

    report = commands.add_parser("report", help="Regenerate a report from existing analysis output")
    report.add_argument("recording_output", type=Path, help="Existing output folder for one recording")
    _add_site_option(report)
    _add_report_options(report)
    report.set_defaults(handler=_report)

    verify = commands.add_parser("verify", help="Verify analysis, report, and frontend outputs")
    verify.add_argument("recording_output", type=Path, help="Existing output folder for one recording")
    _add_site_option(verify)
    verify.set_defaults(handler=_verify)
    return parser

def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (FileNotFoundError, RuntimeError, ValueError, OSError) as exc:
        parser.exit(1, f"Error: {exc}\n")
