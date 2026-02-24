import argparse
import json
import random
import re
from pathlib import Path
from typing import Any, Dict, Iterator, List

from file_parser.compress_io import open_text_reader, open_text_writer

EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
PHONE_RE = re.compile(r"\+?\d[\d().\-\s]{6,}\d")
MULTISPACE_RE = re.compile(r"\s+")
DIGIT_RE = re.compile(r"\d")


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Sample uncertain route-dataset rows for human labeling."
    )
    parser.add_argument(
        "--input",
        default="output/ml/route_dataset.jsonl",
        help="Route dataset JSONL input (default: output/ml/route_dataset.jsonl).",
    )
    parser.add_argument(
        "--output",
        default="output/ml/labels_queue.jsonl",
        help="Review queue JSONL output (default: output/ml/labels_queue.jsonl).",
    )
    parser.add_argument(
        "--low-score-min",
        type=float,
        default=0.05,
        help="Inclusive lower bound for low-score uncertainty band (default: 0.05).",
    )
    parser.add_argument(
        "--low-score-max",
        type=float,
        default=0.20,
        help="Inclusive upper bound for low-score uncertainty band (default: 0.20).",
    )
    parser.add_argument(
        "--high-score-min",
        type=float,
        default=0.65,
        help="Inclusive lower bound for high-score uncertainty band (default: 0.65).",
    )
    parser.add_argument(
        "--high-score-max",
        type=float,
        default=0.85,
        help="Inclusive upper bound for high-score uncertainty band (default: 0.85).",
    )
    parser.add_argument(
        "--max-per-band",
        type=int,
        default=100,
        help="Maximum sampled rows per uncertainty band (default: 100).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=17,
        help="Deterministic random seed for sampling (default: 17).",
    )
    parser.add_argument(
        "--preview-mode",
        choices=["redacted", "none"],
        default="redacted",
        help="Preview mode in output queue: redacted text snippet or none (default: redacted).",
    )
    parser.add_argument(
        "--preview-chars",
        type=int,
        default=120,
        help="Maximum preview characters when --preview-mode=redacted (default: 120).",
    )
    parser.add_argument(
        "--exclude-human-labeled",
        action="store_true",
        help="Skip rows where labels.label_source is human.",
    )
    return parser.parse_args()


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with open_text_reader(path) as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def _as_float(value: Any, default: Any = 0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _redact_preview(text: str, max_chars: int) -> str:
    if not isinstance(text, str) or max_chars <= 0:
        return ""
    preview = EMAIL_RE.sub("[email]", text)
    preview = PHONE_RE.sub("[phone]", preview)
    preview = DIGIT_RE.sub("0", preview)
    preview = MULTISPACE_RE.sub(" ", preview).strip()
    return preview[:max_chars]


def _sample_rows(rows: List[Dict[str, Any]], max_rows: int, seed: int) -> List[Dict[str, Any]]:
    if max_rows <= 0:
        return []
    if len(rows) <= max_rows:
        return list(rows)
    shuffled = list(rows)
    random.Random(seed).shuffle(shuffled)
    return shuffled[:max_rows]


def sample_route_labels(
    *,
    input_path: Path,
    output_path: Path,
    low_score_min: float = 0.05,
    low_score_max: float = 0.20,
    high_score_min: float = 0.65,
    high_score_max: float = 0.85,
    max_per_band: int = 100,
    seed: int = 17,
    preview_mode: str = "redacted",
    preview_chars: int = 120,
    exclude_human_labeled: bool = False,
) -> Dict[str, int]:
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    low_band: List[Dict[str, Any]] = []
    high_band: List[Dict[str, Any]] = []
    summary = {
        "rows_scanned": 0,
        "rows_missing_fields": 0,
        "rows_excluded_human": 0,
        "eligible_low_band": 0,
        "eligible_high_band": 0,
        "sampled_low_band": 0,
        "sampled_high_band": 0,
        "rows_written": 0,
    }

    for row in _iter_jsonl(input_path):
        summary["rows_scanned"] += 1
        chunk_id = row.get("chunk_id")
        text_hash = row.get("chunk_text_hash")
        triage = row.get("triage") if isinstance(row.get("triage"), dict) else {}
        score = _as_float(triage.get("score"), default=None)
        if not isinstance(chunk_id, str) or not chunk_id or not isinstance(text_hash, str) or score is None:
            summary["rows_missing_fields"] += 1
            continue

        labels = row.get("labels") if isinstance(row.get("labels"), dict) else {}
        if exclude_human_labeled and labels.get("label_source") == "human":
            summary["rows_excluded_human"] += 1
            continue

        base = {
            "chunk_id": chunk_id,
            "chunk_text_hash": text_hash,
            "file_id": row.get("file_id"),
            "triage_score": score,
            "suggested_label_route": labels.get("label_route"),
        }
        if preview_mode == "redacted":
            base["preview_redacted"] = _redact_preview(row.get("text") or "", preview_chars)

        if low_score_min <= score <= low_score_max:
            low_row = dict(base)
            low_row["uncertainty_band"] = "low"
            low_band.append(low_row)
            summary["eligible_low_band"] += 1
        if high_score_min <= score <= high_score_max:
            high_row = dict(base)
            high_row["uncertainty_band"] = "high"
            high_band.append(high_row)
            summary["eligible_high_band"] += 1

    low_sample = _sample_rows(low_band, max_per_band, seed)
    high_sample = _sample_rows(high_band, max_per_band, seed + 1)
    summary["sampled_low_band"] = len(low_sample)
    summary["sampled_high_band"] = len(high_sample)

    seen_chunk_ids = set()
    with open_text_writer(output_path) as handle:
        for row in [*low_sample, *high_sample]:
            chunk_id = row["chunk_id"]
            if chunk_id in seen_chunk_ids:
                continue
            seen_chunk_ids.add(chunk_id)
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
            summary["rows_written"] += 1
    return summary


def main():
    args = _parse_args()
    summary = sample_route_labels(
        input_path=Path(args.input),
        output_path=Path(args.output),
        low_score_min=float(args.low_score_min),
        low_score_max=float(args.low_score_max),
        high_score_min=float(args.high_score_min),
        high_score_max=float(args.high_score_max),
        max_per_band=int(args.max_per_band),
        seed=int(args.seed),
        preview_mode=str(args.preview_mode),
        preview_chars=int(args.preview_chars),
        exclude_human_labeled=bool(args.exclude_human_labeled),
    )
    print("Route label sampler summary")
    for key in sorted(summary.keys()):
        print(f"- {key}: {summary[key]}")


if __name__ == "__main__":
    main()
