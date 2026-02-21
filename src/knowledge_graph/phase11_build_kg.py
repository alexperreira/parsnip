import argparse
import json
import time
from pathlib import Path

from file_parser.compress_io import open_text_writer, shard_suffix_for_compression
from loaders.store import connect_db, ensure_schema


def _table_exists(conn, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (name,),
    ).fetchone()
    return bool(row)


def _jsonl_writer(path: Path, compression: str, zstd_level: int | None):
    path = Path(str(path) + shard_suffix_for_compression(compression))
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open_text_writer(path, zstd_level=zstd_level)

    def write_obj(obj: dict):
        handle.write(json.dumps(obj, ensure_ascii=True, sort_keys=True) + "\n")

    return path, handle, write_obj


def _validate(conn, strict: bool):
    issues = []

    # Required properties / basic sanity.
    missing_edges = conn.execute(
        "SELECT COUNT(*) FROM kg_edges WHERE src_type='' OR src_id='' OR edge_type='' OR dst_type='' OR dst_id=''"
    ).fetchone()[0]
    if missing_edges:
        issues.append(f"kg_edges_missing_required_fields={int(missing_edges)}")

    missing_evidence = conn.execute(
        "SELECT COUNT(*) FROM kg_edge_evidence "
        "WHERE file_id='' OR chunk_id='' OR source_phase='' OR extractor_version='' "
        "OR page_start IS NULL OR page_end IS NULL"
    ).fetchone()[0]
    if missing_evidence:
        issues.append(f"kg_edge_evidence_missing_required_fields={int(missing_evidence)}")

    # Referential integrity for current v1 edge endpoints (Person/Event/Case).
    missing_src_person = conn.execute(
        "SELECT COUNT(*) FROM kg_edges e "
        "LEFT JOIN person_clusters p ON e.src_type='Person' AND p.person_id=CAST(e.src_id AS INTEGER) "
        "WHERE e.src_type='Person' AND p.person_id IS NULL"
    ).fetchone()[0]
    if missing_src_person:
        issues.append(f"kg_edges_missing_src_person={int(missing_src_person)}")

    missing_dst_person = conn.execute(
        "SELECT COUNT(*) FROM kg_edges e "
        "LEFT JOIN person_clusters p ON e.dst_type='Person' AND p.person_id=CAST(e.dst_id AS INTEGER) "
        "WHERE e.dst_type='Person' AND p.person_id IS NULL"
    ).fetchone()[0]
    if missing_dst_person:
        issues.append(f"kg_edges_missing_dst_person={int(missing_dst_person)}")

    missing_src_event = conn.execute(
        "SELECT COUNT(*) FROM kg_edges e "
        "LEFT JOIN events ev ON e.src_type='Event' AND ev.event_id=CAST(e.src_id AS INTEGER) "
        "WHERE e.src_type='Event' AND ev.event_id IS NULL"
    ).fetchone()[0]
    if missing_src_event:
        issues.append(f"kg_edges_missing_src_event={int(missing_src_event)}")

    missing_dst_event = conn.execute(
        "SELECT COUNT(*) FROM kg_edges e "
        "LEFT JOIN events ev ON e.dst_type='Event' AND ev.event_id=CAST(e.dst_id AS INTEGER) "
        "WHERE e.dst_type='Event' AND ev.event_id IS NULL"
    ).fetchone()[0]
    if missing_dst_event:
        issues.append(f"kg_edges_missing_dst_event={int(missing_dst_event)}")

    missing_src_case = conn.execute(
        "SELECT COUNT(*) FROM kg_edges e "
        "LEFT JOIN cases c ON e.src_type='Case' AND c.case_id_norm=e.src_id "
        "WHERE e.src_type='Case' AND c.case_id_norm IS NULL"
    ).fetchone()[0]
    if missing_src_case:
        issues.append(f"kg_edges_missing_src_case={int(missing_src_case)}")

    missing_dst_case = conn.execute(
        "SELECT COUNT(*) FROM kg_edges e "
        "LEFT JOIN cases c ON e.dst_type='Case' AND c.case_id_norm=e.dst_id "
        "WHERE e.dst_type='Case' AND c.case_id_norm IS NULL"
    ).fetchone()[0]
    if missing_dst_case:
        issues.append(f"kg_edges_missing_dst_case={int(missing_dst_case)}")

    unknown_endpoint_types = conn.execute(
        "SELECT COUNT(*) FROM kg_edges "
        "WHERE src_type NOT IN ('Person','Event','Case') OR dst_type NOT IN ('Person','Event','Case')"
    ).fetchone()[0]
    if unknown_endpoint_types:
        issues.append(f"kg_edges_unknown_endpoint_types={int(unknown_endpoint_types)}")

    if issues and strict:
        raise SystemExit("KG export validation failed: " + ", ".join(issues))
    return issues


