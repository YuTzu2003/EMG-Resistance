from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import build_opener, ProxyHandler, Request, urlopen
from .config import AppConfig

class LLMError(RuntimeError):
    """An expected provider, transport, or response failure."""

@dataclass(frozen=True)
class LLMResponse:
    content: str
    provider: str
    model: str
    endpoint: str

def _post_json(
    url: str,
    payload: dict,
    headers: dict[str, str],
    timeout_s: float,
    bypass_proxy: bool = False,
) -> dict:
    request = Request(url,data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),headers={"Content-Type": "application/json", **headers},method="POST",)
    try:
        opener = build_opener(ProxyHandler({})) if bypass_proxy else None
        response_context = opener.open(request, timeout=timeout_s) if opener else urlopen(request, timeout=timeout_s)
        with response_context as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise LLMError(f"LLM HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

def generate_text(config: AppConfig, system_prompt: str, user_prompt: str) -> LLMResponse:
    if not config.llm_enabled:
        raise LLMError("LLM provider is disabled")
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    if config.llm_provider == "ollama":
        endpoint = f"{config.llm_base_url}/api/chat"
        payload = {
            "model": config.llm_model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": config.llm_temperature},
        }
        body = _post_json(endpoint, payload, {}, config.llm_timeout_s, bypass_proxy=True)
        content = body.get("message", {}).get("content", "")
    else:
        endpoint = f"{config.llm_base_url}/chat/completions"
        payload = {
            "model": config.llm_model,
            "messages": messages,
            "temperature": config.llm_temperature,
        }
        headers = {"Authorization": f"Bearer {config.llm_api_key}"}
        body = _post_json(endpoint, payload, headers, config.llm_timeout_s)
        choices = body.get("choices") or []
        content = choices[0].get("message", {}).get("content", "") if choices else ""
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM returned no report text")
    return LLMResponse(content.strip(), config.llm_provider, config.llm_model, endpoint)
