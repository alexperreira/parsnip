import argparse
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from file_parser.compress_io import open_text_reader
from loaders.store import connect_db, ensure_schema
from knowledge_graph.phase12_graphdb_mirror import resolve_kg_paths


@dataclass(frozen=True)
class _Diff:
    kind: str
    key: str
    only_in_sql: int
    only_in_graph: int


def _redact_key(value: str) -> str:
    if not isinstance(value, str) or not value:
        return "hash:empty"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"hash:{digest}"


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


def _load_export_indexes(kg_dir: Path):
    paths = resolve_kg_paths(kg_dir)

    nodes = {"Person": set(), "Event": set(), "Case": set()}
    for record in _iter_jsonl(paths.person_nodes):
        if record.get("node_type") == "Person" and isinstance(record.get("node_id"), str):
            nodes["Person"].add(record["node_id"])
    for record in _iter_jsonl(paths.event_nodes):
        if record.get("node_type") == "Event" and isinstance(record.get("node_id"), str):
            nodes["Event"].add(record["node_id"])
    for record in _iter_jsonl(paths.case_nodes):
        if record.get("node_type") == "Case" and isinstance(record.get("node_id"), str):
            nodes["Case"].add(record["node_id"])

    event_ids_by_case = {}
    person_ids_by_case = {}
    event_ids_by_person = {}
    case_ids_by_person = {}
    edge_count = 0

    for record in _iter_jsonl(paths.edges):
        edge_count += 1
        src_type = record.get("src_type")
        src_id = record.get("src_id")
        edge_type = record.get("edge_type")
        dst_type = record.get("dst_type")
        dst_id = record.get("dst_id")
        if not all(isinstance(v, str) and v for v in (src_type, src_id, edge_type, dst_type, dst_id)):
            continue

        if edge_type == "IN_CASE" and dst_type == "Case":
            if src_type == "Event":
                event_ids_by_case.setdefault(dst_id, set()).add(src_id)
            if src_type == "Person":
                person_ids_by_case.setdefault(dst_id, set()).add(src_id)
                case_ids_by_person.setdefault(src_id, set()).add(dst_id)
        if edge_type == "MENTIONED_IN_EVENT" and src_type == "Person" and dst_type == "Event":
            event_ids_by_person.setdefault(src_id, set()).add(dst_id)

    evidence_count = 0
    for _ in _iter_jsonl(paths.edge_evidence):
        evidence_count += 1

    return {
        "paths": paths,
        "nodes": nodes,
        "event_ids_by_case": event_ids_by_case,
        "person_ids_by_case": person_ids_by_case,
        "event_ids_by_person": event_ids_by_person,
        "case_ids_by_person": case_ids_by_person,
        "edge_count_total": edge_count,
        "evidence_count_total": evidence_count,
    }


def _sql_set(conn, sql: str, params: tuple):
    values = set()
    for (value,) in conn.execute(sql, params):
        if value is None:
            continue
        values.add(str(value))
    return values