def build_export_kg(
    db_path,
    out_dir,
    compression="zstd",
    zstd_level=3,
    strict=True,
):
    started = time.monotonic()
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=False)

    if not _table_exists(conn, "kg_edges") or not _table_exists(conn, "kg_edge_evidence"):
        raise SystemExit("KG tables not found. Run Phase B first (phase11_materialize_edges).")

    issues = _validate(conn, strict=strict)

    out_dir = Path(out_dir)
    nodes_dir = out_dir / "nodes"
    edges_dir = out_dir / "edges"

    counts = {
        "nodes_person": 0,
        "nodes_event": 0,
        "nodes_case": 0,
        "edges": 0,
        "edge_evidence": 0,
        "issues": len(issues),
    }

    node_files = {}

    path, handle, write = _jsonl_writer(nodes_dir / "person", compression, zstd_level)
    node_files["person"] = path
    for person_id, display_name, display_name_norm, dob in conn.execute(
        "SELECT person_id, display_name, display_name_norm, dob "
        "FROM person_clusters ORDER BY person_id"
    ):
        if person_id is None or display_name is None or display_name_norm is None:
            continue
        write(
            {
                "node_type": "Person",
                "node_id": str(int(person_id)),
                "display_name": str(display_name),
                "display_name_norm": str(display_name_norm),
                "dob": str(dob) if isinstance(dob, str) and dob else None,
            }
        )
        counts["nodes_person"] += 1
    handle.close()

    path, handle, write = _jsonl_writer(nodes_dir / "event", compression, zstd_level)
    node_files["event"] = path
    # Join timeline normalization when available.
    for row in conn.execute(
        "SELECT e.event_id, e.event, e.date, "
        "t.date_start, t.date_end, t.precision, t.status "
        "FROM events e LEFT JOIN event_times t ON t.event_id = e.event_id "
        "ORDER BY e.event_id"
    ):
        event_id, event_text, date_raw, date_start, date_end, precision, status = row
        if event_id is None or event_text is None:
            continue
        write(
            {
                "node_type": "Event",
                "node_id": str(int(event_id)),
                "event": str(event_text),
                "date_raw": str(date_raw) if isinstance(date_raw, str) and date_raw else None,
                "date_start": str(date_start) if isinstance(date_start, str) and date_start else None,
                "date_end": str(date_end) if isinstance(date_end, str) and date_end else None,
                "precision": str(precision) if isinstance(precision, str) and precision else None,
                "status": str(status) if isinstance(status, str) and status else None,
            }
        )
        counts["nodes_event"] += 1
    handle.close()

    path, handle, write = _jsonl_writer(nodes_dir / "case", compression, zstd_level)
    node_files["case"] = path
    for case_id_norm, case_id_display, sources_json in conn.execute(
        "SELECT case_id_norm, case_id_display, sources_json FROM cases ORDER BY case_id_norm"
    ):
        if not isinstance(case_id_norm, str) or not case_id_norm:
            continue
        sources = None
        if isinstance(sources_json, str) and sources_json.strip():
            try:
                parsed = json.loads(sources_json)
                if isinstance(parsed, list):
                    sources = [str(s) for s in parsed if isinstance(s, str)]
            except json.JSONDecodeError:
                sources = None
        write(
            {
                "node_type": "Case",
                "node_id": case_id_norm,
                "case_id_display": str(case_id_display)
                if isinstance(case_id_display, str) and case_id_display
                else None,
                "sources": sources,
            }
        )
        counts["nodes_case"] += 1
    handle.close()

    edge_files = {}

    path, handle, write = _jsonl_writer(edges_dir / "edges", compression, zstd_level)
    edge_files["edges"] = path
    for row in conn.execute(
        "SELECT src_type, src_id, edge_type, dst_type, dst_id, created_utc "
        "FROM kg_edges "
        "ORDER BY src_type, src_id, edge_type, dst_type, dst_id"
    ):
        src_type, src_id, edge_type, dst_type, dst_id, created_utc = row
        if not (src_type and src_id and edge_type and dst_type and dst_id):
            continue
        write(
            {
                "src_type": str(src_type),
                "src_id": str(src_id),
                "edge_type": str(edge_type),
                "dst_type": str(dst_type),
                "dst_id": str(dst_id),
                "created_utc": str(created_utc) if isinstance(created_utc, str) and created_utc else None,
            }
        )
        counts["edges"] += 1
    handle.close()

    path, handle, write = _jsonl_writer(edges_dir / "edge_evidence", compression, zstd_level)
    edge_files["edge_evidence"] = path
    for row in conn.execute(
        "SELECT src_type, src_id, edge_type, dst_type, dst_id, "
        "file_id, chunk_id, page_start, page_end, confidence, "
        "source_phase, extractor_version, created_utc "
        "FROM kg_edge_evidence "
        "ORDER BY src_type, src_id, edge_type, dst_type, dst_id, "
        "file_id, chunk_id, page_start, page_end, source_phase, extractor_version"
    ):
        (
            src_type,
            src_id,
            edge_type,
            dst_type,
            dst_id,
            file_id,
            chunk_id,
            page_start,
            page_end,
            confidence,
            source_phase,
            extractor_version,
            created_utc,
        ) = row
        if not (src_type and src_id and edge_type and dst_type and dst_id):
            continue
        if not (file_id and chunk_id and source_phase and extractor_version):
            continue
        write(
            {
                "src_type": str(src_type),
                "src_id": str(src_id),
                "edge_type": str(edge_type),
                "dst_type": str(dst_type),
                "dst_id": str(dst_id),
                "file_id": str(file_id),
                "chunk_id": str(chunk_id),
                "page_start": int(page_start) if isinstance(page_start, int) else -1,
                "page_end": int(page_end) if isinstance(page_end, int) else -1,
                "confidence": float(confidence) if isinstance(confidence, (int, float)) else None,
                "source_phase": str(source_phase),
                "extractor_version": str(extractor_version),
                "created_utc": str(created_utc) if isinstance(created_utc, str) and created_utc else None,
            }
        )
        counts["edge_evidence"] += 1
    handle.close()

    top_edge_types = [
        (edge_type, int(count))
        for edge_type, count in conn.execute(
            "SELECT edge_type, COUNT(*) FROM kg_edges GROUP BY edge_type ORDER BY COUNT(*) DESC, edge_type LIMIT 20"
        )
        if isinstance(edge_type, str)
    ]

    conn.close()
    counts["elapsed_seconds"] = round(time.monotonic() - started, 3)

    return {
        "out_dir": str(out_dir),
        "nodes": {k: str(v) for k, v in node_files.items()},
        "edges": {k: str(v) for k, v in edge_files.items()},
        "counts": counts,
        "top_edge_types": top_edge_types,
        "issues": issues,
    }


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Export knowledge-graph nodes/edges from SQLite to deterministic JSONL shards (Phase 11)."
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--out",
        default="output/kg",
        help="Output directory (default: output/kg).",
    )
    parser.add_argument(
        "--compression",
        choices=("zstd", "gzip", "none"),
        default="zstd",
        help="Compression for JSONL outputs (default: zstd).",
    )
    parser.add_argument(
        "--zstd-level",
        type=int,
        default=3,
        help="Zstandard compression level (default: 3).",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail on validation issues (default: true).",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    result = build_export_kg(
        args.db,
        args.out,
        compression=args.compression,
        zstd_level=args.zstd_level,
        strict=args.strict,
    )
    print("KG export summary")
    counts = result["counts"]
    for key in (
        "nodes_person",
        "nodes_event",
        "nodes_case",
        "edges",
        "edge_evidence",
        "issues",
        "elapsed_seconds",
    ):
        print(f"  {key}: {counts.get(key)}")
    if result["top_edge_types"]:
        print("  top_edge_types:")
        for edge_type, count in result["top_edge_types"]:
            print(f"    - {edge_type}: {count}")


if __name__ == "__main__":
    main()

