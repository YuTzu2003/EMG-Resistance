"""Single project entry point.

Examples:
    python main.py run "D:/EMG_Data/Recording_A"
    python main.py run --hpf-file "D:/EMG_Data/test.hpf" --resistance-file "D:/EMG_Data/resistance.csv" --output "D:/EMG_Results/test"
    python main.py report "output/Recording_A"
    python main.py verify "output/Recording_A"
"""

from emg_pipeline import main

if __name__ == "__main__":
    raise SystemExit(main())