def build_parity_checks(
    db_path,
    kg_dir,
    max_cases=50,
    max_people=50,
    max_diffs=20,
    strict=True,
):
    started = time.monotonic()

    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=False)

    # Ensure KG tables exist.
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='kg_edges' LIMIT 1"
    ).fetchone()
    if not row:
        raise SystemExit("Missing kg_edges. Run phase11_materialize_edges first.")

    kg_dir = Path(kg_dir)
    export = _load_export_indexes(kg_dir)

    counts = {
        "cases_checked": 0,
        "people_checked": 0,
        "diffs": 0,
        "sql_edges": int(conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0]),
        "sql_evidence": int(conn.execute("SELECT COUNT(*) FROM kg_edge_evidence").fetchone()[0]),
        "export_edges": int(export["edge_count_total"]),
        "export_evidence": int(export["evidence_count_total"]),
    }

    diffs: list[_Diff] = []

    def record_diff(kind: str, key: str, only_sql: set[str], only_graph: set[str]):
        if not only_sql and not only_graph:
            return
        counts["diffs"] += 1
        if len(diffs) < max_diffs:
            safe_key = key if kind == "count" else _redact_key(key)
            diffs.append(
                _Diff(
                    kind=kind,
                    key=safe_key,
                    only_in_sql=len(only_sql),
                    only_in_graph=len(only_graph),
                )
            )

    if counts["sql_edges"] != counts["export_edges"]:
        record_diff(
            "count",
            "kg_edges",
            {str(counts["sql_edges"])} if counts["sql_edges"] else set(),
            {str(counts["export_edges"])} if counts["export_edges"] else set(),
        )
    if counts["sql_evidence"] != counts["export_evidence"]:
        record_diff(
            "count",
            "kg_edge_evidence",
            {str(counts["sql_evidence"])} if counts["sql_evidence"] else set(),
            {str(counts["export_evidence"])} if counts["export_evidence"] else set(),
        )

    case_ids = [
        str(value)
        for (value,) in conn.execute("SELECT case_id_norm FROM cases ORDER BY case_id_norm LIMIT ?", (max_cases,))
        if isinstance(value, str) and value
    ]
    for case_id_norm in case_ids:
        counts["cases_checked"] += 1

        sql_events = _sql_set(
            conn,
            "SELECT src_id FROM kg_edges "
            "WHERE src_type='Event' AND edge_type='IN_CASE' AND dst_type='Case' AND dst_id=?",
            (case_id_norm,),
        )
        graph_events = export["event_ids_by_case"].get(case_id_norm, set())
        record_diff("case_events", case_id_norm, sql_events - graph_events, graph_events - sql_events)

        sql_people = _sql_set(
            conn,
            "SELECT src_id FROM kg_edges "
            "WHERE src_type='Person' AND edge_type='IN_CASE' AND dst_type='Case' AND dst_id=?",
            (case_id_norm,),
        )
        graph_people = export["person_ids_by_case"].get(case_id_norm, set())
        record_diff("case_people", case_id_norm, sql_people - graph_people, graph_people - sql_people)

    person_ids = [
        str(value)
        for (value,) in conn.execute("SELECT person_id FROM person_clusters ORDER BY person_id LIMIT ?", (max_people,))
        if value is not None
    ]
    for person_id in person_ids:
        counts["people_checked"] += 1

        sql_cases = _sql_set(
            conn,
            "SELECT dst_id FROM kg_edges "
            "WHERE src_type='Person' AND src_id=? AND edge_type='IN_CASE' AND dst_type='Case'",
            (person_id,),
        )
        graph_cases = export["case_ids_by_person"].get(person_id, set())
        record_diff("person_cases", person_id, sql_cases - graph_cases, graph_cases - sql_cases)

        sql_events = _sql_set(
            conn,
            "SELECT dst_id FROM kg_edges "
            "WHERE src_type='Person' AND src_id=? AND edge_type='MENTIONED_IN_EVENT' AND dst_type='Event'",
            (person_id,),
        )
        graph_events = export["event_ids_by_person"].get(person_id, set())
        record_diff("person_events", person_id, sql_events - graph_events, graph_events - sql_events)

        # 2-hop traversal: people connected via shared cases.
        sql_twohop_people = _sql_set(
            conn,
            "SELECT DISTINCT e2.src_id "
            "FROM kg_edges e1 "
            "JOIN kg_edges e2 ON e1.dst_id = e2.dst_id "
            "WHERE e1.src_type='Person' AND e1.src_id=? AND e1.edge_type='IN_CASE' "
            "AND e1.dst_type='Case' "
            "AND e2.src_type='Person' AND e2.edge_type='IN_CASE' AND e2.dst_type='Case' "
            "AND e2.src_id <> ?",
            (person_id, person_id),
        )
        graph_twohop = set()
        for case_id in export["case_ids_by_person"].get(person_id, set()):
            graph_twohop |= export["person_ids_by_case"].get(case_id, set())
        graph_twohop.discard(person_id)
        record_diff("person_twohop_via_case", person_id, sql_twohop_people - graph_twohop, graph_twohop - sql_twohop_people)

    conn.close()
    elapsed = round(time.monotonic() - started, 3)

    result = {
        "counts": counts,
        "diff_samples": [diff.__dict__ for diff in diffs],
        "elapsed_seconds": elapsed,
    }
    if strict and counts["diffs"] > 0:
        raise SystemExit("KG parity checks failed (see diff_samples).")
    return result


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Phase 12: parity checks between SQLite KG joins and exported-graph traversal results."
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--kg-dir",
        default="output/kg",
        help="KG export directory produced by phase11_build_kg (default: output/kg).",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=50,
        help="Max cases checked (default: 50).",
    )
    parser.add_argument(
        "--max-people",
        type=int,
        default=50,
        help="Max people checked (default: 50).",
    )
    parser.add_argument(
        "--max-diffs",
        type=int,
        default=20,
        help="Max diff samples recorded (default: 20).",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail non-zero if diffs exist (default: true).",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    result = build_parity_checks(
        args.db,
        args.kg_dir,
        max_cases=max(0, int(args.max_cases)),
        max_people=max(0, int(args.max_people)),
        max_diffs=max(0, int(args.max_diffs)),
        strict=bool(args.strict),
    )
    counts = result["counts"]
    print("KG parity checks summary")
    for key in (
        "cases_checked",
        "people_checked",
        "diffs",
        "sql_edges",
        "export_edges",
        "sql_evidence",
        "export_evidence",
        "elapsed_seconds",
    ):
        if key == "elapsed_seconds":
            print(f"  {key}: {result[key]}")
        else:
            print(f"  {key}: {counts.get(key)}")
    if result["diff_samples"]:
        print("  diff_samples:")
        for sample in result["diff_samples"]:
            print(f"    - {sample}")


if __name__ == "__main__":
    main()
