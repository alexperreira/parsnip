import json
import os
import unittest
from unittest import mock

from llm.provider_client import LLMProviderConfigError, _call_openai


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
            with self.assertRaises(LLMProviderConfigError):
                _call_openai("prompt", "gpt-4.1-mini", timeout=3, base_url="https://api.openai.com/v1")

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


if __name__ == "__main__":
    unittest.main()
