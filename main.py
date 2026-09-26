"""Single project entry point.

Examples:
    python main.py run Recording_A --use-exported-csv
    python main.py report Recording_A
    python main.py verify Recording_A
"""

from emg_pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())