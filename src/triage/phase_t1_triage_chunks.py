import argparse
import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from file_parser.compress_io import open_text_reader, open_text_writer
from triage.lightweight_signals import (
    DEFAULT_KEYWORD_PACKS,
    compile_keyword_packs,
    compute_lightweight_signals,
    load_keyword_packs_from_dir,
)
from triage.scoring import estimate_tokens, score_from_features, select_under_budgets


def _parse_args():
    parser = argparse.ArgumentParser(description="Phase T1 triage over chunks.jsonl.")
    parser.add_argument(
        "--input",
        default="output/text/chunks.jsonl",
        help="Input chunks JSONL path (default: output/text/chunks.jsonl).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Output directory for triage artifacts (default: output).",
    )
    parser.add_argument(
        "--keyword-packs-dir",
        default=None,
        help="Optional directory of keyword pack .txt files (adds/overrides defaults).",
    )
    parser.add_argument(
        "--max-llm-chunks",
        type=int,
        default=None,
        help="Cap total chunks selected for LLM.",
    )
    parser.add_argument(
        "--max-llm-chunks-per-file",
        type=int,
        default=None,
        help="Cap selected chunks per file_id.",
    )
    parser.add_argument(
        "--max-llm-tokens",
        type=int,
        default=None,
        help="Cap estimated tokens selected for LLM.",
    )
    parser.add_argument(
        "--allow-file-ids",
        default=None,
        help="Optional newline-delimited file_id allowlist path.",
    )
    parser.add_argument(
        "--deny-file-ids",
        default=None,
        help="Optional newline-delimited file_id denylist path.",
    )
    parser.add_argument(
        "--ner",
        action="store_true",
        help="Enable optional local NER (spaCy). Off by default.",
    )
    parser.add_argument(
        "--ner-model",
        default="en_core_web_sm",
        help="spaCy model name for --ner (default: en_core_web_sm).",
    )
    parser.add_argument(
        "--route-large-threshold",
        type=float,
        default=0.75,
        help="Score threshold for llm_large (default: 0.75).",
    )
    parser.add_argument(
        "--route-skip-threshold",
        type=float,
        default=0.10,
        help="Score threshold below which a chunk is routed to skip (default: 0.10).",
    )
    parser.add_argument(
        "--small-output",
        default="chunks.llm_small.jsonl",
        help="Filename for llm_small chunk stream under --output-dir (default: chunks.llm_small.jsonl).",
    )
    parser.add_argument(
        "--large-output",
        default="chunks.llm_large.jsonl",
        help="Filename for llm_large chunk stream under --output-dir (default: chunks.llm_large.jsonl).",
    )
    return parser.parse_args()


def _iter_jsonl(path: Path):
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


def _load_id_set(path: Optional[str]) -> Optional[set]:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"List not found: {p}")
    ids = set()
    with p.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            ids.add(line)
    return ids or None


def _merge_keyword_packs(defaults: Dict[str, List[str]], overrides: Optional[Dict[str, List[str]]]):
    if not overrides:
        return dict(defaults)
    merged: Dict[str, List[str]] = dict(defaults)
    for pack, keywords in overrides.items():
        merged[pack] = list(keywords)
    return merged


