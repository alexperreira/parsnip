import json
import os
from pathlib import Path
import urllib.request

try:
    from dotenv import dotenv_values
except Exception:  # pragma: no cover - optional import fallback
    dotenv_values = None


DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class LLMProviderConfigError(ValueError):
    pass


def _default_dotenv_candidates() -> list[Path]:
    # Prefer caller cwd, then repository root (relative to this module).
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [Path.cwd() / ".env", repo_root / ".env"]
    seen = set()
    unique = []
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        unique.append(path)
    return unique


def _read_openai_api_key_from_dotenv(paths: list[Path]) -> str | None:
    return _read_api_key_from_dotenv(paths, "OPENAI_API_KEY")


def _read_api_key_from_dotenv(paths: list[Path], env_var: str) -> str | None:
    for path in paths:
        if not path.is_file():
            continue
        raw = None
        if dotenv_values is None:
            raw = _fallback_dotenv_get(path, env_var)
        else:
            try:
                values = dotenv_values(path)
            except Exception:
                values = None
            raw = values.get(env_var) if isinstance(values, dict) else None
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _resolve_openai_api_key() -> str | None:
    api_key = os.getenv("OPENAI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip()
    return _read_openai_api_key_from_dotenv(_default_dotenv_candidates())

def _resolve_gemini_api_key() -> str | None:
    api_key = os.getenv("GEMINI_API_KEY")
    if isinstance(api_key, str) and api_key.strip():
        return api_key.strip()
    return _read_api_key_from_dotenv(_default_dotenv_candidates(), "GEMINI_API_KEY")

def call_llm(
    prompt: str,
    model: str,
    provider: str,
    host: str,
    timeout: int,
    openai_base_url: str,
    gemini_base_url: str,
) -> str:
    if provider == "openai":
        return _call_openai(prompt, model, timeout, openai_base_url)
    if provider == "gemini":
        return _call_gemini(prompt, model, timeout, gemini_base_url)
    return _call_ollama(prompt, model, host, timeout)


def _call_ollama(prompt: str, model: str, host: str, timeout: int) -> str:
    url = host.rstrip("/") + "/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body)
    return parsed.get("response", "")


def _call_openai(prompt: str, model: str, timeout: int, base_url: str) -> str:
    api_key = _resolve_openai_api_key()
    if not isinstance(api_key, str) or not api_key.strip():
        raise LLMProviderConfigError(
            "OPENAI_API_KEY is required when --provider=openai "
            "(set env var or define OPENAI_API_KEY in .env)."
        )

    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body)
    choices = parsed.get("choices")
    if not isinstance(choices, list) or not choices:
        raise LLMProviderConfigError("OpenAI response missing choices.")
    first = choices[0]
    if not isinstance(first, dict):
        raise LLMProviderConfigError("OpenAI response choice is invalid.")
    message = first.get("message")
    if not isinstance(message, dict):
        raise LLMProviderConfigError("OpenAI response missing message.")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts)
    raise LLMProviderConfigError("OpenAI response missing message content.")


def _call_gemini(prompt: str, model: str, timeout: int, base_url: str) -> str:
    api_key = _resolve_gemini_api_key()
    if not isinstance(api_key, str) or not api_key.strip():
        raise LLMProviderConfigError(
            "GEMINI_API_KEY is required when --provider=gemini "
            "(set env var or define GEMINI_API_KEY in .env)."
        )

    url = base_url.rstrip("/") + f"/models/{model}:generateContent"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0},
    }
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": api_key,
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        body = response.read().decode("utf-8")
    parsed = json.loads(body)
    candidates = parsed.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        raise LLMProviderConfigError("Gemini response missing candidates.")
    first = candidates[0]
    if not isinstance(first, dict):
        raise LLMProviderConfigError("Gemini response candidate is invalid.")
    content = first.get("content")
    if not isinstance(content, dict):
        raise LLMProviderConfigError("Gemini response missing content.")
    parts = content.get("parts")
    if not isinstance(parts, list) or not parts:
        raise LLMProviderConfigError("Gemini response missing content parts.")
    texts = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
    return "".join(texts)


def _fallback_dotenv_get(path: Path, key: str) -> str | None:
    # Minimal .env parsing fallback when python-dotenv isn't available.
    # Supports KEY=VALUE with optional single/double quotes and basic escapes.
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                raw = line.strip()
                if not raw or raw.startswith("#"):
                    continue
                if "=" not in raw:
                    continue
                name, value = raw.split("=", 1)
                name = name.strip()
                if name != key:
                    continue
                value = value.strip()
                if not value:
                    return None
                if value.startswith('"') and value.endswith('"') and len(value) >= 2:
                    unquoted = value[1:-1]
                    return (
                        unquoted.replace("\\\\", "\\")
                        .replace('\\"', '"')
                        .replace("\\n", "\n")
                        .replace("\\r", "\r")
                        .replace("\\t", "\t")
                        .strip()
                    )
                if value.startswith("'") and value.endswith("'") and len(value) >= 2:
                    return value[1:-1].strip()
                # Strip trailing comments for unquoted values: KEY=value # comment
                if " #" in value:
                    value = value.split(" #", 1)[0].strip()
                return value.strip()
    except Exception:
        return None
    return None
