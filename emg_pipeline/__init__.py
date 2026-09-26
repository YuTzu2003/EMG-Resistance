"""Public entry points for EMG and resistance analysis."""

from .analysis_reporting import generate_report
from .application import main
from .signal_processing import process_recording

__all__ = ["generate_report", "main", "process_recording"]
