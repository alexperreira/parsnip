import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from file_parser.compress_io import open_text_reader


_SAFE_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


@dataclass(frozen=True)
class KgPaths:
    kg_dir: Path
    person_nodes: Path
    event_nodes: Path
    case_nodes: Path
    edges: Path
    edge_evidence: Path


def _find_single_jsonl(prefix_dir: Path, stem: str) -> Path:
    if not _SAFE_NAME_RE.match(stem):
        raise SystemExit(f"Unsafe stem: {stem!r}")
    candidates = []
    for suffix in (".jsonl.zst", ".jsonl.gz", ".jsonl"):
        path = prefix_dir / f"{stem}{suffix}"
        if path.exists():
            candidates.append(path)
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise SystemExit(f"Missing export file: {prefix_dir}/{stem}.jsonl(.zst|.gz)")
    raise SystemExit(f"Ambiguous export files for {stem}: {candidates}")


def resolve_kg_paths(kg_dir: Path) -> KgPaths:
    kg_dir = Path(kg_dir)
    nodes_dir = kg_dir / "nodes"
    edges_dir = kg_dir / "edges"
    if not nodes_dir.exists() or not edges_dir.exists():
        raise SystemExit(f"Not a kg export dir: {kg_dir}")
    return KgPaths(
        kg_dir=kg_dir,
        person_nodes=_find_single_jsonl(nodes_dir, "person"),
        event_nodes=_find_single_jsonl(nodes_dir, "event"),
        case_nodes=_find_single_jsonl(nodes_dir, "case"),
        edges=_find_single_jsonl(edges_dir, "edges"),
        edge_evidence=_find_single_jsonl(edges_dir, "edge_evidence"),
    )


