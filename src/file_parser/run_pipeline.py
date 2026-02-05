import argparse
from pathlib import Path


def _parse_args():
    parser = argparse.ArgumentParser(description="Run Phase 0-2 end-to-end.")
    parser.add_argument("--input", required=True, help="Input root to resolve files.")
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Output directory for pipeline artifacts.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume by skipping file_ids already in outputs.",
    )
    parser.add_argument(
        "--include-unknown",
        action="store_true",
        help="Include unknown classification files in Phase 2.",
    )
    parser.add_argument("--workers", type=int, default=1, help="Number of concurrent workers.")
    parser.add_argument(
        "--page-workers",
        type=int,
        default=1,
        help="Number of concurrent OCR workers per PDF.",
    )
    parser.add_argument(
        "--skip-low-signal-bytes",
        type=int,
        default=0,
        help="Skip OCR for pages with rendered PNG size <= N bytes (0 disables).",
    )
    parser.add_argument("--max-pages", type=int, default=None, help="Max pages to OCR.")
    parser.add_argument(
        "--page-timeout",
        type=int,
        default=120,
        help="Timeout per page OCR (seconds).",
    )
    parser.add_argument("--lang", default="eng", help="OCR language (tesseract).")
    parser.add_argument("--dpi", type=int, default=300, help="Render DPI.")
    parser.add_argument(
        "--text-dir",
        default=None,
        help="Optional directory for per-page text outputs.",
    )
    parser.add_argument(
        "--ordered",
        action="store_true",
        help="Write output in input order (forces single worker).",
    )
    parser.add_argument(
        "--progress-interval",
        type=int,
        default=0,
        help="Print progress every N seconds (0 disables).",
    )
    return parser.parse_args()


def _resolve_text_dir(text_dir, output_dir):
    if text_dir is None:
        return None
    path = Path(text_dir)
    if not path.is_absolute():
        path = output_dir / path
    return path.as_posix()


def _ensure_outputs(output_dir, resume):
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "manifest.jsonl"
    phase1_path = output_dir / "phase1.jsonl"
    phase2_path = output_dir / "phase2_ocr.jsonl"
    if not resume:
        for path in (manifest_path, phase1_path, phase2_path):
            if path.exists():
                raise SystemExit("Output path exists; use --resume to append safely.")
    return manifest_path, phase1_path, phase2_path


def main():
    args = _parse_args()
    from file_parser.manifest_builder import build_manifest, _print_summary as _print_manifest_summary
    from file_parser.phase1_detect import build_phase1, _print_summary as _print_phase1_summary
    from file_parser.phase1_report import summarize_phase1, _print_summary as _print_phase1_report
    from file_parser.phase2_ocr import build_phase2, _print_summary as _print_phase2_summary

    output_dir = Path(args.output_dir)
    manifest_path, phase1_path, phase2_path = _ensure_outputs(output_dir, args.resume)
    text_dir = _resolve_text_dir(args.text_dir, output_dir)

    manifest_summary = build_manifest(
        args.input,
        manifest_path,
        resume=args.resume,
        progress_interval=args.progress_interval,
    )
    _print_manifest_summary(manifest_summary)

    phase1_summary = build_phase1(
        manifest_path,
        args.input,
        phase1_path,
        resume=args.resume,
        progress_interval=args.progress_interval,
    )
    _print_phase1_summary(phase1_summary)

    report_summary = summarize_phase1(phase1_path)
    _print_phase1_report(report_summary)

    phase2_summary = build_phase2(
        args.input,
        phase1_path,
        phase2_path,
        resume=args.resume,
        include_unknown=args.include_unknown,
        engine="tesseract",
        lang=args.lang,
        dpi=args.dpi,
        max_pages=args.max_pages,
        text_dir=text_dir,
        page_timeout=args.page_timeout,
        page_workers=args.page_workers,
        skip_low_signal_bytes=args.skip_low_signal_bytes,
        workers=args.workers,
        ordered=args.ordered,
        progress_interval=args.progress_interval,
    )
    _print_phase2_summary(phase2_summary)


if __name__ == "__main__":
    main()
