import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from file_parser.manifest_builder import build_manifest


class ManifestSmokeTest(unittest.TestCase):
    def test_manifest_includes_fs_and_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            (root / "example.pdf").write_bytes(b"%PDF-1.4 dummy")

            zip_path = root / "dataset.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("inner.pdf", b"%PDF-1.4 inner")

            output_path = root / "manifest.jsonl"
            build_manifest(root, output_path, resume=False)

            records = [
                json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            source_types = {record["source_type"] for record in records}

            self.assertIn("fs", source_types)
            self.assertIn("zip", source_types)


if __name__ == "__main__":
    unittest.main()
