import argparse
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional, Tuple

from file_parser.compress_io import open_text_reader, open_text_writer
from llm.cache import chunk_text_hash


def _parse_args():
    parser = argparse.ArgumentParser(description="Build an ML routing dataset (route classification).")
    parser.add_argument(
        "--chunks",
        default="output/text/chunks.jsonl",
        help="Input chunks JSONL path (default: output/text/chunks.jsonl).",
    )
    parser.add_argument(
        "--triage",
        default="output/triage.jsonl",
        help="Input triage JSONL path (default: output/triage.jsonl).",
    )
    parser.add_argument(
        "--entities",
        default="output/entities.jsonl",
        help="Entities output JSONL path (default: output/entities.jsonl).",
    )
    parser.add_argument(
        "--events",
        default="output/events.jsonl",
        help="Events output JSONL path (default: output/events.jsonl).",
    )
    parser.add_argument(
        "--conversations",
        default="output/conversations.jsonl",
        help="Conversations output JSONL path (default: output/conversations.jsonl).",
    )
    parser.add_argument(
        "--identity-signals",
        default="output/identity_signals.jsonl",
        help="Identity signals output JSONL path (default: output/identity_signals.jsonl).",
    )
    parser.add_argument(
        "--entities-large",
        default="output/entities.llm_large.jsonl",
        help="Optional llm_large entities JSONL path (default: output/entities.llm_large.jsonl).",
    )
    parser.add_argument(
        "--events-large",
        default="output/events.llm_large.jsonl",
        help="Optional llm_large events JSONL path (default: output/events.llm_large.jsonl).",
    )
    parser.add_argument(
        "--conversations-large",
        default="output/conversations.llm_large.jsonl",
        help="Optional llm_large conversations JSONL path (default: output/conversations.llm_large.jsonl).",
    )
    parser.add_argument(
        "--identity-signals-large",
        default="output/identity_signals.llm_large.jsonl",
        help="Optional llm_large identity signals JSONL path (default: output/identity_signals.llm_large.jsonl).",
    )
    parser.add_argument(
        "--output",
        default="output/ml/route_dataset.jsonl",
        help="Output dataset JSONL path (default: output/ml/route_dataset.jsonl).",
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="Optional SQLite path for downstream utility labels (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--labels",
        default=None,
        help="Optional human labels JSONL path (overrides heuristic labels).",
    )
    parser.add_argument(
        "--skip-threshold",
        type=float,
        default=0.10,
        help="Score threshold below which label_route defaults to skip when no yield is observed.",
    )
    parser.add_argument(
        "--large-threshold",
        type=float,
        default=0.75,
        help="Score threshold above which low-quality/no-yield chunks are labeled llm_large.",
    )
    parser.add_argument(
        "--include-features",
        action="store_true",
        help="Include triage features in the dataset (recommended).",
    )
    parser.add_argument(
        "--max-rows",
        type=int,
        default=None,
        help="Optional max rows written.",
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


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _is_low_quality_from_features(features: Mapping[str, Any]) -> bool:
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


@dataclass(frozen=True)
class LLMOutcome:
    items_count: int
    error: Optional[str]
    model: Optional[str]

    @property
    def yield_nonempty(self) -> bool:
        return self.error is None and self.items_count > 0


def _load_llm_outcomes(path: Path) -> Dict[str, LLMOutcome]:
    if not path.exists():
        return {}
    outcomes: Dict[str, LLMOutcome] = {}
    for record in _iter_jsonl(path):
        chunk_id = record.get("chunk_id")
        if not isinstance(chunk_id, str) or not chunk_id:
            continue
        items = record.get("items")
        items_count = len(items) if isinstance(items, list) else 0
        error = record.get("error")
        if error is not None and not isinstance(error, str):
            error = str(error)
        model = record.get("model")
        if model is not None and not isinstance(model, str):
            model = str(model)
        outcomes[chunk_id] = LLMOutcome(items_count=items_count, error=error, model=model)
    return outcomes


def _load_human_labels(path: Optional[Path]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    if not path:
        return {}
    if not path.exists():
        raise SystemExit(f"Labels not found: {path}")
    labels: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for record in _iter_jsonl(path):
        chunk_id = record.get("chunk_id")
        text_hash = record.get("chunk_text_hash")
        label_route = record.get("label_route")
        if not isinstance(chunk_id, str) or not isinstance(text_hash, str) or not isinstance(label_route, str):
            continue
        labels[(chunk_id, text_hash)] = record
    return labels


def _label_route(
    *,
    triage_score: float,
    any_yield: bool,
    low_quality: bool,
    skip_threshold: float,
    large_threshold: float,
) -> str:
    if any_yield:
        return "llm_small"
    if triage_score < skip_threshold:
        return "skip"
    if low_quality and triage_score >= large_threshold:
        return "llm_large"
    return "llm_small"


def _load_downstream_utility(db_path: Optional[Path]) -> Dict[str, Dict[str, int]]:
    if db_path is None or not db_path.exists():
        return {}

    utility_by_chunk: Dict[str, Dict[str, int]] = {}

    def _bump(chunk_id: str, key: str, value: Any):
        count = _as_int(value)
        if count <= 0:
            return
        bucket = utility_by_chunk.setdefault(chunk_id, {})
        bucket[key] = bucket.get(key, 0) + count

    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error:
        return {}

    try:
        table_names = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
            if isinstance(row[0], str)
        }

        if {"events", "event_times"}.issubset(table_names):
            cursor = conn.execute(
                "SELECT e.chunk_id, COUNT(*) "
                "FROM events e "
                "JOIN event_times t ON t.event_id = e.event_id "
                "WHERE t.status = 'ok' "
                "GROUP BY e.chunk_id"
            )
            for chunk_id, count in cursor:
                if isinstance(chunk_id, str) and chunk_id:
                    _bump(chunk_id, "timeline_ok_events", count)

        if {"person_observations", "person_cluster_members"}.issubset(table_names):
            cursor = conn.execute(
                "SELECT o.chunk_id, COUNT(DISTINCT o.obs_id) "
                "FROM person_observations o "
                "JOIN person_cluster_members m ON m.obs_id = o.obs_id "
                "GROUP BY o.chunk_id"
            )
            for chunk_id, count in cursor:
                if isinstance(chunk_id, str) and chunk_id:
                    _bump(chunk_id, "resolved_person_observations", count)
    except sqlite3.Error:
        return {}
    finally:
        conn.close()

    return utility_by_chunk


def build_route_dataset(
    *,
    chunks_path: Path,
    triage_path: Path,
    entities_path: Path,
    events_path: Path,
    conversations_path: Path,
    identity_signals_path: Path,
    entities_large_path: Optional[Path],
    events_large_path: Optional[Path],
    conversations_large_path: Optional[Path],
    identity_signals_large_path: Optional[Path],
    output_path: Path,
    db_path: Optional[Path] = None,
    labels_path: Optional[Path] = None,
    skip_threshold: float = 0.10,
    large_threshold: float = 0.75,
    include_features: bool = True,
    max_rows: Optional[int] = None,
) -> Dict[str, Any]:
    if not chunks_path.exists():
        raise SystemExit(f"Chunks not found: {chunks_path}")
    if not triage_path.exists():
        raise SystemExit(f"Triage not found: {triage_path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    triage_by_chunk: Dict[str, Dict[str, Any]] = {}
    for record in _iter_jsonl(triage_path):
        chunk_id = record.get("chunk_id")
        if isinstance(chunk_id, str) and chunk_id:
            triage_by_chunk[chunk_id] = record

    outcomes_entities = _load_llm_outcomes(entities_path)
    outcomes_events = _load_llm_outcomes(events_path)
    outcomes_conversations = _load_llm_outcomes(conversations_path)
    outcomes_identity = _load_llm_outcomes(identity_signals_path)
    outcomes_entities_large = _load_llm_outcomes(entities_large_path) if entities_large_path else {}
    outcomes_events_large = _load_llm_outcomes(events_large_path) if events_large_path else {}
    outcomes_conversations_large = (
        _load_llm_outcomes(conversations_large_path) if conversations_large_path else {}
    )
    outcomes_identity_large = (
        _load_llm_outcomes(identity_signals_large_path) if identity_signals_large_path else {}
    )

    human_labels = _load_human_labels(labels_path)
    downstream_utility_by_chunk = _load_downstream_utility(db_path)

    written = 0
    missing_triage = 0
    missing_ids = 0
    empirical_large_labeled = 0
    downstream_utility_labeled = 0

    with open_text_writer(output_path) as out_handle:
        for chunk in _iter_jsonl(chunks_path):
            if max_rows is not None and written >= max_rows:
                break

            chunk_id = chunk.get("chunk_id")
            file_id = chunk.get("file_id")
            if not isinstance(chunk_id, str) or not isinstance(file_id, str) or not chunk_id or not file_id:
                missing_ids += 1
                continue

            triage = triage_by_chunk.get(chunk_id)
            if not triage:
                missing_triage += 1
                continue

            text = chunk.get("text") or ""
            text_hash = chunk_text_hash(text)

            features = triage.get("features") if isinstance(triage.get("features"), dict) else {}
            triage_score = triage.get("score")
            triage_score_val = float(triage_score) if isinstance(triage_score, (int, float)) else 0.0
            low_quality = _is_low_quality_from_features(features)

            ent = outcomes_entities.get(chunk_id) or LLMOutcome(0, None, None)
            evt = outcomes_events.get(chunk_id) or LLMOutcome(0, None, None)
            conv = outcomes_conversations.get(chunk_id) or LLMOutcome(0, None, None)
            ident = outcomes_identity.get(chunk_id) or LLMOutcome(0, None, None)
            any_yield = ent.yield_nonempty or evt.yield_nonempty or conv.yield_nonempty or ident.yield_nonempty
            ent_large = outcomes_entities_large.get(chunk_id) or LLMOutcome(0, None, None)
            evt_large = outcomes_events_large.get(chunk_id) or LLMOutcome(0, None, None)
            conv_large = outcomes_conversations_large.get(chunk_id) or LLMOutcome(0, None, None)
            ident_large = outcomes_identity_large.get(chunk_id) or LLMOutcome(0, None, None)
            any_large_yield = (
                ent_large.yield_nonempty
                or evt_large.yield_nonempty
                or conv_large.yield_nonempty
                or ident_large.yield_nonempty
            )
            utility = downstream_utility_by_chunk.get(chunk_id) or {}
            timeline_ok_events = _as_int(utility.get("timeline_ok_events"))
            resolved_person_obs = _as_int(utility.get("resolved_person_observations"))
            downstream_utility_positive = timeline_ok_events > 0 or resolved_person_obs > 0

            label_route = _label_route(
                triage_score=triage_score_val,
                any_yield=any_yield,
                low_quality=low_quality,
                skip_threshold=skip_threshold,
                large_threshold=large_threshold,
            )
            label_source = "heuristic_from_yield"
            if any_large_yield and not any_yield:
                label_route = "llm_large"
                label_source = "empirical_large_yield"
                empirical_large_labeled += 1
            if downstream_utility_positive and not any_yield and not any_large_yield:
                label_route = "llm_small"
                label_source = "downstream_utility"
                downstream_utility_labeled += 1

            override = human_labels.get((chunk_id, text_hash))
            if override:
                label_route = override.get("label_route", label_route)
                label_source = override.get("label_source", "human")

            row: Dict[str, Any] = {
                "file_id": file_id,
                "chunk_id": chunk_id,
                "chunk_text_hash": text_hash,
                "page_range": [chunk.get("page_start"), chunk.get("page_end")],
                "text": text,
                "triage": {
                    "score": triage_score_val,
                    "route": triage.get("route"),
                    "token_est": triage.get("token_est"),
                },
                "labels": {
                    "label_route": label_route,
                    "label_source": label_source,
                },
                "outcomes": {
                    "entities": {"items_count": ent.items_count, "error": ent.error, "model": ent.model},
                    "events": {"items_count": evt.items_count, "error": evt.error, "model": evt.model},
                    "conversations": {"items_count": conv.items_count, "error": conv.error, "model": conv.model},
                    "identity_signals": {"items_count": ident.items_count, "error": ident.error, "model": ident.model},
                },
                "downstream_utility": {
                    "timeline_ok_events": timeline_ok_events,
                    "resolved_person_observations": resolved_person_obs,
                },
                "derived": {
                    "any_yield": any_yield,
                    "any_large_yield": any_large_yield,
                    "low_quality": low_quality,
                    "downstream_utility_positive": downstream_utility_positive,
                },
            }
            if (
                entities_large_path
                or events_large_path
                or conversations_large_path
                or identity_signals_large_path
            ):
                row["outcomes_large"] = {
                    "entities": {
                        "items_count": ent_large.items_count,
                        "error": ent_large.error,
                        "model": ent_large.model,
                    },
                    "events": {
                        "items_count": evt_large.items_count,
                        "error": evt_large.error,
                        "model": evt_large.model,
                    },
                    "conversations": {
                        "items_count": conv_large.items_count,
                        "error": conv_large.error,
                        "model": conv_large.model,
                    },
                    "identity_signals": {
                        "items_count": ident_large.items_count,
                        "error": ident_large.error,
                        "model": ident_large.model,
                    },
                }
            if include_features:
                row["features"] = features

            out_handle.write(json.dumps(row, ensure_ascii=True) + "\n")
            written += 1

    return {
        "rows_written": written,
        "missing_triage": missing_triage,
        "missing_ids": missing_ids,
        "rows_labeled_empirical_large": empirical_large_labeled,
        "rows_labeled_downstream_utility": downstream_utility_labeled,
    }


def main():
    args = _parse_args()
    summary = build_route_dataset(
        chunks_path=Path(args.chunks),
        triage_path=Path(args.triage),
        entities_path=Path(args.entities),
        events_path=Path(args.events),
        conversations_path=Path(args.conversations),
        identity_signals_path=Path(args.identity_signals),
        entities_large_path=Path(args.entities_large) if args.entities_large else None,
        events_large_path=Path(args.events_large) if args.events_large else None,
        conversations_large_path=Path(args.conversations_large) if args.conversations_large else None,
        identity_signals_large_path=Path(args.identity_signals_large) if args.identity_signals_large else None,
        output_path=Path(args.output),
        db_path=Path(args.db) if args.db else None,
        labels_path=Path(args.labels) if args.labels else None,
        skip_threshold=float(args.skip_threshold),
        large_threshold=float(args.large_threshold),
        include_features=bool(args.include_features),
        max_rows=args.max_rows,
    )
    print("Route dataset summary")
    for key in sorted(summary.keys()):
        print(f"- {key}: {summary[key]}")


if __name__ == "__main__":
    main()
