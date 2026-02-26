import json
import pickle
import tempfile
import unittest
from pathlib import Path

from triage.phase_t1_triage_chunks import build_triage


class TriagePhaseT1Test(unittest.TestCase):
    def _write_jsonl(self, path: Path, records):
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def test_build_triage_writes_outputs_and_budgets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            out_dir = root / "out"

            # One high-signal and one low-signal chunk.
            self._write_jsonl(
                chunks_path,
                [
                    {
                        "chunk_id": "a:0-0",
                        "file_id": "a",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "warrant affidavit on 2025-01-02 at 10:35 PM",
                    },
                    {
                        "chunk_id": "a:1-1",
                        "file_id": "a",
                        "page_start": 1,
                        "page_end": 1,
                        "text": "   ",
                    },
                ],
            )

            summary = build_triage(
                chunks_path=chunks_path,
                output_dir=out_dir,
                max_llm_chunks=1,
                route_skip_threshold=0.2,
            )

            triage_path = Path(summary["triage_path"])
            small_path = Path(summary["llm_small_path"])
            large_path = Path(summary["llm_large_path"])

            self.assertTrue(triage_path.exists())
            self.assertTrue(small_path.exists())
            self.assertTrue(large_path.exists())

            triage_records = [json.loads(line) for line in triage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(triage_records), 2)
            self.assertIn(triage_records[0]["route"], {"llm_small", "llm_large", "skip"})

            selected_small = [json.loads(line) for line in small_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            selected_large = [json.loads(line) for line in large_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(selected_small) + len(selected_large), 1)

    def test_build_triage_model_load_failure_is_fail_soft(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            out_dir = root / "out"

            self._write_jsonl(
                chunks_path,
                [
                    {
                        "chunk_id": "a:0-0",
                        "file_id": "a",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "incident report witness statement",
                    }
                ],
            )

            summary = build_triage(
                chunks_path=chunks_path,
                output_dir=out_dir,
                ml_route_mode="full",
                ml_route_model_path=root / "missing_model.pkl",
            )

            self.assertFalse(summary["ml_model_loaded"])
            self.assertEqual(summary["ml_model_status"], "model_not_found")
            triage_path = Path(summary["triage_path"])
            triage_records = [json.loads(line) for line in triage_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertEqual(len(triage_records), 1)
            self.assertEqual(triage_records[0]["route"], triage_records[0]["heuristic_route"])
            self.assertEqual(triage_records[0]["ml_route"]["policy_gates"], ["fallback_heuristic"])

    def test_build_triage_report_only_does_not_change_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            out_base = root / "out_base"
            out_report_only = root / "out_report_only"
            model_path = root / "route_model.pkl"

            self._write_jsonl(
                chunks_path,
                [
                    {
                        "chunk_id": "a:0-0",
                        "file_id": "a",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "warrant affidavit at 10:35 PM for vehicle stop with witness",
                    },
                    {
                        "chunk_id": "a:1-1",
                        "file_id": "a",
                        "page_start": 1,
                        "page_end": 1,
                        "text": " ",
                    },
                ],
            )

            # Force deterministic model output with priors; report-only mode must still keep heuristic routes.
            fake_model = {
                "model_type": "multinomial_nb",
                "classes": ["skip", "llm_small", "llm_large"],
                "vocab": {},
                "log_priors": {"skip": 0.0, "llm_small": -10.0, "llm_large": -10.0},
                "log_unknown_probs": {"skip": -1.0, "llm_small": -1.0, "llm_large": -1.0},
                "log_token_probs": {"skip": [], "llm_small": [], "llm_large": []},
                "alpha": 1.0,
                "token_regex": r"[A-Za-z0-9_]+",
            }
            with model_path.open("wb") as handle:
                pickle.dump(fake_model, handle, protocol=pickle.HIGHEST_PROTOCOL)

            base_summary = build_triage(
                chunks_path=chunks_path,
                output_dir=out_base,
                route_skip_threshold=0.2,
            )
            report_summary = build_triage(
                chunks_path=chunks_path,
                output_dir=out_report_only,
                route_skip_threshold=0.2,
                ml_route_mode="report-only",
                ml_route_model_path=model_path,
            )

            base_triage = Path(base_summary["triage_path"]).read_text(encoding="utf-8")
            report_triage_rows = [
                json.loads(line)
                for line in Path(report_summary["triage_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            base_triage_rows = [json.loads(line) for line in base_triage.splitlines() if line.strip()]
            self.assertEqual([r["route"] for r in report_triage_rows], [r["route"] for r in base_triage_rows])
            self.assertTrue(all(row["ml_route"]["mode"] == "report-only" for row in report_triage_rows))
            self.assertTrue(all(row["ml_route"]["effective_route"] == row["heuristic_route"] for row in report_triage_rows))

            self.assertEqual(
                Path(base_summary["llm_small_path"]).read_text(encoding="utf-8"),
                Path(report_summary["llm_small_path"]).read_text(encoding="utf-8"),
            )
            self.assertEqual(
                Path(base_summary["llm_large_path"]).read_text(encoding="utf-8"),
                Path(report_summary["llm_large_path"]).read_text(encoding="utf-8"),
            )

    def test_build_triage_full_mode_still_applies_budgets(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            out_dir = root / "out_full"
            model_path = root / "route_model.pkl"

            self._write_jsonl(
                chunks_path,
                [
                    {
                        "chunk_id": "a:0-0",
                        "file_id": "a",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "warrant affidavit witness statement",
                    },
                    {
                        "chunk_id": "a:1-1",
                        "file_id": "a",
                        "page_start": 1,
                        "page_end": 1,
                        "text": "incident timeline interview notes",
                    },
                ],
            )

            # Deterministic model that strongly prefers llm_small.
            fake_model = {
                "model_type": "multinomial_nb",
                "classes": ["skip", "llm_small", "llm_large"],
                "vocab": {},
                "log_priors": {"skip": -10.0, "llm_small": 0.0, "llm_large": -10.0},
                "log_unknown_probs": {"skip": -1.0, "llm_small": -1.0, "llm_large": -1.0},
                "log_token_probs": {"skip": [], "llm_small": [], "llm_large": []},
                "alpha": 1.0,
                "token_regex": r"[A-Za-z0-9_]+",
            }
            with model_path.open("wb") as handle:
                pickle.dump(fake_model, handle, protocol=pickle.HIGHEST_PROTOCOL)

            summary = build_triage(
                chunks_path=chunks_path,
                output_dir=out_dir,
                ml_route_mode="full",
                ml_route_model_path=model_path,
                max_llm_chunks=1,
            )

            self.assertTrue(summary["ml_model_loaded"])
            self.assertEqual(summary["ml_route_mode"], "full")
            self.assertEqual(summary["llm_selected_total"], 1)
            self.assertEqual(summary["llm_budget_skipped_total"], 1)

            triage_rows = [
                json.loads(line)
                for line in Path(summary["triage_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(triage_rows), 2)
            self.assertTrue(all(row["ml_route"]["mode"] == "full" for row in triage_rows))
            self.assertTrue(all(row["route"] in {"llm_small", "llm_large", "skip"} for row in triage_rows))

            selected_small = [
                json.loads(line)
                for line in Path(summary["llm_small_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            selected_large = [
                json.loads(line)
                for line in Path(summary["llm_large_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(selected_small) + len(selected_large), 1)

    def test_build_triage_shadow_mode_can_add_but_not_remove(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            out_dir = root / "out_shadow"
            model_path = root / "route_model.pkl"

            self._write_jsonl(
                chunks_path,
                [
                    {
                        "chunk_id": "a:0-0",
                        "file_id": "a",
                        "page_start": 0,
                        "page_end": 0,
                        "text": "warrant affidavit on 2025-01-02 at 10:35 PM demote",
                    },
                    {
                        "chunk_id": "a:1-1",
                        "file_id": "a",
                        "page_start": 1,
                        "page_end": 1,
                        "text": "promote",
                    },
                ],
            )

            # Model predicts skip for tokens containing "demote", llm_small for "promote".
            fake_model = {
                "model_type": "multinomial_nb",
                "classes": ["skip", "llm_small", "llm_large"],
                "vocab": {"promote": 0, "demote": 1},
                "log_priors": {"skip": 0.0, "llm_small": 0.0, "llm_large": -5.0},
                "log_unknown_probs": {"skip": -1.0, "llm_small": -1.0, "llm_large": -1.0},
                "log_token_probs": {
                    "skip": [-4.0, 0.0],
                    "llm_small": [0.0, -4.0],
                    "llm_large": [-4.0, -4.0],
                },
                "alpha": 1.0,
                "token_regex": r"[A-Za-z0-9_]+",
            }
            with model_path.open("wb") as handle:
                pickle.dump(fake_model, handle, protocol=pickle.HIGHEST_PROTOCOL)

            summary = build_triage(
                chunks_path=chunks_path,
                output_dir=out_dir,
                route_skip_threshold=0.2,
                ml_route_mode="shadow",
                ml_route_model_path=model_path,
            )
            self.assertEqual(summary["ml_route_mode"], "shadow")
            self.assertTrue(summary["ml_model_loaded"])

            triage_rows = [
                json.loads(line)
                for line in Path(summary["triage_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            by_id = {row["chunk_id"]: row for row in triage_rows}

            # Heuristic LLM chunk remains LLM even if model predicts skip.
            self.assertEqual(by_id["a:0-0"]["heuristic_route"], "llm_small")
            self.assertEqual(by_id["a:0-0"]["ml_route"]["predicted_route"], "skip")
            self.assertEqual(by_id["a:0-0"]["route"], "llm_small")
            self.assertEqual(by_id["a:0-0"]["ml_route"]["effective_route"], "llm_small")

            # Heuristic skip chunk can be added to LLM by model in shadow mode.
            self.assertEqual(by_id["a:1-1"]["heuristic_route"], "skip")
            self.assertEqual(by_id["a:1-1"]["ml_route"]["predicted_route"], "llm_small")
            self.assertEqual(by_id["a:1-1"]["route"], "llm_small")
            self.assertEqual(by_id["a:1-1"]["ml_route"]["effective_route"], "llm_small")

    def test_build_triage_routes_complex_narrative_to_large(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            chunks_path = root / "chunks.jsonl"
            out_dir = root / "out_narrative"
            lines = []
            for idx in range(1, 15):
                lines.append(
                    f"Detective Jane Doe interviewed Witness Person {idx} about the incident and noted details."
                )
            lines.append("At 09:45 PM on 2025-04-10 she documented the sequence of events for review.")
            narrative_text = "\n".join(lines)

            self._write_jsonl(
                chunks_path,
                [
                    {
                        "chunk_id": "a:0-0",
                        "file_id": "a",
                        "page_start": 0,
                        "page_end": 0,
                        "text": narrative_text,
                    }
                ],
            )

            summary = build_triage(
                chunks_path=chunks_path,
                output_dir=out_dir,
                route_large_threshold=0.99,
                route_skip_threshold=0.01,
            )

            triage_rows = [
                json.loads(line)
                for line in Path(summary["triage_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(triage_rows), 1)
            row = triage_rows[0]
            self.assertEqual(row["route"], "llm_large")
            self.assertEqual(row["heuristic_route"], "llm_large")
            self.assertTrue(row["route_hints"]["complex_narrative"])

            selected_large = [
                json.loads(line)
                for line in Path(summary["llm_large_path"]).read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(selected_large), 1)


if __name__ == "__main__":
    unittest.main()
