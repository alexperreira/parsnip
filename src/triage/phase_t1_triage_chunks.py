import argparse
import json
import pickle
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
from triage.train_route_model import OFFICIAL_ROUTE_LABELS, load_route_model, predict_route_probabilities


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
    parser.add_argument(
        "--ml-route-model",
        default=None,
        help="Optional trained route model artifact (.pkl). If unavailable, triage falls back to heuristic routing.",
    )
    parser.add_argument(
        "--ml-route-mode",
        default="off",
        choices=["off", "report-only", "shadow", "full"],
        help=(
            "ML routing mode: off (heuristic only), report-only (predict only), "
            "shadow (add-to-LLM only), full (route from model policy)."
        ),
    )
    parser.add_argument(
        "--ml-route-skip-threshold",
        type=float,
        default=0.90,
        help="Only allow model policy skip when P(skip) >= threshold (default: 0.90).",
    )
    parser.add_argument(
        "--ml-route-large-threshold",
        type=float,
        default=0.80,
        help="Allow model policy llm_large when P(llm_large) >= threshold (default: 0.80).",
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


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_low_quality_from_features(features: Dict[str, Any]) -> bool:
    tq = features.get("text_quality") or {}
    char_len = _as_int(tq.get("char_len"))
    non_ws_ratio = tq.get("non_ws_ratio")
    punctuation_ratio = tq.get("punctuation_ratio")
    max_repeat = _as_int(tq.get("max_repeated_char_run"))
    if char_len and char_len < 40:
        return True
    if isinstance(non_ws_ratio, (int, float)) and non_ws_ratio < 0.05:
        return True
    if isinstance(punctuation_ratio, (int, float)) and punctuation_ratio > 0.40:
        return True
    if max_repeat >= 10:
        return True
    return False


def _is_complex_narrative_from_features(features: Dict[str, Any]) -> bool:
    tq = features.get("text_quality") or {}
    struct = features.get("structure") or {}
    entity = features.get("entity_hints") or {}
    event = features.get("event_hints") or {}

    char_len = _as_int(tq.get("char_len"))
    nonempty_lines = _as_int(struct.get("nonempty_line_count"))
    dialogue_markers = _as_int(struct.get("dialogue_marker_count"))
    bullet_density = struct.get("bullet_density")
    table_like = bool(struct.get("table_like"))
    name_like = _as_int(entity.get("capitalized_name_count"))
    date_like = _as_int(event.get("date_like_count"))
    incident_verbs = _as_int(event.get("incident_verb_count"))
    time_like = _as_int(event.get("time_like_count"))

    if char_len < 800:
        return False
    if nonempty_lines < 8:
        return False
    if table_like:
        return False
    if isinstance(bullet_density, (int, float)) and bullet_density >= 0.25:
        return False

    narrative_signal_total = dialogue_markers + name_like + date_like + incident_verbs + time_like
    return narrative_signal_total >= 4


def _route_from_model_policy(
    *,
    probabilities: Dict[str, float],
    skip_threshold: float,
    large_threshold: float,
    low_quality: bool,
    complex_narrative: bool,
    heuristic_score: float,
    heuristic_large_threshold: float,
) -> Tuple[str, List[str]]:
    gates: List[str] = []
    skip_prob = float(probabilities.get("skip", 0.0))
    large_prob = float(probabilities.get("llm_large", 0.0))
    if skip_prob >= skip_threshold:
        gates.append("p_skip_high")
        return "skip", gates
    if large_prob >= large_threshold:
        gates.append("p_large_high")
        return "llm_large", gates
    if complex_narrative:
        gates.append("complex_narrative")
        return "llm_large", gates
    if low_quality and heuristic_score >= heuristic_large_threshold:
        gates.append("low_quality_high_heuristic_score")
        return "llm_large", gates
    gates.append("default_llm_small")
    return "llm_small", gates


def _effective_route_for_mode(
    *,
    heuristic_route: str,
    model_route: str,
    ml_route_mode: str,
) -> Tuple[str, str]:
    if ml_route_mode == "report-only":
        return heuristic_route, "report_only_keep_heuristic"
    if ml_route_mode == "shadow":
        if heuristic_route == "skip" and model_route in {"llm_small", "llm_large"}:
            return model_route, "shadow_add_from_skip"
        return heuristic_route, "shadow_keep_heuristic"
    return model_route, "full_policy_route"


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
    ml_route_model_path: Optional[Path] = None,
    ml_route_mode: str = "off",
    ml_route_skip_threshold: float = 0.90,
    ml_route_large_threshold: float = 0.80,
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

    ml_model = None
    ml_model_loaded = False
    ml_model_status = "disabled"
    if ml_route_mode != "off":
        if ml_route_model_path is None:
            ml_model_status = "missing_model_path"
        else:
            try:
                ml_model = load_route_model(ml_route_model_path)
                ml_model_loaded = True
                ml_model_status = "loaded"
            except FileNotFoundError:
                ml_model_status = "model_not_found"
            except (ValueError, pickle.UnpicklingError):
                ml_model_status = "model_invalid"
            except Exception:
                ml_model_status = "model_load_error"

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
        "ml_predictions_total": 0,
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
            low_quality = _is_low_quality_from_features(features)
            complex_narrative = _is_complex_narrative_from_features(features)

            route = "llm_small"
            if score < route_skip_threshold:
                route = "skip"
            elif score >= route_large_threshold or complex_narrative:
                route = "llm_large"
            heuristic_route = route

            ml_summary = None
            if ml_route_mode != "off":
                if ml_model_loaded and ml_model is not None:
                    probs = predict_route_probabilities(ml_model, text)
                    probs = {label: float(probs.get(label, 0.0)) for label in OFFICIAL_ROUTE_LABELS}
                    model_route, policy_gates = _route_from_model_policy(
                        probabilities=probs,
                        skip_threshold=ml_route_skip_threshold,
                        large_threshold=ml_route_large_threshold,
                        low_quality=low_quality,
                        complex_narrative=complex_narrative,
                        heuristic_score=score,
                        heuristic_large_threshold=route_large_threshold,
                    )
                    counts["ml_predictions_total"] += 1
                    effective_route, mode_gate = _effective_route_for_mode(
                        heuristic_route=heuristic_route,
                        model_route=model_route,
                        ml_route_mode=ml_route_mode,
                    )
                    route = effective_route
                    ml_summary = {
                        "mode": ml_route_mode,
                        "model_loaded": True,
                        "predicted_route": model_route,
                        "effective_route": effective_route,
                        "probabilities": probs,
                        "policy_gates": [*policy_gates, mode_gate],
                    }
                else:
                    ml_summary = {
                        "mode": ml_route_mode,
                        "model_loaded": False,
                        "predicted_route": None,
                        "effective_route": heuristic_route,
                        "probabilities": None,
                        "policy_gates": ["fallback_heuristic"],
                        "fallback_reason": ml_model_status,
                    }

            triage_record = {
                "file_id": file_id,
                "chunk_id": chunk_id,
                "page_range": [record.get("page_start"), record.get("page_end")],
                "score": score,
                "route": route,
                "heuristic_route": heuristic_route,
                "token_est": token_est,
                "features": features,
                "route_hints": {
                    "low_quality": low_quality,
                    "complex_narrative": complex_narrative,
                },
            }
            if ml_summary is not None:
                triage_record["ml_route"] = ml_summary
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
        "ml_route_mode": ml_route_mode,
        "ml_model_loaded": ml_model_loaded,
        "ml_model_status": ml_model_status,
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
        ml_route_model_path=Path(args.ml_route_model) if args.ml_route_model else None,
        ml_route_mode=str(args.ml_route_mode),
        ml_route_skip_threshold=float(args.ml_route_skip_threshold),
        ml_route_large_threshold=float(args.ml_route_large_threshold),
    )
    print("Triage summary")
    for key in sorted(summary.keys()):
        print(f"- {key}: {summary[key]}")


if __name__ == "__main__":
    main()
