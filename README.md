# EMG-Resistance

EMG and resistance analysis for cycling recordings. The project supports
official Delsys File Utility export from `.hpf`, EMG filtering and RMS
calculation, signal-phase alignment with resistance data, cadence analysis,
and a static browser report.

## Setup

Requires Python 3.14 or newer and [uv](https://docs.astral.sh/uv/).

```powershell
uv sync
```

Run the exploratory notebooks with:

```powershell
uv run jupyter lab
```

## EMG processing

Place local recordings under `practice/Recording_A` or `practice/Recording_B`,
then run:

```powershell
uv run python main.py run Recording_A
uv run python main.py run Recording_B
uv run python main.py verify Recording_A
```

The `.hpf` export requires Delsys File Utility on Windows. If it is not in a
standard installation location, pass `--file-utility PATH`. When the official
CSV already exists, use `--use-exported-csv` to skip the export step. Detailed
processing, synchronization assumptions, output definitions, and limitations
are documented in [EMG_PROCESSING.md](EMG_PROCESSING.md).

## GitHub Pages report

The frontend in `docs/` is a static site and does not require Python or a
server at runtime. `docs/data/` contains only compact, 10 Hz visualization
data and de-identified metadata; raw `.hpf`, source CSV, and full-resolution
analysis output are not published.

The workflow in `.github/workflows/pages.yml` deploys `docs/` automatically
when changes reach `main`. In the repository settings, enable **Pages** with
**GitHub Actions** as the source. The generated Pages URL is then shown in the
workflow deployment and repository Pages settings.

For local preview, open `docs/index.html` with the VS Code **Live Server**
extension. No project-specific web server is required.

## Validation

```powershell
uv run python -m unittest discover -s tests -v
```

## Data policy

Raw EMG recordings, spreadsheets, exported analysis results, archives,
experiment project files, and presentation files are intentionally excluded
from version control by `.gitignore`. Place those files in the local working
directory when running the pipeline.
