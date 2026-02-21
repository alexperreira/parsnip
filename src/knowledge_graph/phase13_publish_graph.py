import argparse
import json
import time
from pathlib import Path

from knowledge_graph.phase11_materialize_edges import build_materialize_edges
from knowledge_graph.phase11_build_kg import build_export_kg
from knowledge_graph.phase12_graphdb_mirror import generate_neo4j_cypher
from knowledge_graph.phase12_parity_checks import build_parity_checks
from loaders.store import connect_db


def _load_schema_version(db_path: str) -> str | None:
    conn = connect_db(db_path)
    row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    conn.close()
    if row and isinstance(row[0], str):
        return row[0]
    return None


def _edge_volume_summary(db_path: str):
    conn = connect_db(db_path)
    edges_total = int(conn.execute("SELECT COUNT(*) FROM kg_edges").fetchone()[0])

    per_person = [
        int(v)
        for (v,) in conn.execute(
            "SELECT COUNT(*) AS c FROM kg_edges WHERE src_type='Person' GROUP BY src_id ORDER BY c"
        )
    ]
    per_case = [
        int(v)
        for (v,) in conn.execute(
            "SELECT COUNT(*) AS c FROM kg_edges WHERE dst_type='Case' GROUP BY dst_id ORDER BY c"
        )
    ]
    conn.close()

    def pct(values: list[int], p: float) -> int | None:
        if not values:
            return None
        idx = int(round((len(values) - 1) * p))
        idx = max(0, min(len(values) - 1, idx))
        return int(values[idx])

    return {
        "edges_total": edges_total,
        "edges_per_person": {
            "count_people": len(per_person),
            "p50": pct(per_person, 0.50),
            "p90": pct(per_person, 0.90),
            "p99": pct(per_person, 0.99),
            "max": max(per_person) if per_person else None,
        },
        "edges_per_case": {
            "count_cases": len(per_case),
            "p50": pct(per_case, 0.50),
            "p90": pct(per_case, 0.90),
            "p99": pct(per_case, 0.99),
            "max": max(per_case) if per_case else None,
        },
    }


def build_publish_graph(
    db_path: str,
    out_dir: str,
    *,
    kg_reset: bool = True,
    compression: str = "zstd",
    zstd_level: int = 3,
    cypher_out: str | None = None,
    strict: bool = True,
    run_parity_checks: bool = True,
):
    started = time.monotonic()
    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)

    materialize = build_materialize_edges(db_path, reset=kg_reset)

    export = build_export_kg(
        db_path,
        out_dir_path,
        compression=compression,
        zstd_level=zstd_level,
        strict=strict,
    )

    cypher, cypher_counts = generate_neo4j_cypher(out_dir_path, batch_size=500, include_evidence=True)
    if cypher_out:
        cypher_path = Path(cypher_out)
        cypher_path.parent.mkdir(parents=True, exist_ok=True)
        cypher_path.write_text(cypher, encoding="utf-8")
    else:
        cypher_path = out_dir_path / "neo4j_import.cypher"
        cypher_path.write_text(cypher, encoding="utf-8")

    parity = None
    if run_parity_checks:
        parity = build_parity_checks(db_path, out_dir_path, strict=strict)

    schema_version = _load_schema_version(db_path)
    volumes = _edge_volume_summary(db_path)

    result = {
        "schema_version": schema_version,
        "materialize": materialize,
        "export": {
            "counts": export["counts"],
            "top_edge_types": export["top_edge_types"],
            "nodes": export["nodes"],
            "edges": export["edges"],
            "issues": export["issues"],
        },
        "neo4j_cypher": {
            "path": str(cypher_path),
            "counts": cypher_counts,
        },
        "parity": parity,
        "edge_volumes": volumes,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }

    manifest_path = out_dir_path / "build_manifest.json"
    manifest_path.write_text(json.dumps(result, ensure_ascii=True, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return result


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Phase 13: publish the KG build artifacts (SQLite -> exports -> Neo4j Cypher) deterministically."
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--out",
        default="output/kg",
        help="Output directory for exports and manifest (default: output/kg).",
    )
    parser.add_argument(
        "--kg-reset",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Reset KG tables before materialization (default: true).",
    )
    parser.add_argument(
        "--compression",
        choices=("zstd", "gzip", "none"),
        default="zstd",
        help="Compression for exports (default: zstd).",
    )
    parser.add_argument(
        "--zstd-level",
        type=int,
        default=3,
        help="Zstandard compression level (default: 3).",
    )
    parser.add_argument(
        "--cypher-out",
        default=None,
        help="Optional path for the generated Neo4j import Cypher (default: <out>/neo4j_import.cypher).",
    )
    parser.add_argument(
        "--parity",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run parity checks after export (default: true).",
    )
    parser.add_argument(
        "--strict",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail on validation/parity diffs (default: true).",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    result = build_publish_graph(
        args.db,
        args.out,
        kg_reset=bool(args.kg_reset),
        compression=str(args.compression),
        zstd_level=int(args.zstd_level),
        cypher_out=args.cypher_out,
        strict=bool(args.strict),
        run_parity_checks=bool(args.parity),
    )
    print("KG publish summary")
    counts = result["export"]["counts"]
    print(f"  schema_version: {result.get('schema_version')}")
    print(f"  edges: {counts.get('edges')}")
    print(f"  evidence: {counts.get('edge_evidence')}")
    if result.get("parity"):
        print(f"  parity_diffs: {result['parity']['counts'].get('diffs')}")
    print(f"  manifest: {Path(args.out) / 'build_manifest.json'}")
    print(f"  neo4j_cypher: {result['neo4j_cypher']['path']}")


if __name__ == "__main__":
    main()

