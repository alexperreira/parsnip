import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from llm.provider_client import LLMProviderConfigError, _call_gemini, _call_openai


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class LlmProviderClientTest(unittest.TestCase):
    def test_openai_requires_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("llm.provider_client._default_dotenv_candidates", return_value=[]):
                with self.assertRaises(LLMProviderConfigError):
                    _call_openai("prompt", "gpt-4.1-mini", timeout=3, base_url="https://api.openai.com/v1")

    def test_gemini_requires_api_key(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with mock.patch("llm.provider_client._default_dotenv_candidates", return_value=[]):
                with self.assertRaises(LLMProviderConfigError):
                    _call_gemini(
                        "prompt",
                        "gemini-3.1-pro",
                        timeout=3,
                        base_url="https://generativelanguage.googleapis.com/v1beta",
                    )

    def test_openai_parses_string_content(self):
        captured = {}

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["auth"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"items":[{"entity":"Alice","type":"person","quote":"Alice","confidence":0.9}]}'
                            }
                        }
                    ]
                }
            )

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                text = _call_openai(
                    "prompt",
                    "gpt-4.1-mini",
                    timeout=7,
                    base_url="https://api.openai.com/v1",
                )

        self.assertIn('"items"', text)
        self.assertEqual(captured["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(captured["auth"], "Bearer sk-test")
        self.assertEqual(captured["timeout"], 7)

    def test_gemini_parses_parts_text(self):
        captured = {}

        def _fake_urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["api_key"] = request.headers.get("X-goog-api-key") or request.headers.get("x-goog-api-key")
            captured["timeout"] = timeout
            return _FakeResponse(
                {
                    "candidates": [
                        {
                            "content": {
                                "parts": [
                                    {"text": '{"items":'},
                                    {"text": "[]"},
                                    {"text": "}"},
                                ]
                            }
                        }
                    ]
                }
            )

        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "gm-test"}):
            with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                text = _call_gemini(
                    "prompt",
                    "gemini-3.1-pro",
                    timeout=7,
                    base_url="https://generativelanguage.googleapis.com/v1beta",
                )

        self.assertEqual(text, '{"items":[]}')
        self.assertEqual(
            captured["url"],
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.1-pro:generateContent",
        )
        self.assertEqual(captured["api_key"], "gm-test")
        self.assertEqual(captured["timeout"], 7)

    def test_openai_parses_list_content(self):
        def _fake_urlopen(_request, timeout=None):
            _ = timeout
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": [
                                    {"type": "output_text", "text": '{"items":'},
                                    {"type": "output_text", "text": "[]"},
                                    {"type": "output_text", "text": "}"},
                                ]
                            }
                        }
                    ]
                }
            )

        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}):
            with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                text = _call_openai(
                    "prompt",
                    "gpt-4.1-mini",
                    timeout=7,
                    base_url="https://api.openai.com/v1",
                )

        self.assertEqual(text, '{"items":[]}')

    def test_gemini_uses_dotenv_when_env_missing(self):
        captured = {}

        def _fake_urlopen(request, timeout):
            captured["api_key"] = request.headers.get("X-goog-api-key") or request.headers.get("x-goog-api-key")
            captured["timeout"] = timeout
            return _FakeResponse({"candidates": [{"content": {"parts": [{"text": '{"items":[]}' }]}}]})

        with tempfile.TemporaryDirectory() as tmpdir:
            dotenv_path = Path(tmpdir) / ".env"
            dotenv_path.write_text("GEMINI_API_KEY=gm-from-dotenv\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch(
                    "llm.provider_client._default_dotenv_candidates",
                    return_value=[dotenv_path],
                ):
                    with mock.patch(
                        "llm.provider_client.dotenv_values",
                        return_value={"GEMINI_API_KEY": "gm-from-dotenv"},
                    ):
                        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                            _call_gemini(
                                "prompt",
                                "gemini-3.1-pro",
                                timeout=7,
                                base_url="https://generativelanguage.googleapis.com/v1beta",
                            )

        self.assertEqual(captured["api_key"], "gm-from-dotenv")
        self.assertEqual(captured["timeout"], 7)

    def test_openai_uses_dotenv_when_env_missing(self):
        captured = {}

        def _fake_urlopen(request, timeout):
            captured["auth"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"items":[]}'
                            }
                        }
                    ]
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            dotenv_path = Path(tmpdir) / ".env"
            dotenv_path.write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch(
                    "llm.provider_client._default_dotenv_candidates",
                    return_value=[dotenv_path],
                ):
                    with mock.patch(
                        "llm.provider_client.dotenv_values",
                        return_value={"OPENAI_API_KEY": "sk-from-dotenv"},
                    ):
                        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                            _call_openai(
                                "prompt",
                                "gpt-4.1-mini",
                                timeout=7,
                                base_url="https://api.openai.com/v1",
                            )

        self.assertEqual(captured["auth"], "Bearer sk-from-dotenv")
        self.assertEqual(captured["timeout"], 7)

    def test_openai_prefers_env_over_dotenv(self):
        captured = {}

        def _fake_urlopen(request, timeout):
            captured["auth"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"items":[]}'
                            }
                        }
                    ]
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            dotenv_path = Path(tmpdir) / ".env"
            dotenv_path.write_text("OPENAI_API_KEY=sk-from-dotenv\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}, clear=True):
                with mock.patch(
                    "llm.provider_client._default_dotenv_candidates",
                    return_value=[dotenv_path],
                ):
                    with mock.patch(
                        "llm.provider_client.dotenv_values",
                        return_value={"OPENAI_API_KEY": "sk-from-dotenv"},
                    ):
                        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                            _call_openai(
                                "prompt",
                                "gpt-4.1-mini",
                                timeout=7,
                                base_url="https://api.openai.com/v1",
                            )

        self.assertEqual(captured["auth"], "Bearer sk-from-env")
        self.assertEqual(captured["timeout"], 7)

    def test_openai_uses_python_dotenv_values(self):
        captured = {}

        def _fake_urlopen(request, timeout):
            captured["auth"] = request.headers.get("Authorization")
            captured["timeout"] = timeout
            return _FakeResponse(
                {
                    "choices": [
                        {
                            "message": {
                                "content": '{"items":[]}'
                            }
                        }
                    ]
                }
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            dotenv_path = Path(tmpdir) / ".env"
            dotenv_path.write_text('OPENAI_API_KEY="line1\\nline2"\n', encoding="utf-8")

            def _fake_dotenv_values(_path):
                # Simulate python-dotenv parsed output; raw multiline semantics are delegated to library.
                return {"OPENAI_API_KEY": "sk-dotenv-parser"}

            with mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch(
                    "llm.provider_client._default_dotenv_candidates",
                    return_value=[dotenv_path],
                ):
                    with mock.patch("llm.provider_client.dotenv_values", side_effect=_fake_dotenv_values):
                        with mock.patch("urllib.request.urlopen", side_effect=_fake_urlopen):
                            _call_openai(
                                "prompt",
                                "gpt-4.1-mini",
                                timeout=7,
                                base_url="https://api.openai.com/v1",
                            )

        self.assertEqual(captured["auth"], "Bearer sk-dotenv-parser")
        self.assertEqual(captured["timeout"], 7)


if __name__ == "__main__":
    unittest.main()
