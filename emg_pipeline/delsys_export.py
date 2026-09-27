from pathlib import Path
import shutil
import subprocess

def find_file_utility(explicit: Path | None = None) -> Path:
    candidates = [explicit] if explicit else [
        Path(r"C:\Program Files (x86)\Delsys, Inc\Delsys File Utility\DelsysFileUtil.exe"),
        Path(r"C:\Program Files\Delsys, Inc\Delsys File Utility\DelsysFileUtil.exe"),
    ]
    command = shutil.which("DelsysFileUtil.exe")
    if command and not explicit:
        candidates.append(Path(command))
    for candidate in candidates:
        if candidate is not None and candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError("DelsysFileUtil.exe not found; pass --file-utility PATH")

def export_hpf(hpf: Path,file_utility: Path | None = None,overwrite: bool = False,) -> Path:
    hpf = hpf.expanduser().resolve()
    if not hpf.is_file():
        raise FileNotFoundError(f"HPF file not found: {hpf}")
    if hpf.suffix.lower() != ".hpf":
        raise ValueError(f"Expected an .hpf file: {hpf}")
    exported = hpf.with_suffix(".csv")
    if exported.exists() and not overwrite:
        raise FileExistsError( f"CSV already exists: {exported}; pass --overwrite to replace it")
    utility = find_file_utility(file_utility)
    try:
        subprocess.run([str(utility), "-nogui", "-o", "CSV", "-i", str(hpf)],check=True,capture_output=True,text=True,timeout=180,)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Delsys File Utility failed to export {hpf}: {exc}") from exc
    if not exported.is_file() or exported.stat().st_size == 0:
        raise RuntimeError(f"Delsys File Utility produced no CSV: {exported}")
    return exported
