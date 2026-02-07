import argparse
import gzip
import json
from pathlib import Path


def _safe_pct(numerator, denominator):
    if denominator <= 0:
        return 0.0
    return round((numerator / denominator) * 100.0, 3)


def _iter_jsonl(path):
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rt", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line), None
            except json.JSONDecodeError:
                yield None, "json_decode_error"


def _manifest_shards(phase3_dir):
    manifest_path = phase3_dir / "manifest.json"
    if not manifest_path.exists():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return None
    shards = payload.get("shards") or []
    shard_paths = []
    for entry in shards:
        if not isinstance(entry, dict):
            continue
        shard_name = entry.get("shard")
        if shard_name:
            shard_paths.append(phase3_dir / shard_name)
    return shard_paths if shard_paths else None


def _phase3_shards(phase3_path):
    if phase3_path.is_file():
        return [phase3_path]
    if not phase3_path.exists() or not phase3_path.is_dir():
        raise SystemExit("Phase 3 path must be an existing file or directory.")
    manifest_shards = _manifest_shards(phase3_path)
    if manifest_shards is not None:
        return manifest_shards
    shards = sorted(phase3_path.glob("docs_*.jsonl.gz"))
    if shards:
        return shards
    shards = sorted(phase3_path.glob("docs_*.jsonl"))
    if shards:
        return shards
    raise SystemExit("No Phase 3 shard files found.")


def _count_chunks(chunks_path):
    total_chunks = 0
    json_decode_errors = 0
    for record, error in _iter_jsonl(chunks_path):
        if error:
            json_decode_errors += 1
            continue
        if isinstance(record, dict):
            total_chunks += 1
    return {
        "total_chunks": total_chunks,
        "json_decode_errors": json_decode_errors,
    }


def _count_items_coverage(path):
    records = 0
    records_with_items = 0
    json_decode_errors = 0
    for record, error in _iter_jsonl(path):
        if error:
            json_decode_errors += 1
            continue
        if not isinstance(record, dict):
            continue
        records += 1
        items = record.get("items")
        if isinstance(items, list) and len(items) > 0:
            records_with_items += 1
    return {
        "records": records,
        "records_with_items": records_with_items,
        "json_decode_errors": json_decode_errors,
    }


def _count_invalid_json_rate(paths):
    total_records = 0
    invalid_json_records = 0
    json_decode_errors = 0
    for path in paths:
        for record, error in _iter_jsonl(path):
            if error:
                json_decode_errors += 1
                continue
            if not isinstance(record, dict):
                continue
            total_records += 1
            if record.get("error") == "invalid_json":
                invalid_json_records += 1
    return {
        "total_records": total_records,
        "invalid_json_records": invalid_json_records,
        "json_decode_errors": json_decode_errors,
    }


def _count_empty_text_pages(phase3_path):
    total_pages = 0
    empty_text_pages = 0
    json_decode_errors = 0
    docs = 0
    for shard_path in _phase3_shards(phase3_path):
        if not shard_path.exists():
            raise SystemExit(f"Missing Phase 3 shard: {shard_path}")
        for record, error in _iter_jsonl(shard_path):
            if error:
                json_decode_errors += 1
                continue
            if not isinstance(record, dict):
                continue
            docs += 1
            pages = record.get("pages")
            if not isinstance(pages, list):
                continue
            for page in pages:
                if not isinstance(page, dict):
                    continue
                total_pages += 1
                text = page.get("text") or ""
                if not str(text).strip():
                    empty_text_pages += 1
    return {
        "docs": docs,
        "total_pages": total_pages,
        "empty_text_pages": empty_text_pages,
        "json_decode_errors": json_decode_errors,
    }