def _cypher_str(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (value != value):  # NaN
            return "null"
        return str(value)
    if not isinstance(value, str):
        value = str(value)
    value = value.replace("\\", "\\\\").replace("'", "\\'")
    value = value.replace("\r", "\\r").replace("\n", "\\n")
    return "'" + value + "'"


def _cypher_list_str(values):
    if values is None:
        return "null"
    if not isinstance(values, list):
        return "null"
    parts = []
    for item in values:
        if isinstance(item, str):
            parts.append(_cypher_str(item))
    return "[" + ", ".join(parts) + "]"


def _edge_key(src_type, src_id, edge_type, dst_type, dst_id):
    return f"{src_type}|{src_id}|{edge_type}|{dst_type}|{dst_id}"


def _evidence_key(edge_key, file_id, chunk_id, page_start, page_end, source_phase, extractor_version):
    return f"{edge_key}|{file_id}|{chunk_id}|{page_start}|{page_end}|{source_phase}|{extractor_version}"


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


def _emit_constraint_block():
    return "\n".join(
        [
            "// Constraints",
            "CREATE CONSTRAINT person_id IF NOT EXISTS FOR (n:Person) REQUIRE n.person_id IS UNIQUE;",
            "CREATE CONSTRAINT event_id IF NOT EXISTS FOR (n:Event) REQUIRE n.event_id IS UNIQUE;",
            "CREATE CONSTRAINT case_id_norm IF NOT EXISTS FOR (n:Case) REQUIRE n.case_id_norm IS UNIQUE;",
            "CREATE CONSTRAINT kg_edge_key IF NOT EXISTS FOR (n:KGEdge) REQUIRE n.edge_key IS UNIQUE;",
            "CREATE CONSTRAINT kg_evidence_key IF NOT EXISTS FOR (n:KGEvidence) REQUIRE n.evidence_key IS UNIQUE;",
            "",
        ]
    )


def _emit_batch_unwind(rows: list[dict], body: str) -> str:
    if not rows:
        return ""
    rows_json = json.dumps(rows, ensure_ascii=True, sort_keys=True)
    return "\n".join(
        [
            "UNWIND " + rows_json + " AS row",
            body.strip(),
            ";",
            "",
        ]
    )


def generate_neo4j_cypher(kg_dir, batch_size=500, include_evidence=True):
    paths = resolve_kg_paths(Path(kg_dir))

    counts = {
        "person_nodes": 0,
        "event_nodes": 0,
        "case_nodes": 0,
        "edges": 0,
        "edge_evidence": 0,
    }

    parts = [_emit_constraint_block()]

    # Nodes
    batch = []
    for record in _iter_jsonl(paths.person_nodes):
        if record.get("node_type") != "Person":
            continue
        person_id = record.get("node_id")
        if not isinstance(person_id, str) or not person_id:
            continue
        batch.append(
            {
                "person_id": person_id,
                "display_name": record.get("display_name"),
                "display_name_norm": record.get("display_name_norm"),
                "dob": record.get("dob"),
            }
        )
        counts["person_nodes"] += 1
        if len(batch) >= batch_size:
            parts.append(
                _emit_batch_unwind(
                    batch,
                    "MERGE (p:Person {person_id: row.person_id})\n"
                    "SET p.display_name = row.display_name,\n"
                    "    p.display_name_norm = row.display_name_norm,\n"
                    "    p.dob = row.dob",
                )
            )
            batch = []
    parts.append(
        _emit_batch_unwind(
            batch,
            "MERGE (p:Person {person_id: row.person_id})\n"
            "SET p.display_name = row.display_name,\n"
            "    p.display_name_norm = row.display_name_norm,\n"
            "    p.dob = row.dob",
        )
    )

    batch = []
    for record in _iter_jsonl(paths.event_nodes):
        if record.get("node_type") != "Event":
            continue
        event_id = record.get("node_id")
        if not isinstance(event_id, str) or not event_id:
            continue
        batch.append(
            {
                "event_id": event_id,
                "event": record.get("event"),
                "date_raw": record.get("date_raw"),
                "date_start": record.get("date_start"),
                "date_end": record.get("date_end"),
                "precision": record.get("precision"),
                "status": record.get("status"),
            }
        )
        counts["event_nodes"] += 1
        if len(batch) >= batch_size:
            parts.append(
                _emit_batch_unwind(
                    batch,
                    "MERGE (e:Event {event_id: row.event_id})\n"
                    "SET e.event = row.event,\n"
                    "    e.date_raw = row.date_raw,\n"
                    "    e.date_start = row.date_start,\n"
                    "    e.date_end = row.date_end,\n"
                    "    e.precision = row.precision,\n"
                    "    e.status = row.status",
                )
            )
            batch = []
    parts.append(
        _emit_batch_unwind(
            batch,
            "MERGE (e:Event {event_id: row.event_id})\n"
            "SET e.event = row.event,\n"
            "    e.date_raw = row.date_raw,\n"
            "    e.date_start = row.date_start,\n"
            "    e.date_end = row.date_end,\n"
            "    e.precision = row.precision,\n"
            "    e.status = row.status",
        )
    )

    batch = []
    for record in _iter_jsonl(paths.case_nodes):
        if record.get("node_type") != "Case":
            continue
        case_id_norm = record.get("node_id")
        if not isinstance(case_id_norm, str) or not case_id_norm:
            continue
        batch.append(
            {
                "case_id_norm": case_id_norm,
                "case_id_display": record.get("case_id_display"),
                "sources": record.get("sources") if isinstance(record.get("sources"), list) else None,
            }
        )
        counts["case_nodes"] += 1
        if len(batch) >= batch_size:
            parts.append(
                _emit_batch_unwind(
                    batch,
                    "MERGE (c:Case {case_id_norm: row.case_id_norm})\n"
                    "SET c.case_id_display = row.case_id_display,\n"
                    "    c.sources = row.sources",
                )
            )
            batch = []
    parts.append(
        _emit_batch_unwind(
            batch,
            "MERGE (c:Case {case_id_norm: row.case_id_norm})\n"
            "SET c.case_id_display = row.case_id_display,\n"
            "    c.sources = row.sources",
        )
    )

    # Edges as reified KGEdge nodes + endpoint links.
    batch = []
    for record in _iter_jsonl(paths.edges):
        src_type = record.get("src_type")
        src_id = record.get("src_id")
        edge_type = record.get("edge_type")
        dst_type = record.get("dst_type")
        dst_id = record.get("dst_id")
        if not all(isinstance(v, str) and v for v in (src_type, src_id, edge_type, dst_type, dst_id)):
            continue
        ek = _edge_key(src_type, src_id, edge_type, dst_type, dst_id)
        batch.append(
            {
                "edge_key": ek,
                "edge_type": edge_type,
                "src_type": src_type,
                "src_id": src_id,
                "dst_type": dst_type,
                "dst_id": dst_id,
                "created_utc": record.get("created_utc"),
            }
        )
        counts["edges"] += 1
        if len(batch) >= batch_size:
            parts.append(
                _emit_batch_unwind(
                    batch,
                    "MERGE (e:KGEdge {edge_key: row.edge_key})\n"
                    "SET e.edge_type = row.edge_type,\n"
                    "    e.src_type = row.src_type,\n"
                    "    e.src_id = row.src_id,\n"
                    "    e.dst_type = row.dst_type,\n"
                    "    e.dst_id = row.dst_id,\n"
                    "    e.created_utc = row.created_utc\n"
                    "WITH e, row\n"
                    "CALL {\n"
                    "  WITH e, row\n"
                    "  CALL {\n"
                    "    WITH e, row\n"
                    "    MATCH (s:Person {person_id: row.src_id}) WHERE row.src_type = 'Person'\n"
                    "    MERGE (s)-[:KG_SRC]->(e)\n"
                    "    RETURN 1 AS _\n"
                    "    UNION\n"
                    "    WITH e, row\n"
                    "    MATCH (s:Event {event_id: row.src_id}) WHERE row.src_type = 'Event'\n"
                    "    MERGE (s)-[:KG_SRC]->(e)\n"
                    "    RETURN 1 AS _\n"
                    "    UNION\n"
                    "    WITH e, row\n"
                    "    MATCH (s:Case {case_id_norm: row.src_id}) WHERE row.src_type = 'Case'\n"
                    "    MERGE (s)-[:KG_SRC]->(e)\n"
                    "    RETURN 1 AS _\n"
                    "  }\n"
                    "  CALL {\n"
                    "    WITH e, row\n"
                    "    MATCH (d:Person {person_id: row.dst_id}) WHERE row.dst_type = 'Person'\n"
                    "    MERGE (e)-[:KG_DST]->(d)\n"
                    "    RETURN 1 AS _\n"
                    "    UNION\n"
                    "    WITH e, row\n"
                    "    MATCH (d:Event {event_id: row.dst_id}) WHERE row.dst_type = 'Event'\n"
                    "    MERGE (e)-[:KG_DST]->(d)\n"
                    "    RETURN 1 AS _\n"
                    "    UNION\n"
                    "    WITH e, row\n"
                    "    MATCH (d:Case {case_id_norm: row.dst_id}) WHERE row.dst_type = 'Case'\n"
                    "    MERGE (e)-[:KG_DST]->(d)\n"
                    "    RETURN 1 AS _\n"
                    "  }\n"
                    "  RETURN 1 AS _\n"
                    "}\n"
                    "RETURN 1 AS _",
                )
            )
            batch = []
    parts.append(
        _emit_batch_unwind(
            batch,
            "MERGE (e:KGEdge {edge_key: row.edge_key})\n"
            "SET e.edge_type = row.edge_type,\n"
            "    e.src_type = row.src_type,\n"
            "    e.src_id = row.src_id,\n"
            "    e.dst_type = row.dst_type,\n"
            "    e.dst_id = row.dst_id,\n"
            "    e.created_utc = row.created_utc\n"
            "WITH e, row\n"
            "CALL {\n"
            "  WITH e, row\n"
            "  CALL {\n"
            "    WITH e, row\n"
            "    MATCH (s:Person {person_id: row.src_id}) WHERE row.src_type = 'Person'\n"
            "    MERGE (s)-[:KG_SRC]->(e)\n"
            "    RETURN 1 AS _\n"
            "    UNION\n"
            "    WITH e, row\n"
            "    MATCH (s:Event {event_id: row.src_id}) WHERE row.src_type = 'Event'\n"
            "    MERGE (s)-[:KG_SRC]->(e)\n"
            "    RETURN 1 AS _\n"
            "    UNION\n"
            "    WITH e, row\n"
            "    MATCH (s:Case {case_id_norm: row.src_id}) WHERE row.src_type = 'Case'\n"
            "    MERGE (s)-[:KG_SRC]->(e)\n"
            "    RETURN 1 AS _\n"
            "  }\n"
            "  CALL {\n"
            "    WITH e, row\n"
            "    MATCH (d:Person {person_id: row.dst_id}) WHERE row.dst_type = 'Person'\n"
            "    MERGE (e)-[:KG_DST]->(d)\n"
            "    RETURN 1 AS _\n"
            "    UNION\n"
            "    WITH e, row\n"
            "    MATCH (d:Event {event_id: row.dst_id}) WHERE row.dst_type = 'Event'\n"
            "    MERGE (e)-[:KG_DST]->(d)\n"
            "    RETURN 1 AS _\n"
            "    UNION\n"
            "    WITH e, row\n"
            "    MATCH (d:Case {case_id_norm: row.dst_id}) WHERE row.dst_type = 'Case'\n"
            "    MERGE (e)-[:KG_DST]->(d)\n"
            "    RETURN 1 AS _\n"
            "  }\n"
            "  RETURN 1 AS _\n"
            "}\n"
            "RETURN 1 AS _",
        )
    )

    if include_evidence:
        batch = []
        for record in _iter_jsonl(paths.edge_evidence):
            src_type = record.get("src_type")
            src_id = record.get("src_id")
            edge_type = record.get("edge_type")
            dst_type = record.get("dst_type")
            dst_id = record.get("dst_id")
            if not all(isinstance(v, str) and v for v in (src_type, src_id, edge_type, dst_type, dst_id)):
                continue
            edge_key = _edge_key(src_type, src_id, edge_type, dst_type, dst_id)
            file_id = record.get("file_id")
            chunk_id = record.get("chunk_id")
            source_phase = record.get("source_phase")
            extractor_version = record.get("extractor_version")
            page_start = record.get("page_start")
            page_end = record.get("page_end")
            if not all(isinstance(v, str) and v for v in (file_id, chunk_id, source_phase, extractor_version)):
                continue
            ev_key = _evidence_key(
                edge_key,
                file_id,
                chunk_id,
                int(page_start) if isinstance(page_start, int) else -1,
                int(page_end) if isinstance(page_end, int) else -1,
                source_phase,
                extractor_version,
            )
            batch.append(
                {
                    "evidence_key": ev_key,
                    "edge_key": edge_key,
                    "file_id": file_id,
                    "chunk_id": chunk_id,
                    "page_start": int(page_start) if isinstance(page_start, int) else -1,
                    "page_end": int(page_end) if isinstance(page_end, int) else -1,
                    "confidence": record.get("confidence"),
                    "source_phase": source_phase,
                    "extractor_version": extractor_version,
                    "created_utc": record.get("created_utc"),
                }
            )
            counts["edge_evidence"] += 1
            if len(batch) >= batch_size:
                parts.append(
                    _emit_batch_unwind(
                        batch,
                        "MATCH (e:KGEdge {edge_key: row.edge_key})\n"
                        "MERGE (v:KGEvidence {evidence_key: row.evidence_key})\n"
                        "SET v.file_id = row.file_id,\n"
                        "    v.chunk_id = row.chunk_id,\n"
                        "    v.page_start = row.page_start,\n"
                        "    v.page_end = row.page_end,\n"
                        "    v.confidence = row.confidence,\n"
                        "    v.source_phase = row.source_phase,\n"
                        "    v.extractor_version = row.extractor_version,\n"
                        "    v.created_utc = row.created_utc\n"
                        "MERGE (v)-[:EVIDENCE_FOR]->(e)",
                    )
                )
                batch = []
        parts.append(
            _emit_batch_unwind(
                batch,
                "MATCH (e:KGEdge {edge_key: row.edge_key})\n"
                "MERGE (v:KGEvidence {evidence_key: row.evidence_key})\n"
                "SET v.file_id = row.file_id,\n"
                "    v.chunk_id = row.chunk_id,\n"
                "    v.page_start = row.page_start,\n"
                "    v.page_end = row.page_end,\n"
                "    v.confidence = row.confidence,\n"
                "    v.source_phase = row.source_phase,\n"
                "    v.extractor_version = row.extractor_version,\n"
                "    v.created_utc = row.created_utc\n"
                "MERGE (v)-[:EVIDENCE_FOR]->(e)",
            )
        )

    cypher = "\n".join(part for part in parts if part)
    return cypher, counts


def query_templates():
    return {
        "case_overview": (
            "// Params: $case_id_norm, $limit_people, $limit_events\n"
            "MATCH (c:Case {case_id_norm: $case_id_norm})\n"
            "OPTIONAL MATCH (c)<-[:KG_DST]-(e1:KGEdge {edge_type:'IN_CASE'})<-[:KG_SRC]-(ev:Event)\n"
            "WITH c, collect(DISTINCT ev)[0..coalesce($limit_events, 50)] AS events\n"
            "OPTIONAL MATCH (p:Person)-[:KG_SRC]->(e2:KGEdge {edge_type:'IN_CASE'})-[:KG_DST]->(c)\n"
            "WITH c, events, p, count(e2) AS w\n"
            "ORDER BY w DESC\n"
            "WITH c, events, collect(p)[0..coalesce($limit_people, 50)] AS people\n"
            "RETURN c, events, people"
        ),
        "person_profile": (
            "// Params: $person_id, $limit_cases, $limit_events\n"
            "MATCH (p:Person {person_id: $person_id})\n"
            "OPTIONAL MATCH (p)-[:KG_SRC]->(:KGEdge {edge_type:'IN_CASE'})-[:KG_DST]->(c:Case)\n"
            "WITH p, collect(DISTINCT c)[0..coalesce($limit_cases, 50)] AS cases\n"
            "OPTIONAL MATCH (p)-[:KG_SRC]->(:KGEdge {edge_type:'MENTIONED_IN_EVENT'})-[:KG_DST]->(ev:Event)\n"
            "WITH p, cases, collect(DISTINCT ev)[0..coalesce($limit_events, 50)] AS events\n"
            "RETURN p, cases, events"
        ),
        "person_traversal_2hop_via_case": (
            "// Params: $person_id, $case_id_norm (optional), $limit\n"
            "MATCH (p:Person {person_id: $person_id})\n"
            "MATCH (p)-[:KG_SRC]->(:KGEdge {edge_type:'IN_CASE'})-[:KG_DST]->(c:Case)\n"
            "WHERE $case_id_norm IS NULL OR c.case_id_norm = $case_id_norm\n"
            "MATCH (other:Person)-[:KG_SRC]->(:KGEdge {edge_type:'IN_CASE'})-[:KG_DST]->(c)\n"
            "WHERE other.person_id <> p.person_id\n"
            "RETURN c.case_id_norm AS via_case, collect(DISTINCT other)[0..coalesce($limit, 200)] AS people"
        ),
        "event_storyline": (
            "// Params: $case_id_norm, $limit\n"
            "MATCH (c:Case {case_id_norm:$case_id_norm})\n"
            "MATCH (ev:Event)-[:KG_SRC]->(:KGEdge {edge_type:'IN_CASE'})-[:KG_DST]->(c)\n"
            "WITH ev ORDER BY ev.date_start ASC, ev.event_id ASC\n"
            "RETURN collect(ev)[0..coalesce($limit, 500)] AS events"
        ),
        "explain_edge": (
            "// Params: $edge_key, $limit\n"
            "MATCH (e:KGEdge {edge_key:$edge_key})\n"
            "MATCH (v:KGEvidence)-[:EVIDENCE_FOR]->(e)\n"
            "RETURN v ORDER BY v.file_id, v.chunk_id, v.page_start, v.source_phase "
            "LIMIT coalesce($limit, 200)"
        ),
    }


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Phase 12: generate a deterministic Neo4j Cypher import for the KG exports."
    )
    parser.add_argument(
        "--kg-dir",
        default="output/kg",
        help="KG export directory produced by phase11_build_kg (default: output/kg).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Write Cypher to this file (default: print summary only).",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=500,
        help="Rows per UNWIND batch (default: 500).",
    )
    parser.add_argument(
        "--include-evidence",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Include edge evidence nodes (default: true).",
    )
    parser.add_argument(
        "--print-query",
        default=None,
        help="Print a query template by name (case_overview, person_profile, person_traversal_2hop_via_case, "
        "event_storyline, explain_edge).",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    if args.print_query:
        templates = query_templates()
        query = templates.get(args.print_query)
        if not query:
            raise SystemExit(f"Unknown query template: {args.print_query}")
        print(query)
        return

    started = time.monotonic()
    cypher, counts = generate_neo4j_cypher(
        args.kg_dir,
        batch_size=max(1, int(args.batch_size)),
        include_evidence=bool(args.include_evidence),
    )
    elapsed = round(time.monotonic() - started, 3)
    print("Phase 12 Neo4j mirror summary")
    for key in ("person_nodes", "event_nodes", "case_nodes", "edges", "edge_evidence"):
        print(f"  {key}: {counts[key]}")
    print(f"  elapsed_seconds: {elapsed}")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(cypher, encoding="utf-8")
        print(f"  wrote_cypher: {out_path}")


if __name__ == "__main__":
    main()

