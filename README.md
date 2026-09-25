"# EMG-Resistance" 

EMG and torque analysis notebooks for the resistance experiment.

## Contents

- `main.ipynb`: exploratory analysis
- `EMG_Torque_Avg.ipynb`: average-based EMG/torque analysis
- `EMG_Torque_Median.ipynb`: median-based EMG/torque analysis
- `pyproject.toml` and `uv.lock`: Python project and dependency definitions

## Setup

Requires Python 3.14 or newer and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
```

Open the notebooks with Jupyter after installing the environment:

```powershell
uv run jupyter lab
```

## Data policy

Raw EMG recordings, spreadsheets, CSV files, exported analysis results,
archives, experiment project files, and presentation files are intentionally
excluded from version control by `.gitignore`. Place those files in the local
working directory when running the notebooks."
"# EMG-Resistance" 
