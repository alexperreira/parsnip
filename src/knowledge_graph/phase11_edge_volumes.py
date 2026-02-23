import argparse
import json
from dataclasses import dataclass

from loaders.store import connect_db, ensure_schema


@dataclass(frozen=True)
class EdgeVolumeSummary:
    edges_total: int
    evidence_total: int
    people_with_edges: int
    cases_with_edges: int
    edges_per_person: dict
    edges_per_case: dict
    top_edge_types: list[tuple[str, int]]


def _pct_offset(n: int, p: float) -> int | None:
    if n <= 0:
        return None
    if p <= 0:
        return 0
    if p >= 1:
        return n - 1
    # Deterministic inclusive percentile index.
    return int((n - 1) * p)


def _pct_from_group_counts(conn, group_sql: str, n: int, p: float) -> int | None:
    offset = _pct_offset(n, p)
    if offset is None:
        return None
    row = conn.execute(
        "SELECT c FROM (" + group_sql + ") ORDER BY c LIMIT 1 OFFSET ?",
        (int(offset),),
    ).fetchone()
    return int(row[0]) if row else None


def compute_edge_volumes(db_path: str) -> EdgeVolumeSummary:
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=False)

    edges_total = int(conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0])
    evidence_total = int(conn.execute("SELECT COUNT(*) FROM kg_edge_evidence").fetchone()[0])

    people_with_edges = int(
        conn.execute("SELECT COUNT(DISTINCT src_id) FROM kg_edges WHERE src_type='Person'").fetchone()[0]
    )
    cases_with_edges = int(
        conn.execute("SELECT COUNT(DISTINCT dst_id) FROM kg_edges WHERE dst_type='Case'").fetchone()[0]
    )

    per_person_counts_sql = (
        "SELECT COUNT(*) AS c FROM kg_edges WHERE src_type='Person' GROUP BY src_id"
    )
    per_case_counts_sql = "SELECT COUNT(*) AS c FROM kg_edges WHERE dst_type='Case' GROUP BY dst_id"

    per_person = {
        "p50": _pct_from_group_counts(conn, per_person_counts_sql, people_with_edges, 0.50),
        "p90": _pct_from_group_counts(conn, per_person_counts_sql, people_with_edges, 0.90),
        "p99": _pct_from_group_counts(conn, per_person_counts_sql, people_with_edges, 0.99),
        "max": int(
            conn.execute("SELECT MAX(c) FROM (" + per_person_counts_sql + ")").fetchone()[0] or 0
        )
        if people_with_edges
        else None,
    }
    per_case = {
        "p50": _pct_from_group_counts(conn, per_case_counts_sql, cases_with_edges, 0.50),
        "p90": _pct_from_group_counts(conn, per_case_counts_sql, cases_with_edges, 0.90),
        "p99": _pct_from_group_counts(conn, per_case_counts_sql, cases_with_edges, 0.99),
        "max": int(conn.execute("SELECT MAX(c) FROM (" + per_case_counts_sql + ")").fetchone()[0] or 0)
        if cases_with_edges
        else None,
    }

    top_edge_types = [
        (str(edge_type), int(count))
        for edge_type, count in conn.execute(
            "SELECT edge_type, COUNT(*) FROM kg_edges "
            "GROUP BY edge_type ORDER BY COUNT(*) DESC, edge_type LIMIT 20"
        )
        if isinstance(edge_type, str)
    ]

    conn.close()
    return EdgeVolumeSummary(
        edges_total=edges_total,
        evidence_total=evidence_total,
        people_with_edges=people_with_edges,
        cases_with_edges=cases_with_edges,
        edges_per_person=per_person,
        edges_per_case=per_case,
        top_edge_types=top_edge_types,
    )


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Compute edge volume distributions from SQLite KG tables (Phase 11)."
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print JSON output (default: false).",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    summary = compute_edge_volumes(args.db)
    if args.json:
        print(json.dumps(summary.__dict__, ensure_ascii=True, sort_keys=True, indent=2))
        return

    print("KG edge volumes")
    print(f"  edges_total: {summary.edges_total}")
    print(f"  evidence_total: {summary.evidence_total}")
    print(f"  people_with_edges: {summary.people_with_edges}")
    print(f"  cases_with_edges: {summary.cases_with_edges}")
    print("  edges_per_person:")
    for k in ("p50", "p90", "p99", "max"):
        print(f"    {k}: {summary.edges_per_person.get(k)}")
    print("  edges_per_case:")
    for k in ("p50", "p90", "p99", "max"):
        print(f"    {k}: {summary.edges_per_case.get(k)}")
    if summary.top_edge_types:
        print("  top_edge_types:")
        for edge_type, count in summary.top_edge_types:
            print(f"    - {edge_type}: {count}")


if __name__ == "__main__":
    main()

