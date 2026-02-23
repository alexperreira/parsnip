import json
import pickle
import tempfile
import unittest
from pathlib import Path

from triage.train_route_model import OFFICIAL_ROUTE_LABELS, split_file_ids, train_route_model


class TrainRouteModelTest(unittest.TestCase):
    def _write_jsonl(self, path: Path, records):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True) + "\n")

    def _base_dataset(self):
        return [
            {
                "file_id": "f1",
                "chunk_id": "f1:0",
                "text": "blank divider page",
                "labels": {"label_route": "skip"},
                "triage": {"route": "skip", "token_est": 5},
            },
            {
                "file_id": "f2",
                "chunk_id": "f2:0",
                "text": "offense report with suspect and witness details",
                "labels": {"label_route": "llm_small"},
                "triage": {"route": "llm_small", "token_est": 12},
            },
            {
                "file_id": "f3",
                "chunk_id": "f3:0",
                "text": "dense legal boilerplate and appendix schedule",
                "labels": {"label_route": "llm_large"},
                "triage": {"route": "llm_large", "token_est": 16},
            },
            {
                "file_id": "f4",
                "chunk_id": "f4:0",
                "text": "empty separator",
                "labels": {"label_route": "skip"},
                "triage": {"route": "skip", "token_est": 4},
            },
            {
                "file_id": "f5",
                "chunk_id": "f5:0",
                "text": "incident date location witness name",
                "labels": {"label_route": "llm_small"},
                "triage": {"route": "llm_small", "token_est": 10},
            },
        ]

    def test_split_file_ids_is_deterministic(self):
        file_ids = ["z", "a", "a", "b", "c", "d"]
        train1, test1 = split_file_ids(file_ids, random_seed=7, test_fraction=0.4)
        train2, test2 = split_file_ids(file_ids, random_seed=7, test_fraction=0.4)
        self.assertEqual(train1, train2)
        self.assertEqual(test1, test2)
        self.assertTrue(set(train1).isdisjoint(set(test1)))
        self.assertEqual(sorted(set(file_ids)), sorted(train1 + test1))

    def test_label_encoding_is_stable(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset_path = root / "route_dataset.jsonl"
            model_path = root / "route_model.pkl"
            report_path = root / "route_eval.json"
            version_path = root / "model_version.json"

            rows = [row for row in self._base_dataset() if row["labels"]["label_route"] != "llm_large"]
            self._write_jsonl(dataset_path, rows)

            train_route_model(
                input_path=dataset_path,
                output_model_path=model_path,
                output_report_path=report_path,
                output_version_path=version_path,
                random_seed=11,
                test_fraction=0.4,
            )
            with model_path.open("rb") as handle:
                model = pickle.load(handle)
            self.assertEqual(model["classes"], list(OFFICIAL_ROUTE_LABELS))

    def test_report_schema_presence(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            dataset_path = root / "route_dataset.jsonl"
            model_path = root / "route_model.pkl"
            report_path = root / "route_eval.json"
            version_path = root / "model_version.json"
            self._write_jsonl(dataset_path, self._base_dataset())

            summary = train_route_model(
                input_path=dataset_path,
                output_model_path=model_path,
                output_report_path=report_path,
                output_version_path=version_path,
                random_seed=21,
                test_fraction=0.4,
            )
            self.assertEqual(summary["rows_train"] + summary["rows_test"], len(self._base_dataset()))

            report = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertIn("dataset", report)
            self.assertIn("split", report)
            self.assertIn("classes", report)
            self.assertIn("metrics", report)
            self.assertIn("model", report)

            metrics = report["metrics"]
            self.assertIn("confusion_matrix", metrics)
            self.assertIn("per_class", metrics)
            self.assertIn("llm_send_recall", metrics)
            self.assertIn("llm_workload", metrics)
            for actual in OFFICIAL_ROUTE_LABELS:
                self.assertIn(actual, metrics["confusion_matrix"])
                for predicted in OFFICIAL_ROUTE_LABELS:
                    self.assertIn(predicted, metrics["confusion_matrix"][actual])


if __name__ == "__main__":
    unittest.main()
