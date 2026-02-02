import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from file_parser.manifest_builder import build_manifest
from file_parser.phase1_detect import build_phase1


def _build_text_pdf_bytes(text):
    if any(ch in text for ch in ("\\", "(", ")")):
        raise ValueError("Text contains unsupported characters for this fixture.")
    stream = f"BT /F1 24 Tf 72 120 Td ({text}) Tj ET\n"
    obj1 = "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    obj3 = (
        "3 0 obj\n"
        "<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        "/MediaBox [0 0 200 200] /Contents 5 0 R >>\n"
        "endobj\n"
    )
    obj4 = "4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    obj5 = (
        f"5 0 obj\n<< /Length {len(stream)} >>\nstream\n{stream}endstream\nendobj\n"
    )

    header = "%PDF-1.4\n"
    objects = [obj1, obj2, obj3, obj4, obj5]
    offsets = []
    current = len(header)
    for obj in objects:
        offsets.append(current)
        current += len(obj)
    xref_offset = current
    xref = ["xref\n0 6\n", "0000000000 65535 f \n"]
    for offset in offsets:
        xref.append(f"{offset:010d} 00000 n \n")
    trailer = (
        "trailer\n<< /Size 6 /Root 1 0 R >>\n"
        f"startxref\n{xref_offset}\n%%EOF\n"
    )
    pdf_bytes = (header + "".join(objects) + "".join(xref) + trailer).encode("ascii")
    return pdf_bytes


class Phase1DetectSmokeTest(unittest.TestCase):
    def test_detect_text_pdf_in_fs_and_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf_bytes = _build_text_pdf_bytes("A" * 80)
            (root / "example.pdf").write_bytes(pdf_bytes)

            zip_path = root / "dataset.zip"
            with zipfile.ZipFile(zip_path, "w") as zf:
                zf.writestr("inner.pdf", pdf_bytes)

            manifest_path = root / "manifest.jsonl"
            build_manifest(root, manifest_path, resume=False)

            output_path = root / "phase1.jsonl"
            build_phase1(manifest_path, root, output_path, resume=False)

            records = [
                json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            fs_class = [r for r in records if r["source_type"] == "fs"]
            zip_class = [r for r in records if r["source_type"] == "zip"]

            self.assertTrue(fs_class)
            self.assertTrue(zip_class)
            self.assertIn(fs_class[0]["classification"], {"text", "mixed"})
            self.assertIn(zip_class[0]["classification"], {"text", "mixed"})


if __name__ == "__main__":
    unittest.main()
