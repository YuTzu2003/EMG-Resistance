from dataclasses import dataclass
import os
from pathlib import Path

_PROVIDERS = {"none", "ollama", "openai"}
_DATA_SCOPES = {"summary", "aligned_sample"}
_REPORT_TEMPLATES = {"rider", "research"}
RECORDINGS = ("Recording_A", "Recording_B")
DEFAULT_PRACTICE_ROOT = Path("practice")
DEFAULT_OUTPUT_ROOT = Path("output")
DEFAULT_SITE_ROOT = Path("docs")
DEFAULT_ENV_FILE = Path(".env")

def _read_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"Invalid .env line {number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class AppConfig:
    llm_provider: str = "none"
    llm_base_url: str = ""
    llm_model: str = ""
    llm_api_key: str | None = None
    llm_timeout_s: float = 120.0
    llm_temperature: float = 0.2
    llm_data_scope: str = "summary"
    llm_max_sample_rows: int = 120
    report_template: str = "rider"
    @property
    def llm_enabled(self) -> bool:
        return self.llm_provider != "none"

def load_config(env_file: Path = DEFAULT_ENV_FILE, environ: dict[str, str] | None = None) -> AppConfig:
    values = _read_env_file(env_file)
    values.update(environ if environ is not None else os.environ)
    provider = values.get("LLM_PROVIDER", "none").strip().lower()
    data_scope = values.get("LLM_DATA_SCOPE", "summary").strip().lower()
    report_template = values.get("REPORT_TEMPLATE", "rider").strip().lower()

    if provider not in _PROVIDERS:
        raise ValueError(f"LLM_PROVIDER must be one of {sorted(_PROVIDERS)}")
    if data_scope not in _DATA_SCOPES:
        raise ValueError(f"LLM_DATA_SCOPE must be one of {sorted(_DATA_SCOPES)}")
    if report_template not in _REPORT_TEMPLATES:
        raise ValueError(f"REPORT_TEMPLATE must be one of {sorted(_REPORT_TEMPLATES)}")
    
    base_url = values.get("LLM_BASE_URL", "").strip().rstrip("/")
    model = values.get("LLM_MODEL", "").strip()
    api_key = values.get("LLM_API_KEY", "").strip() or None
    if provider != "none" and (not base_url or not model):
        raise ValueError("LLM_BASE_URL and LLM_MODEL are required when LLM_PROVIDER is enabled")
    if provider == "openai" and not api_key:
        raise ValueError("LLM_API_KEY is required when LLM_PROVIDER=openai")
    timeout_s = float(values.get("LLM_TIMEOUT_S", "120"))
    temperature = float(values.get("LLM_TEMPERATURE", "0.2"))
    max_sample_rows = int(values.get("LLM_MAX_SAMPLE_ROWS", "120"))
    if timeout_s <= 0:
        raise ValueError("LLM_TIMEOUT_S must be positive")
    if not 0 <= temperature <= 2:
        raise ValueError("LLM_TEMPERATURE must be between 0 and 2")
    if not 1 <= max_sample_rows <= 1000:
        raise ValueError("LLM_MAX_SAMPLE_ROWS must be between 1 and 1000")
    return AppConfig(
        llm_provider=provider,
        llm_base_url=base_url,
        llm_model=model,
        llm_api_key=api_key,
        llm_timeout_s=timeout_s,
        llm_temperature=temperature,
        llm_data_scope=data_scope,
        llm_max_sample_rows=max_sample_rows,
        report_template=report_template,
    )