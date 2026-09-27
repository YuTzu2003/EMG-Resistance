"""Public entry points for EMG and resistance analysis."""

__all__ = ["generate_report", "main", "process_recording"]


def __getattr__(name: str):
    """Load the analysis stack only when a public analysis entry point is used."""
    if name == "main":
        from .application import main
        return main
    if name == "generate_report":
        from .analysis_reporting import generate_report
        return generate_report
    if name == "process_recording":
        from .signal_processing import process_recording
        return process_recording
    raise AttributeError(name)
