import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from file_parser.phase2_ocr import build_phase2


class Phase2SkeletonTest(unittest.TestCase):
    def test_phase2_filters_scanned_and_mixed(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            records = [
                {"file_id": "a", "ext": "pdf", "classification": "scanned"},
                {"file_id": "b", "ext": "pdf", "classification": "mixed"},
                {"file_id": "c", "ext": "pdf", "classification": "text"},
                {"file_id": "d", "ext": "pdf", "classification": "unknown"},
            ]
            phase1_path.write_text(
                "\n".join(json.dumps(r) for r in records) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            summary = build_phase2(
                root,
                phase1_path,
                output_path,
                resume=False,
                engine="noop",
            )
            self.assertEqual(summary["written"], 2)

            output_records = [
                json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            ids = {record["file_id"] for record in output_records}
            self.assertEqual(ids, {"a", "b"})

    def test_phase2_resume(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            phase1_path.write_text(
                json.dumps({"file_id": "a", "ext": "pdf", "classification": "scanned"}) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            build_phase2(root, phase1_path, output_path, resume=False, engine="noop")
            summary = build_phase2(root, phase1_path, output_path, resume=True, engine="noop")
            self.assertEqual(summary["written"], 0)
            self.assertEqual(summary["skipped"], 1)

    def test_phase2_workers_with_noop_engine(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            records = [
                {"file_id": "a", "ext": "pdf", "classification": "scanned"},
                {"file_id": "b", "ext": "pdf", "classification": "mixed"},
            ]
            phase1_path.write_text(
                "\n".join(json.dumps(r) for r in records) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            summary = build_phase2(
                root,
                phase1_path,
                output_path,
                resume=False,
                engine="noop",
                workers=2,
            )
            self.assertEqual(summary["written"], 2)

    def test_phase2_ordered_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            records = [
                {"file_id": "a", "ext": "pdf", "classification": "scanned"},
                {"file_id": "b", "ext": "pdf", "classification": "mixed"},
                {"file_id": "c", "ext": "pdf", "classification": "scanned"},
            ]
            phase1_path.write_text(
                "\n".join(json.dumps(r) for r in records) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            build_phase2(
                root,
                phase1_path,
                output_path,
                resume=False,
                engine="noop",
                workers=2,
                ordered=True,
            )

            output_records = [
                json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual([r["file_id"] for r in output_records], ["a", "b", "c"])

    def test_phase2_page_workers_cap_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            phase1_path.write_text(
                json.dumps({"file_id": "a", "ext": "pdf", "classification": "scanned"}) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"
            buffer = io.StringIO()
            with mock.patch("file_parser.phase2_ocr.os.cpu_count", return_value=4):
                with contextlib.redirect_stdout(buffer):
                    build_phase2(
                        root,
                        phase1_path,
                        output_path,
                        resume=False,
                        engine="noop",
                        workers=2,
                        page_workers=4,
                    )
            output = buffer.getvalue()
            self.assertIn("capping page_workers", output)

    def test_phase2_worker_oversubscription_warning(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            phase1_path = root / "phase1.jsonl"
            phase1_path.write_text(
                json.dumps({"file_id": "a", "ext": "pdf", "classification": "scanned"}) + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"
            buffer = io.StringIO()
            with mock.patch("file_parser.phase2_ocr.os.cpu_count", return_value=1):
                with contextlib.redirect_stdout(buffer):
                    build_phase2(
                        root,
                        phase1_path,
                        output_path,
                        resume=False,
                        engine="noop",
                        workers=2,
                        page_workers=1,
                    )
            output = buffer.getvalue()
            self.assertIn("exceeds CPU count", output)

    def test_phase2_skips_low_signal_pages(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf_path = root / "doc.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")
            phase1_path = root / "phase1.jsonl"
            phase1_path.write_text(
                json.dumps(
                    {
                        "file_id": "a",
                        "ext": "pdf",
                        "classification": "scanned",
                        "source_type": "fs",
                        "virtual_path": "doc.pdf",
                        "page_count": 1,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"

            def _fake_render(temp_pdf, output_dir, dpi, start_page, end_page):
                render_prefix = Path(output_dir) / "page"
                for page_number in range(start_page, end_page + 1):
                    image_path = f"{render_prefix}-{page_number}.png"
                    Path(image_path).write_bytes(b"tiny")
                return render_prefix

            with mock.patch("file_parser.phase2_ocr._ensure_engine_dependencies", return_value=True):
                with mock.patch("file_parser.phase2_ocr._render_pdf_page_range", side_effect=_fake_render):
                    with mock.patch("file_parser.phase2_ocr.subprocess.run") as run_mock:
                        summary = build_phase2(
                            root,
                            phase1_path,
                            output_path,
                            resume=False,
                            engine="tesseract",
                            page_workers=1,
                            skip_low_signal_bytes=10,
                        )
                        self.assertEqual(summary["written"], 1)
                        run_mock.assert_not_called()

            output_records = [
                json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(output_records[0]["pages"][0]["errors"], "SkippedLowSignal")

    def test_phase2_mixed_image_heavy_routes_only_low_text_pages_to_ocr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            pdf_path = root / "doc.pdf"
            pdf_path.write_bytes(b"%PDF-1.4\n%fake\n")
            phase1_path = root / "phase1.jsonl"
            phase1_path.write_text(
                json.dumps(
                    {
                        "file_id": "mixed_doc",
                        "ext": "pdf",
                        "classification": "mixed",
                        "source_type": "fs",
                        "virtual_path": "doc.pdf",
                        "page_count": 2,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            output_path = root / "phase2.jsonl"
            render_calls = []

            def _fake_render(temp_pdf, output_dir, dpi, start_page, end_page):
                render_calls.append((start_page, end_page))
                render_prefix = Path(output_dir) / "page"
                for page_number in range(start_page, end_page + 1):
                    image_path = f"{render_prefix}-{page_number}.png"
                    Path(image_path).write_bytes(b"image")
                return render_prefix

            fake_signals = [
                {
                    "page_index": 0,
                    "text": "x" * 80,
                    "text_char_count": 80,
                    "has_image": True,
                },
                {
                    "page_index": 1,
                    "text": "",
                    "text_char_count": 0,
                    "has_image": True,
                },
            ]

            with mock.patch("file_parser.phase2_ocr._ensure_engine_dependencies", return_value=True):
                with mock.patch("file_parser.phase2_ocr.inspect_pdf_pages", return_value=fake_signals):
                    with mock.patch("file_parser.phase2_ocr._render_pdf_page_range", side_effect=_fake_render):
                        with mock.patch(
                            "file_parser.phase2_ocr.subprocess.run",
                            return_value=mock.Mock(stdout=b"ocr text", stderr=b""),
                        ) as run_mock:
                            summary = build_phase2(
                                root,
                                phase1_path,
                                output_path,
                                resume=False,
                                engine="tesseract",
                                mixed_ocr_mode="image-heavy",
                                page_workers=1,
                            )
                            self.assertEqual(summary["written"], 1)
                            self.assertEqual(run_mock.call_count, 2)

            self.assertEqual(render_calls, [(1, 2)])
            output_records = [
                json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()
            ]
            pages = output_records[0]["pages"]
            self.assertEqual(len(pages), 2)

            self.assertEqual(pages[0]["page_index"], 0)
            self.assertEqual(pages[0]["ocr_decision"], "ocr")
            self.assertEqual(pages[0]["ocr_reason"], "image_detected_trigger")
            self.assertEqual(pages[0]["signal_text_chars"], 80)
            self.assertTrue(pages[0]["signal_has_image"])
            self.assertEqual(pages[0]["text"], "ocr text")

            self.assertEqual(pages[1]["page_index"], 1)
            self.assertEqual(pages[1]["ocr_decision"], "ocr")
            self.assertEqual(pages[1]["ocr_reason"], "image_with_low_text")
            self.assertEqual(pages[1]["signal_text_chars"], 0)
            self.assertTrue(pages[1]["signal_has_image"])
            self.assertEqual(pages[1]["text"], "ocr text")


if __name__ == "__main__":
    unittest.main()
