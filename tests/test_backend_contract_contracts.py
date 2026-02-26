import hashlib
import json
import unittest
from pathlib import Path

from query.contracts import (
    EvidenceRef,
    UNKNOWN_CASE_ID,
    build_artifact_id,
    build_chunk_id,
    build_file_id,
    build_product_path,
    normalize_case_id,
    normalize_stable_id,
)


class BackendContractsTest(unittest.TestCase):
    def _load_fixture(self, name: str):
        fixture_path = Path(__file__).parent / "fixtures" / "backend_contract" / name
        with fixture_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def test_build_file_id_matches_manifest_hash_rule(self):
        source_type = "zip"
        container_path = "archive/intake.zip"
        virtual_path = "zip://archive/intake.zip::nested/doc.pdf"
        expected = hashlib.sha256(
            f"{source_type}|{container_path}|{virtual_path}".encode("utf-8")
        ).hexdigest()
        self.assertEqual(build_file_id(source_type, container_path, virtual_path), expected)

    def test_build_chunk_id_and_product_paths(self):
        chunk_id = build_chunk_id("f" * 64, 2, 4)
        self.assertEqual(chunk_id, f"{'f' * 64}:2-4")
        self.assertEqual(build_product_path("case", None, None), f"case/{UNKNOWN_CASE_ID}")
        self.assertEqual(
            build_product_path("doc", "f" * 64, "CASE-2026_001"),
            f"case/CASE-2026_001/doc/{'f' * 64}",
        )
        self.assertEqual(
            build_product_path("chunk", chunk_id, "CASE-2026_001"),
            f"case/CASE-2026_001/chunk/{chunk_id}",
        )

    def test_build_artifact_id_is_deterministic(self):
        first = build_artifact_id("case_summary", "case", "CASE-2026_001", version="v1")
        second = build_artifact_id("case_summary", "case", "CASE-2026_001", version="v1")
        other = build_artifact_id("case_summary", "case", "CASE-2026_001", version="v2")
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("art_"))
        self.assertNotEqual(first, other)

    def test_normalize_case_id_and_stable_id_reject_forbidden_chars(self):
        self.assertEqual(normalize_case_id(" case-2026 / 001 "), "CASE-2026_001")
        self.assertEqual(normalize_case_id(""), UNKNOWN_CASE_ID)
        with self.assertRaises(ValueError):
            normalize_stable_id("bad/id", "scope_id")

    def test_evidence_ref_fixture_round_trip(self):
        payload = self._load_fixture("evidence_ref.json")
        evidence = EvidenceRef.from_dict(payload)
        self.assertEqual(evidence.quote, "Jane Doe approved the request on March 5, 2025.")
        serialized = evidence.to_dict()
        self.assertEqual(serialized["char_start"], 12)
        self.assertEqual(serialized["char_end"], 58)
        self.assertEqual(serialized["confidence"], 0.86)
        self.assertEqual(serialized["source_phase"], "llm.extract_events")

    def test_evidence_ref_validation(self):
        payload = self._load_fixture("evidence_ref.json")
        payload["confidence"] = 1.2
        with self.assertRaises(ValueError):
            EvidenceRef.from_dict(payload)

        payload = self._load_fixture("evidence_ref.json")
        payload["char_end"] = 5
        with self.assertRaises(ValueError):
            EvidenceRef.from_dict(payload)

        payload = self._load_fixture("evidence_ref.json")
        payload["prompt_hash"] = "NOT_A_HASH"
        with self.assertRaises(ValueError):
            EvidenceRef.from_dict(payload)


if __name__ == "__main__":
    unittest.main()
