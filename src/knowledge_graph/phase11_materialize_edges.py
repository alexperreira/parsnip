import argparse
import re
import time
from dataclasses import dataclass

from loaders.store import connect_db, ensure_schema


_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalize_name(value):
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    if not value:
        return None
    value = _NON_ALNUM_RE.sub(" ", value)
    value = " ".join(value.split())
    return value if value else None


def _as_page(value):
    if isinstance(value, bool):
        return -1
    if isinstance(value, int):
        return value if value >= 0 else -1
    if isinstance(value, float) and value.is_integer():
        return int(value) if value >= 0 else -1
    if isinstance(value, str):
        value = value.strip()
        if value.isdigit():
            return int(value)
    return -1


def _merge_pages(left_start, left_end, right_start, right_end):
    left_start = _as_page(left_start)
    left_end = _as_page(left_end)
    right_start = _as_page(right_start)
    right_end = _as_page(right_end)

    starts = [p for p in (left_start, right_start) if p >= 0]
    ends = [p for p in (left_end, right_end) if p >= 0]
    if not starts and not ends:
        return -1, -1
    page_start = min(starts) if starts else -1
    page_end = max(ends) if ends else page_start
    if page_end < page_start and page_end >= 0 and page_start >= 0:
        page_start, page_end = page_end, page_start
    return page_start, page_end


@dataclass(frozen=True)
class _Edge:
    src_type: str
    src_id: str
    edge_type: str
    dst_type: str
    dst_id: str


def _now_utc_sql():
    return "datetime('now')"


def _upsert_cases(conn):
    existing = {}
    for case_id_norm, case_id_display, sources_json in conn.execute(
        "SELECT case_id_norm, case_id_display, sources_json FROM cases"
    ):
        sources = set()
        if isinstance(sources_json, str) and sources_json.strip():
            # Stored as JSON-ish; keep it simple and tolerate legacy formats.
            raw = sources_json.strip().strip("[]")
            for part in raw.split(","):
                part = part.strip().strip('"').strip("'")
                if part:
                    sources.add(part)
        existing[str(case_id_norm)] = {
            "display": str(case_id_display) if isinstance(case_id_display, str) and case_id_display else None,
            "sources": sources,
        }

    discovered = {}

    for case_id_norm, case_id, source in conn.execute(
        "SELECT DISTINCT case_id_norm, case_id, source FROM event_cases WHERE case_id_norm IS NOT NULL"
    ):
        if not isinstance(case_id_norm, str) or not case_id_norm.strip():
            continue
        display = str(case_id) if isinstance(case_id, str) and case_id.strip() else None
        src = str(source) if isinstance(source, str) and source.strip() else "event_cases"
        bucket = discovered.setdefault(case_id_norm, {"displays": set(), "sources": set()})
        if display:
            bucket["displays"].add(display)
        bucket["sources"].add(src)

    for case_id_norm, case_id in conn.execute(
        "SELECT DISTINCT value_norm, value FROM identity_signals "
        "WHERE attribute='case_id' AND value_norm IS NOT NULL"
    ):
        if not isinstance(case_id_norm, str) or not case_id_norm.strip():
            continue
        display = str(case_id) if isinstance(case_id, str) and case_id.strip() else None
        bucket = discovered.setdefault(case_id_norm, {"displays": set(), "sources": set()})
        if display:
            bucket["displays"].add(display)
        bucket["sources"].add("identity_signals")

    rows = []
    for case_id_norm, info in discovered.items():
        merged = existing.get(case_id_norm, {"display": None, "sources": set()})
        displays = sorted(info["displays"])
        display = merged["display"] or (displays[0] if displays else None)
        sources = set(merged["sources"]) | set(info["sources"])
        sources_json = "[" + ", ".join(f"\"{s}\"" for s in sorted(sources)) + "]" if sources else None
        rows.append((case_id_norm, display, sources_json))

    if not rows:
        return 0, 0

    inserted = 0
    updated = 0
    for row in rows:
        result = conn.execute(
            "INSERT INTO cases(case_id_norm, case_id_display, sources_json) VALUES (?, ?, ?) "
            "ON CONFLICT(case_id_norm) DO UPDATE SET "
            "case_id_display=COALESCE(cases.case_id_display, excluded.case_id_display), "
            "sources_json=excluded.sources_json",
            row,
        )
        if result.rowcount == 1:
            inserted += 1
        else:
            updated += 1
    return inserted, updated