def build_triage(
    chunks_path: Path,
    output_dir: Path,
    *,
    keyword_packs_dir: Optional[str] = None,
    max_llm_chunks: Optional[int] = None,
    max_llm_chunks_per_file: Optional[int] = None,
    max_llm_tokens: Optional[int] = None,
    allow_file_ids: Optional[Iterable[str]] = None,
    deny_file_ids: Optional[Iterable[str]] = None,
    ner_enabled: bool = False,
    ner_model: str = "en_core_web_sm",
    route_large_threshold: float = 0.75,
    route_skip_threshold: float = 0.10,
    small_output_name: str = "chunks.llm_small.jsonl",
    large_output_name: str = "chunks.llm_large.jsonl",
) -> Dict[str, Any]:
    if not chunks_path.exists():
        raise SystemExit(f"Input not found: {chunks_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    triage_path = output_dir / "triage.jsonl"
    small_path = output_dir / small_output_name
    large_path = output_dir / large_output_name

    packs_overrides = None
    if keyword_packs_dir:
        packs_overrides = load_keyword_packs_from_dir(keyword_packs_dir)
    keyword_packs = _merge_keyword_packs(DEFAULT_KEYWORD_PACKS, packs_overrides)
    compiled = compile_keyword_packs(keyword_packs)

    # First pass: compute features + scores.
    started = time.monotonic()
    enriched_chunks: List[Dict[str, Any]] = []
    counts = {
        "records_total": 0,
        "chunks_total": 0,
        "chunks_scored": 0,
        "missing_ids": 0,
        "json_decode_errors": 0,
        "triage_written": 0,
    }

    with open_text_writer(triage_path) as triage_handle:
        for record in _iter_jsonl(chunks_path):
            counts["records_total"] += 1
            chunk_id = record.get("chunk_id")
            file_id = record.get("file_id")
            if not isinstance(chunk_id, str) or not isinstance(file_id, str) or not chunk_id or not file_id:
                counts["missing_ids"] += 1
                continue
            counts["chunks_total"] += 1
            text = record.get("text") or ""
            features = compute_lightweight_signals(
                text,
                compiled_keyword_packs=compiled,
                ner_enabled=ner_enabled,
                ner_model=ner_model,
                ner_include_spans=False,
            )
            score = score_from_features(features)
            token_est = estimate_tokens(text)

            route = "llm_small"
            if score < route_skip_threshold:
                route = "skip"
            elif score >= route_large_threshold:
                route = "llm_large"

            triage_record = {
                "file_id": file_id,
                "chunk_id": chunk_id,
                "page_range": [record.get("page_start"), record.get("page_end")],
                "score": score,
                "route": route,
                "token_est": token_est,
                "features": features,
            }
            triage_handle.write(json.dumps(triage_record, ensure_ascii=True) + "\n")
            counts["triage_written"] += 1
            counts["chunks_scored"] += 1

            # Keep a minimal envelope needed for budget selection + later emission.
            enriched_chunks.append(
                {
                    "chunk_id": chunk_id,
                    "file_id": file_id,
                    "text": text,
                    "record": record,
                    "route": route,
                    "score": score,
                    "features": features,
                }
            )

    # Second pass: apply budgets to LLM-eligible chunks.
    llm_candidates = [c for c in enriched_chunks if c["route"] in ("llm_small", "llm_large")]
    selection_input = [
        {"chunk_id": c["chunk_id"], "file_id": c["file_id"], "text": c["text"], "features": c["features"]}
        for c in llm_candidates
    ]
    decision = select_under_budgets(
        selection_input,
        max_llm_chunks=max_llm_chunks,
        max_llm_chunks_per_file=max_llm_chunks_per_file,
        max_llm_tokens=max_llm_tokens,
        allow_file_ids=allow_file_ids,
        deny_file_ids=deny_file_ids,
    )
    selected = set(decision.selected_chunk_ids)

    written_small = 0
    written_large = 0
    budget_skipped = 0
    with open_text_writer(small_path) as small_handle, open_text_writer(large_path) as large_handle:
        for item in llm_candidates:
            if item["chunk_id"] not in selected:
                budget_skipped += 1
                continue
            if item["route"] == "llm_large":
                large_handle.write(json.dumps(item["record"], ensure_ascii=True) + "\n")
                written_large += 1
            else:
                small_handle.write(json.dumps(item["record"], ensure_ascii=True) + "\n")
                written_small += 1

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return {
        **counts,
        "triage_path": triage_path.as_posix(),
        "llm_small_path": small_path.as_posix(),
        "llm_large_path": large_path.as_posix(),
        "llm_selected_total": decision.selected_total,
        "llm_selected_tokens_est": decision.selected_tokens_est,
        "llm_budget_skipped_total": budget_skipped,
        "elapsed_ms": elapsed_ms,
    }


def main():
    args = _parse_args()
    chunks_path = Path(args.input)
    output_dir = Path(args.output_dir)
    allow = _load_id_set(args.allow_file_ids)
    deny = _load_id_set(args.deny_file_ids)
    summary = build_triage(
        chunks_path=chunks_path,
        output_dir=output_dir,
        keyword_packs_dir=args.keyword_packs_dir,
        max_llm_chunks=args.max_llm_chunks,
        max_llm_chunks_per_file=args.max_llm_chunks_per_file,
        max_llm_tokens=args.max_llm_tokens,
        allow_file_ids=allow,
        deny_file_ids=deny,
        ner_enabled=bool(args.ner),
        ner_model=str(args.ner_model),
        route_large_threshold=float(args.route_large_threshold),
        route_skip_threshold=float(args.route_skip_threshold),
        small_output_name=str(args.small_output),
        large_output_name=str(args.large_output),
    )
    print("Triage summary")
    for key in sorted(summary.keys()):
        print(f"- {key}: {summary[key]}")


if __name__ == "__main__":
    main()

