import json
import tempfile
import unittest
from pathlib import Path

from file_parser import cli, compress_io


@unittest.skipUnless(compress_io._zstandard is not None, "zstandard dependency is required")
class CliRunExtractChunkTriageIntegrationTest(unittest.TestCase):
    def _seed_phase_inputs(self, output_dir: Path):
        output_dir.mkdir(parents=True, exist_ok=True)
        phase1_path = output_dir / "phase1.jsonl"
        phase2_path = output_dir / "phase2_ocr.jsonl"
        phase1_record = {
            "file_id": "doc_a",
            "ext": "pdf",
            "classification": "scanned",
            "page_count": 1,
            "virtual_path": "unused.pdf",
        }
        phase2_record = {
            "file_id": "doc_a",
            "pages": [
                {
                    "text": "On 2025-01-02 Officer Jane Doe reported a warrant affidavit summary.",
                    "confidence": 0.95,
                }
            ],
        }
        phase1_path.write_text(json.dumps(phase1_record, ensure_ascii=True) + "\n", encoding="utf-8")
        phase2_path.write_text(json.dumps(phase2_record, ensure_ascii=True) + "\n", encoding="utf-8")

    def _run_extract_chunk_triage(self, input_dir: Path, output_dir: Path):
        cli.run(
            ctx=None,
            input_dir=str(input_dir),
            output_dir=str(output_dir),
            steps="extract-text,chunk,triage",
            llm_provider="ollama",
            llm_model="llama3",
            llm_small_model=None,
            llm_large_model=None,
            llm_host="http://localhost:11434",
            llm_openai_base_url="https://api.openai.com/v1",
            llm_gemini_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            llm_timeout=120,
            llm_two_pass_eval=False,
            llm_retry_small_failures=False,
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

    def test_run_extract_chunk_triage_outputs_are_stable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            input_dir = root / "input"
            input_dir.mkdir(parents=True, exist_ok=True)

            output_a = root / "out_a"
            output_b = root / "out_b"
            self._seed_phase_inputs(output_a)
            self._seed_phase_inputs(output_b)

            self._run_extract_chunk_triage(input_dir, output_a)
            self._run_extract_chunk_triage(input_dir, output_b)

            chunks_a = (output_a / "text" / "chunks.jsonl")
            chunks_b = (output_b / "text" / "chunks.jsonl")
            triage_a = output_a / "triage.jsonl"
            triage_b = output_b / "triage.jsonl"

            self.assertTrue(chunks_a.exists())
            self.assertTrue(chunks_b.exists())
            self.assertTrue(triage_a.exists())
            self.assertTrue(triage_b.exists())
            self.assertEqual(chunks_a.read_text(encoding="utf-8"), chunks_b.read_text(encoding="utf-8"))
            self.assertEqual(triage_a.read_text(encoding="utf-8"), triage_b.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