def build_materialize_edges(db_path, reset=False, extractor_version="kg_phase11_v1"):
    started = time.monotonic()
    conn = connect_db(db_path)
    ensure_schema(conn, overwrite=False)

    if reset:
        conn.execute("DELETE FROM kg_edge_evidence")
        conn.execute("DELETE FROM kg_edges")
        conn.execute("DELETE FROM cases")
        conn.commit()

    conn.create_function("name_norm", 1, _normalize_name)

    cases_inserted, cases_updated = _upsert_cases(conn)

    summary = {
        "cases_inserted_or_updated": int(cases_inserted + cases_updated),
        "edges_inserted": 0,
        "evidence_inserted": 0,
        "event_case_rows": 0,
        "person_event_rows": 0,
        "person_case_rows": 0,
    }

    def insert_edge(edge: _Edge):
        result = conn.execute(
            "INSERT OR IGNORE INTO kg_edges("
            "src_type, src_id, edge_type, dst_type, dst_id, created_utc"
            ") VALUES (?, ?, ?, ?, ?, " + _now_utc_sql() + ")",
            (edge.src_type, edge.src_id, edge.edge_type, edge.dst_type, edge.dst_id),
        )
        if result.rowcount == 1:
            summary["edges_inserted"] += 1

    def insert_evidence(edge: _Edge, file_id, chunk_id, page_start, page_end, confidence, source_phase):
        result = conn.execute(
            "INSERT OR IGNORE INTO kg_edge_evidence("
            "src_type, src_id, edge_type, dst_type, dst_id, "
            "file_id, chunk_id, page_start, page_end, confidence, "
            "source_phase, extractor_version, created_utc"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, " + _now_utc_sql() + ")",
            (
                edge.src_type,
                edge.src_id,
                edge.edge_type,
                edge.dst_type,
                edge.dst_id,
                file_id,
                chunk_id,
                int(page_start),
                int(page_end),
                confidence,
                source_phase,
                extractor_version,
            ),
        )
        if result.rowcount == 1:
            summary["evidence_inserted"] += 1

    # Edge: Event -[:IN_CASE]-> Case (evidence from the originating event row).
    for (
        event_id,
        case_id_norm,
        case_source,
        file_id,
        chunk_id,
        ev_page_start,
        ev_page_end,
        ev_conf,
    ) in conn.execute(
        "SELECT ec.event_id, ec.case_id_norm, ec.source, "
        "e.file_id, e.chunk_id, e.page_start, e.page_end, e.confidence "
        "FROM event_cases ec JOIN events e ON e.event_id = ec.event_id"
    ):
        if event_id is None or not isinstance(case_id_norm, str) or not isinstance(file_id, str):
            continue
        if not isinstance(chunk_id, str) or not chunk_id:
            continue
        edge = _Edge("Event", str(int(event_id)), "IN_CASE", "Case", case_id_norm)
        insert_edge(edge)
        page_start, page_end = _merge_pages(ev_page_start, ev_page_end, None, None)
        insert_evidence(
            edge,
            file_id,
            chunk_id,
            page_start,
            page_end,
            ev_conf if isinstance(ev_conf, (int, float)) else None,
            f"phase9_event_cases:{case_source}" if isinstance(case_source, str) else "phase9_event_cases",
        )
        summary["event_case_rows"] += 1

    # Edge: Person -[:MENTIONED_IN_EVENT]-> Event (co-occurrence in the same file/chunk).
    for (
        person_id,
        event_id,
        file_id,
        chunk_id,
        person_page_start,
        person_page_end,
        ev_page_start,
        ev_page_end,
        ev_conf,
    ) in conn.execute(
        "SELECT DISTINCT pcm.person_id, e.event_id, "
        "e.file_id, e.chunk_id, "
        "po.page_start, po.page_end, "
        "e.page_start, e.page_end, "
        "e.confidence "
        "FROM person_cluster_members pcm "
        "JOIN person_observations po ON po.obs_id = pcm.obs_id "
        "JOIN events e ON e.file_id = po.file_id AND e.chunk_id = po.chunk_id"
    ):
        if person_id is None or event_id is None:
            continue
        if not isinstance(file_id, str) or not isinstance(chunk_id, str) or not chunk_id:
            continue
        edge = _Edge("Person", str(int(person_id)), "MENTIONED_IN_EVENT", "Event", str(int(event_id)))
        insert_edge(edge)
        page_start, page_end = _merge_pages(person_page_start, person_page_end, ev_page_start, ev_page_end)
        insert_evidence(
            edge,
            file_id,
            chunk_id,
            page_start,
            page_end,
            ev_conf if isinstance(ev_conf, (int, float)) else None,
            "phase11_cooccurrence:file_chunk",
        )
        summary["person_event_rows"] += 1

    # Edge: Person -[:IN_CASE]-> Case (identity_signals case_id attached to a resolved person).
    for (
        person_id,
        case_id_norm,
        file_id,
        chunk_id,
        page_start,
        page_end,
        conf,
    ) in conn.execute(
        "SELECT DISTINCT pcm.person_id, s.value_norm, s.file_id, s.chunk_id, "
        "s.page_start, s.page_end, s.confidence "
        "FROM identity_signals s "
        "JOIN person_observations po "
        "ON po.file_id = s.file_id AND po.chunk_id = s.chunk_id "
        "AND po.name_norm = name_norm(s.person_text) "
        "JOIN person_cluster_members pcm ON pcm.obs_id = po.obs_id "
        "WHERE s.attribute='case_id' AND s.value_norm IS NOT NULL"
    ):
        if person_id is None or not isinstance(case_id_norm, str) or not case_id_norm.strip():
            continue
        if not isinstance(file_id, str) or not isinstance(chunk_id, str) or not chunk_id:
            continue
        edge = _Edge("Person", str(int(person_id)), "IN_CASE", "Case", case_id_norm)
        insert_edge(edge)
        insert_evidence(
            edge,
            file_id,
            chunk_id,
            _as_page(page_start),
            _as_page(page_end),
            conf if isinstance(conf, (int, float)) else None,
            "phase6_identity_signals:case_id",
        )
        summary["person_case_rows"] += 1

    conn.commit()
    conn.close()
    summary["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return summary


def _parse_args():
    parser = argparse.ArgumentParser(
        description="Materialize Stage 1 knowledge-graph edge tables in SQLite (Phase 11)."
    )
    parser.add_argument(
        "--db",
        default="output/store.sqlite",
        help="SQLite DB path (default: output/store.sqlite).",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Clear existing cases/kg_edges/kg_edge_evidence before rebuilding.",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    summary = build_materialize_edges(args.db, reset=args.reset)
    print("Knowledge graph materialization summary")
    for key in (
        "cases_inserted_or_updated",
        "event_case_rows",
        "person_event_rows",
        "person_case_rows",
        "edges_inserted",
        "evidence_inserted",
        "elapsed_seconds",
    ):
        print(f"  {key}: {summary.get(key)}")


if __name__ == "__main__":
    main()

