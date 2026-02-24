import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from file_parser import cli


class CliRunValidateArgsTest(unittest.TestCase):
    def test_validate_step_does_not_use_stale_triage_or_timings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            (output_dir / "triage.jsonl").write_text('{"chunk_id":"stale"}\n', encoding="utf-8")
            (output_dir / "stage_timings.json").write_text('{"timings_ms":{"triage":1}}\n', encoding="utf-8")

            calls = []

            def _capture(command, remainder, handler):
                calls.append((command, list(remainder)))
                return None

            with patch("file_parser.cli._dispatch_to_main", side_effect=_capture):
                cli.run(
                    ctx=None,
                    input_dir=str(output_dir),
                    output_dir=str(output_dir),
                    steps="validate",
                    llm_provider="ollama",
                    llm_model="llama3",
                    llm_host="http://localhost:11434",
                    llm_openai_base_url="https://api.openai.com/v1",
                    llm_gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                    llm_timeout=120,
                    llm_two_pass_eval=False,
                    triage_keyword_packs_dir=None,
                    triage_max_llm_chunks=None,
                    triage_max_llm_chunks_per_file=None,
                    triage_max_llm_tokens=None,
                    triage_allow_file_ids=None,
                    triage_deny_file_ids=None,
                    triage_ner=False,
                    triage_ner_model="en_core_web_sm",
                    triage_route_large_threshold=0.75,
                    triage_route_skip_threshold=0.10,
                    triage_ml_route_model=None,
                    triage_ml_route_mode="off",
                    triage_ml_route_skip_threshold=0.90,
                    triage_ml_route_large_threshold=0.80,
                    no_interactive=False,
                )

            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][0], "validate")
            args = calls[0][1]
            self.assertNotIn("--triage", args)
            self.assertNotIn("--timings", args)

    def test_validate_step_includes_triage_and_timings_when_triage_runs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            calls = []

            def _capture(command, remainder, handler):
                calls.append((command, list(remainder)))
                return None

            with patch("file_parser.cli._dispatch_to_main", side_effect=_capture):
                cli.run(
                    ctx=None,
                    input_dir=str(output_dir),
                    output_dir=str(output_dir),
                    steps="triage,validate",
                    llm_provider="ollama",
                    llm_model="llama3",
                    llm_host="http://localhost:11434",
                    llm_openai_base_url="https://api.openai.com/v1",
                    llm_gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                    llm_timeout=120,
                    llm_two_pass_eval=False,
                    triage_keyword_packs_dir=None,
                    triage_max_llm_chunks=None,
                    triage_max_llm_chunks_per_file=None,
                    triage_max_llm_tokens=None,
                    triage_allow_file_ids=None,
                    triage_deny_file_ids=None,
                    triage_ner=False,
                    triage_ner_model="en_core_web_sm",
                    triage_route_large_threshold=0.75,
                    triage_route_skip_threshold=0.10,
                    triage_ml_route_model=None,
                    triage_ml_route_mode="off",
                    triage_ml_route_skip_threshold=0.90,
                    triage_ml_route_large_threshold=0.80,
                    no_interactive=False,
                )

            validate_calls = [call for call in calls if call[0] == "validate"]
            self.assertEqual(len(validate_calls), 1)
            args = validate_calls[0][1]
            self.assertIn("--triage", args)
            self.assertIn("--timings", args)

    def test_triage_step_passes_ml_route_flags(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            calls = []

            def _capture(command, remainder, handler):
                calls.append((command, list(remainder)))
                return None

            with patch("file_parser.cli._dispatch_to_main", side_effect=_capture):
                cli.run(
                    ctx=None,
                    input_dir=str(output_dir),
                    output_dir=str(output_dir),
                    steps="triage",
                    llm_provider="ollama",
                    llm_model="llama3",
                    llm_host="http://localhost:11434",
                    llm_openai_base_url="https://api.openai.com/v1",
                    llm_gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                    llm_timeout=120,
                    llm_two_pass_eval=False,
                    triage_keyword_packs_dir=None,
                    triage_max_llm_chunks=5,
                    triage_max_llm_chunks_per_file=2,
                    triage_max_llm_tokens=500,
                    triage_allow_file_ids=None,
                    triage_deny_file_ids=None,
                    triage_ner=False,
                    triage_ner_model="en_core_web_sm",
                    triage_route_large_threshold=0.75,
                    triage_route_skip_threshold=0.10,
                    triage_ml_route_model="output/ml/route_model.pkl",
                    triage_ml_route_mode="report-only",
                    triage_ml_route_skip_threshold=0.91,
                    triage_ml_route_large_threshold=0.81,
                    no_interactive=False,
                )

            triage_calls = [call for call in calls if call[0] == "triage"]
            self.assertEqual(len(triage_calls), 1)
            triage_args = triage_calls[0][1]
            self.assertIn("--ml-route-model", triage_args)
            self.assertIn("output/ml/route_model.pkl", triage_args)
            self.assertIn("--ml-route-mode", triage_args)
            self.assertIn("report-only", triage_args)
            self.assertIn("--ml-route-skip-threshold", triage_args)
            self.assertIn("0.91", triage_args)
            self.assertIn("--ml-route-large-threshold", triage_args)
            self.assertIn("0.81", triage_args)

    def test_llm_two_pass_eval_runs_large_route_extractors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            # Presence of llm_large chunks enables second pass.
            (output_dir / "chunks.llm_large.jsonl").write_text(
                '{"chunk_id":"x:0-0","file_id":"x","text":"t"}\n',
                encoding="utf-8",
            )
            calls = []

            def _capture(command, remainder, handler):
                calls.append((command, list(remainder)))
                return None

            with patch("file_parser.cli._dispatch_to_main", side_effect=_capture):
                cli.run(
                    ctx=None,
                    input_dir=str(output_dir),
                    output_dir=str(output_dir),
                    steps="llm",
                    llm_provider="ollama",
                    llm_model="llama3",
                    llm_host="http://localhost:11434",
                    llm_openai_base_url="https://api.openai.com/v1",
                    llm_gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                    llm_timeout=120,
                    llm_two_pass_eval=True,
                    triage_keyword_packs_dir=None,
                    triage_max_llm_chunks=None,
                    triage_max_llm_chunks_per_file=None,
                    triage_max_llm_tokens=None,
                    triage_allow_file_ids=None,
                    triage_deny_file_ids=None,
                    triage_ner=False,
                    triage_ner_model="en_core_web_sm",
                    triage_route_large_threshold=0.75,
                    triage_route_skip_threshold=0.10,
                    triage_ml_route_model=None,
                    triage_ml_route_mode="off",
                    triage_ml_route_skip_threshold=0.90,
                    triage_ml_route_large_threshold=0.80,
                    no_interactive=False,
                )

            llm_calls = [call for call in calls if call[0].startswith("llm ")]
            self.assertEqual(len(llm_calls), 6)
            outputs = []
            for _, args in llm_calls:
                if "--output" in args:
                    outputs.append(args[args.index("--output") + 1])
            self.assertIn(str(output_dir / "entities.jsonl"), outputs)
            self.assertIn(str(output_dir / "entities.llm_large.jsonl"), outputs)
            self.assertIn(str(output_dir / "events.llm_large.jsonl"), outputs)
            self.assertIn(str(output_dir / "conversations.llm_large.jsonl"), outputs)


if __name__ == "__main__":
    unittest.main()
