import io
import unittest
from types import SimpleNamespace
from unittest import mock

from file_parser.pdf_page_signals import inspect_pdf_pages
from file_parser.pdf_page_signals import mixed_page_ocr_decision
from file_parser.pdf_page_signals import should_ocr_mixed_page


class _FakeXObject:
    def __init__(self, subtype):
        self._subtype = subtype

    def get_object(self):
        return {"/Subtype": self._subtype}


class _FakePage:
    def __init__(self, text, xobjects=None, raise_extract=False):
        self._text = text
        self._raise_extract = raise_extract
        resources = {}
        if xobjects is not None:
            resources["/XObject"] = xobjects
        self._resources = resources

    def extract_text(self):
        if self._raise_extract:
            raise ValueError("extract failed")
        return self._text

    def get(self, key, default=None):
        if key == "/Resources":
            return self._resources
        return default


class PdfPageSignalsTest(unittest.TestCase):
    def test_inspect_pdf_pages_collects_text_and_image_signals(self):
        pages = [
            _FakePage("hello world", {"Im0": _FakeXObject("/Image")}),
            _FakePage("abc", {"Form0": _FakeXObject("/Form")}),
            _FakePage(None, None, raise_extract=True),
        ]
        reader = SimpleNamespace(pages=pages)
        with mock.patch("file_parser.pdf_page_signals.PdfReader", return_value=reader):
            signals = inspect_pdf_pages(io.BytesIO(b"dummy"))

        self.assertEqual(len(signals), 3)
        self.assertEqual(signals[0]["page_index"], 0)
        self.assertEqual(signals[0]["text"], "hello world")
        self.assertEqual(signals[0]["text_char_count"], 11)
        self.assertTrue(signals[0]["has_image"])

        self.assertEqual(signals[1]["page_index"], 1)
        self.assertEqual(signals[1]["text_char_count"], 3)
        self.assertFalse(signals[1]["has_image"])

        self.assertEqual(signals[2]["text"], "")
        self.assertEqual(signals[2]["text_char_count"], 0)
        self.assertFalse(signals[2]["has_image"])

    def test_mixed_page_ocr_decision_thresholds(self):
        self.assertEqual(
            mixed_page_ocr_decision(50, True, text_page_min_chars=50, low_text_max_chars=10),
            ("skip_pdf_text", "text_page_threshold_met"),
        )
        self.assertEqual(
            mixed_page_ocr_decision(5, False, text_page_min_chars=50, low_text_max_chars=10),
            ("skip_no_image", "no_images_detected"),
        )
        self.assertEqual(
            mixed_page_ocr_decision(10, True, text_page_min_chars=50, low_text_max_chars=10),
            ("ocr", "image_with_low_text"),
        )
        self.assertEqual(
            mixed_page_ocr_decision(20, True, text_page_min_chars=50, low_text_max_chars=10),
            ("skip_pdf_text", "prefer_embedded_text"),
        )

    def test_should_ocr_mixed_page(self):
        self.assertTrue(should_ocr_mixed_page(0, True, text_page_min_chars=50, low_text_max_chars=10))
        self.assertFalse(should_ocr_mixed_page(12, True, text_page_min_chars=50, low_text_max_chars=10))
        self.assertFalse(should_ocr_mixed_page(0, False, text_page_min_chars=50, low_text_max_chars=10))


if __name__ == "__main__":
    unittest.main()
