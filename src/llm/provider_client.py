import json
import os
import urllib.request


DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"


class LLMProviderConfigError(ValueError):
    pass


def call_llm(prompt: str, model: str, provider: str, host: str, timeout: int, openai_base_url: str) -> str:
    if provider == "openai":
        return _call_openai(prompt, model, timeout, openai_base_url)
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
    api_key = os.getenv("OPENAI_API_KEY")
    if not isinstance(api_key, str) or not api_key.strip():
        raise LLMProviderConfigError(
            "OPENAI_API_KEY is required when --provider=openai."
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