def build_phase7(chunks_path, entities_path, events_path, phase3_path, conversations_path=None):
    chunks_path = Path(chunks_path)
    entities_path = Path(entities_path)
    events_path = Path(events_path)
    phase3_path = Path(phase3_path)
    conversations_path = Path(conversations_path) if conversations_path else None

    required_paths = [chunks_path, entities_path, events_path]
    for path in required_paths:
        if not path.exists():
            raise SystemExit(f"Required input is missing: {path}")

    chunk_counts = _count_chunks(chunks_path)
    entity_counts = _count_items_coverage(entities_path)
    event_counts = _count_items_coverage(events_path)
    llm_paths = [entities_path, events_path]
    if conversations_path is not None:
        if not conversations_path.exists():
            raise SystemExit(f"Required input is missing: {conversations_path}")
        llm_paths.append(conversations_path)
    invalid_json_counts = _count_invalid_json_rate(llm_paths)
    empty_page_counts = _count_empty_text_pages(phase3_path)

    total_chunks = chunk_counts["total_chunks"]
    entity_records = entity_counts["records"]
    event_records = event_counts["records"]
    chunk_alignment_warnings = []
    if entity_records != total_chunks:
        chunk_alignment_warnings.append(
            (
                "entities_record_count_mismatch: "
                f"entities_records={entity_records}, total_chunks={total_chunks}"
            )
        )
    if event_records != total_chunks:
        chunk_alignment_warnings.append(
            (
                "events_record_count_mismatch: "
                f"events_records={event_records}, total_chunks={total_chunks}"
            )
        )

    return {
        "total_chunks": total_chunks,
        "entity_records": entity_records,
        "event_records": event_records,
        "chunks_with_entities": entity_counts["records_with_items"],
        "chunks_with_events": event_counts["records_with_items"],
        "entity_yield_pct": _safe_pct(entity_counts["records_with_items"], total_chunks),
        "event_yield_pct": _safe_pct(event_counts["records_with_items"], total_chunks),
        "llm_total_records": invalid_json_counts["total_records"],
        "llm_invalid_json_records": invalid_json_counts["invalid_json_records"],
        "llm_invalid_json_rate_pct": _safe_pct(
            invalid_json_counts["invalid_json_records"],
            invalid_json_counts["total_records"],
        ),
        "phase3_docs": empty_page_counts["docs"],
        "phase3_total_pages": empty_page_counts["total_pages"],
        "phase3_empty_text_pages": empty_page_counts["empty_text_pages"],
        "empty_text_page_rate_pct": _safe_pct(
            empty_page_counts["empty_text_pages"], empty_page_counts["total_pages"]
        ),
        "json_decode_errors": {
            "chunks": chunk_counts["json_decode_errors"],
            "entities": entity_counts["json_decode_errors"],
            "events": event_counts["json_decode_errors"],
            "llm_outputs": invalid_json_counts["json_decode_errors"],
            "phase3": empty_page_counts["json_decode_errors"],
        },
        "warnings": chunk_alignment_warnings,
    }


def _print_summary(summary):
    print("Phase 7 validation summary")
    print(f"  total_chunks: {summary['total_chunks']}")
    print(
        "  chunk_entity_yield_pct: "
        f"{summary['entity_yield_pct']} ({summary['chunks_with_entities']}/{summary['total_chunks']})"
    )
    print(
        "  chunk_event_yield_pct: "
        f"{summary['event_yield_pct']} ({summary['chunks_with_events']}/{summary['total_chunks']})"
    )
    print(
        "  llm_invalid_json_rate_pct: "
        f"{summary['llm_invalid_json_rate_pct']} "
        f"({summary['llm_invalid_json_records']}/{summary['llm_total_records']})"
    )
    print(
        "  empty_text_page_rate_pct: "
        f"{summary['empty_text_page_rate_pct']} "
        f"({summary['phase3_empty_text_pages']}/{summary['phase3_total_pages']})"
    )
    decode_errors = summary["json_decode_errors"]
    if any(value > 0 for value in decode_errors.values()):
        print("  json_decode_errors:")
        print(f"    chunks: {decode_errors['chunks']}")
        print(f"    entities: {decode_errors['entities']}")
        print(f"    events: {decode_errors['events']}")
        print(f"    llm_outputs: {decode_errors['llm_outputs']}")
        print(f"    phase3: {decode_errors['phase3']}")
    if summary["warnings"]:
        print("  warnings:")
        for warning in summary["warnings"]:
            print(f"    - {warning}")


def _parse_args():
    parser = argparse.ArgumentParser(description="Phase 7 sanity checks.")
    parser.add_argument(
        "--chunks",
        default="output/text/chunks.jsonl",
        help="Chunks JSONL path (default: output/text/chunks.jsonl).",
    )
    parser.add_argument(
        "--entities",
        default="output/entities.jsonl",
        help="Entities JSONL path (default: output/entities.jsonl).",
    )
    parser.add_argument(
        "--events",
        default="output/events.jsonl",
        help="Events JSONL path (default: output/events.jsonl).",
    )
    parser.add_argument(
        "--conversations",
        default=None,
        help="Optional conversations JSONL path for invalid-json rollup.",
    )
    parser.add_argument(
        "--phase3",
        default="output/text",
        help="Phase 3 output directory or shard file (default: output/text).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON summary.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    summary = build_phase7(
        chunks_path=args.chunks,
        entities_path=args.entities,
        events_path=args.events,
        phase3_path=args.phase3,
        conversations_path=args.conversations,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=True, indent=2))
    else:
        _print_summary(summary)


if __name__ == "__main__":
    main()
