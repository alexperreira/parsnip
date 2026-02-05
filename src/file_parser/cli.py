import argparse
import sys

from file_parser.manifest_builder import main as manifest_main
from file_parser.phase1_detect import main as phase1_main
from file_parser.phase1_report import main as report_main
from file_parser.phase2_ocr import main as phase2_main
from file_parser.run_pipeline import main as pipeline_main
from text_extraction.phase3_extract_text import main as phase3_main


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="fileparse",
        description="File parsing pipeline CLI.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("pipeline", help="Run Phase 0-2 end-to-end.")
    subparsers.add_parser("manifest", help="Build a PDF manifest (Phase 0).")
    subparsers.add_parser("phase1", help="Classify PDFs as text/scanned/mixed/unknown.")
    subparsers.add_parser("report", help="Summarize Phase 1 results.")
    subparsers.add_parser("phase2", help="Run OCR for scanned/mixed PDFs.")
    subparsers.add_parser("phase3", help="Run unified text extraction.")

    return parser.parse_known_args(argv)


def main(argv=None):
    args, remainder = _parse_args(argv)
    dispatch = {
        "pipeline": pipeline_main,
        "manifest": manifest_main,
        "phase1": phase1_main,
        "report": report_main,
        "phase2": phase2_main,
        "phase3": phase3_main,
    }
    handler = dispatch.get(args.command)
    if handler is None:
        raise SystemExit(f"Unknown command: {args.command}")

    prior_argv = sys.argv
    sys.argv = [f"fileparse {args.command}", *remainder]
    try:
        return handler()
    finally:
        sys.argv = prior_argv


if __name__ == "__main__":
    main()
